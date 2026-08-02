"""The single monotonic provider-state transition boundary.

Lock order for payment pipeline transactions is always Payment, then queue row,
then User/entitlement rows. Callers that initially claim a queue row must release
that claim transaction before entering this transition/finalization transaction.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from sqlalchemy import select
from database.models import PaymentEvent, PaymentFulfillmentOperation, TariffQuote
from services.payment_provider_validation import validate_provider_payment
from utils.datetime_helpers import now_utc


@dataclass(frozen=True)
class ProviderTransition:
    outcome: str  # applied, conflict, retry
    observed_status: str
    grant_allowed: bool = False
    reason: str | None = None


def parse_provider_captured_at(value) -> datetime:
    """Parse a provider ISO-8601 instant without assuming a missing timezone."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("captured_at_missing")
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError("captured_at_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("captured_at_timezone_missing")
    return parsed.astimezone(timezone.utc)


async def apply_provider_transition(session, payment, data, *, source, event_type=None):
    observed = str((data or {}).get("status") or "unknown")
    current = payment.provider_status
    if observed not in {"pending", "waiting_for_capture", "succeeded", "canceled"}:
        return ProviderTransition("retry", observed, reason="unknown_provider_status")
    if observed == "succeeded":
        # A successful provider snapshot is authoritative financial evidence.  Record
        # it before identity validation so a bad command correlation cannot erase the
        # fact that money was received.
        requires_verified_capture = bool(
            payment.tariff_quote_id or payment.payment_kind == "balance_topup"
        )
        if requires_verified_capture and source == "provider_create_payment_post":
            return ProviderTransition(
                "retry", observed, reason="captured_at_requires_verified_get"
            )
        payment.paid_at = payment.paid_at or now_utc()
        if requires_verified_capture:
            try:
                captured_at = parse_provider_captured_at(data.get("captured_at"))
            except ValueError as exc:
                payment.provider_status = "succeeded"
                payment.reconciliation_status = "manual_review"
                payment.fulfillment_status = "manual_review"
                payment.fulfillment_last_error_code = str(exc)
                quote = (
                    await session.scalar(
                        select(TariffQuote)
                        .where(TariffQuote.id == payment.tariff_quote_id)
                        .with_for_update()
                    )
                    if payment.tariff_quote_id
                    else None
                )
                if quote:
                    quote.status = "manual_review"
                    quote.manual_review_at = quote.manual_review_at or now_utc()
                    quote.diagnostic_reason = str(exc)
                session.add(
                    PaymentEvent(
                        payment_id=payment.id,
                        event_type="provider_captured_at_invalid",
                        provider_status=observed,
                        reason=str(exc),
                        source=source,
                    )
                )
                return ProviderTransition("conflict", observed, reason=str(exc))
            if (
                payment.provider_confirmed_at
                and payment.provider_confirmed_at != captured_at
            ):
                payment.provider_status = "succeeded"
                payment.reconciliation_status = "manual_review"
                payment.fulfillment_status = "manual_review"
                payment.fulfillment_last_error_code = "captured_at_changed"
                quote = (
                    await session.scalar(
                        select(TariffQuote)
                        .where(TariffQuote.id == payment.tariff_quote_id)
                        .with_for_update()
                    )
                    if payment.tariff_quote_id
                    else None
                )
                if quote:
                    quote.status = "manual_review"
                    quote.manual_review_at = quote.manual_review_at or now_utc()
                    quote.diagnostic_reason = "captured_at_changed"
                session.add(
                    PaymentEvent(
                        payment_id=payment.id,
                        event_type="provider_captured_at_conflict",
                        provider_status=observed,
                        reason="captured_at_changed",
                        source=source,
                    )
                )
                return ProviderTransition(
                    "conflict", observed, reason="captured_at_changed"
                )
            payment.provider_confirmed_at = captured_at
        else:
            payment.provider_confirmed_at = payment.provider_confirmed_at or now_utc()
        terminal_reversal = (
            current == "refunded" or payment.fulfillment_status == "reversed"
        )
        if not terminal_reversal:
            payment.provider_status = "succeeded"
        mismatch = validate_provider_payment(payment, data)
        if mismatch:
            payment.reconciliation_status = "mismatch"
            if not terminal_reversal:
                payment.fulfillment_status = "manual_review"
            queued = (
                await session.scalars(
                    select(PaymentFulfillmentOperation)
                    .where(
                        PaymentFulfillmentOperation.payment_id == payment.id,
                        PaymentFulfillmentOperation.operation_type.in_(
                            ("grant_subscription", "grant_referral")
                        ),
                        PaymentFulfillmentOperation.status.in_(("pending", "retry")),
                    )
                    .with_for_update()
                )
            ).all()
            for operation in queued:
                operation.status = "cancelled"
                operation.completed_at = now_utc()
            session.add(
                PaymentEvent(
                    payment_id=payment.id,
                    event_type="provider_snapshot_mismatch",
                    provider_status=observed,
                    reason=mismatch,
                    source=source,
                )
            )
            return ProviderTransition("conflict", observed, reason=mismatch)
        if terminal_reversal:
            payment.reconciliation_status = "mismatch"
            session.add(
                PaymentEvent(
                    payment_id=payment.id,
                    event_type="provider_transition_conflict",
                    provider_status=observed,
                    reason=f"{current}_to_succeeded",
                    source=source,
                )
            )
            return ProviderTransition(
                "conflict", observed, reason="succeeded_after_refund"
            )
        if (
            current == "canceled"
            or payment.checkout_status == "abandoned"
            or source == "provider_cancel_payment"
        ):
            payment.reconciliation_status = "mismatch"
            payment.fulfillment_status = "manual_review"
            session.add(
                PaymentEvent(
                    payment_id=payment.id,
                    event_type="paid_after_cancel",
                    provider_status=observed,
                    reason=f"{current}_to_succeeded",
                    source=source,
                )
            )
            return ProviderTransition("conflict", observed, reason="paid_after_cancel")
        return ProviderTransition(
            "applied",
            observed,
            grant_allowed=payment.fulfillment_status
            not in {"succeeded", "reversed", "manual_review"},
        )
    if current in {"succeeded", "refunded"} or payment.fulfillment_status == "reversed":
        if observed != current:
            payment.reconciliation_status = "mismatch"
            payment.fulfillment_status = (
                "manual_review"
                if payment.fulfillment_status != "reversed"
                else payment.fulfillment_status
            )
            session.add(
                PaymentEvent(
                    payment_id=payment.id,
                    event_type="provider_transition_conflict",
                    provider_status=observed,
                    reason=f"{current}_to_{observed}",
                    source=source,
                )
            )
            return ProviderTransition(
                "conflict", observed, reason="terminal_regression"
            )
        return ProviderTransition("applied", observed)
    if current == "canceled" and observed in {"pending", "waiting_for_capture"}:
        payment.reconciliation_status = "mismatch"
        session.add(
            PaymentEvent(
                payment_id=payment.id,
                event_type="provider_transition_conflict",
                provider_status=observed,
                reason=f"canceled_to_{observed}",
                source=source,
            )
        )
        return ProviderTransition("conflict", observed, reason="terminal_regression")
    payment.provider_status = observed
    if observed == "canceled":
        payment.checkout_status = "abandoned"
        payment.payment_url = None
        if payment.payment_kind == "balance_topup":
            payment.ui_visible = False
        if payment.tariff_quote_id:
            quote = await session.scalar(
                select(TariffQuote)
                .where(TariffQuote.id == payment.tariff_quote_id)
                .with_for_update()
            )
            if quote and quote.status == "active":
                quote.status = "cancelled"
                quote.diagnostic_reason = "provider_canceled"
    return ProviderTransition("applied", observed)
