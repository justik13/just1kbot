"""Monotonic provider-state transitions for YooKassa balance top-ups."""

from dataclasses import dataclass
from datetime import datetime, timezone

from database.models import PaymentEvent
from services.payment_provider_validation import validate_provider_payment
from utils.datetime_helpers import now_utc


@dataclass(frozen=True)
class ProviderTransition:
    outcome: str  # applied, conflict, retry
    observed_status: str
    reason: str | None = None


def parse_provider_captured_at(value) -> datetime:
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


def _manual_review(session, payment, reason: str, source: str, observed: str):
    payment.reconciliation_status = "manual_review"
    payment.fulfillment_status = "manual_review"
    payment.manual_review_reason = reason
    payment.fulfillment_last_error_code = reason
    session.add(
        PaymentEvent(
            payment_id=payment.id,
            event_type="provider_transition_manual_review",
            provider_status=observed,
            reason=reason,
            source=source,
        )
    )


async def apply_provider_transition(session, payment, data, *, source, event_type=None):
    observed = str((data or {}).get("status") or "unknown")
    current = payment.provider_status
    if observed not in {"pending", "waiting_for_capture", "succeeded", "canceled"}:
        return ProviderTransition("retry", observed, "unknown_provider_status")

    if observed == "succeeded":
        if source == "provider_create_payment_post":
            return ProviderTransition("retry", observed, "captured_at_requires_verified_get")
        try:
            captured_at = parse_provider_captured_at(data.get("captured_at"))
        except ValueError as exc:
            # Never synthesize a provider confirmation timestamp. A successful
            # payment without a valid provider timestamp is not safe to credit;
            # retry after a verified GET instead.
            return ProviderTransition("retry", observed, str(exc))
        if payment.provider_confirmed_at and payment.provider_confirmed_at != captured_at:
            payment.provider_status = "succeeded"
            payment.paid_at = payment.paid_at or now_utc()
            _manual_review(session, payment, "captured_at_changed", source, observed)
            return ProviderTransition("conflict", observed, "captured_at_changed")
        payment.provider_confirmed_at = captured_at
        payment.paid_at = payment.paid_at or captured_at
        mismatch = validate_provider_payment(payment, data)
        if mismatch:
            payment.provider_status = "succeeded"
            payment.reconciliation_status = "mismatch"
            payment.fulfillment_status = "manual_review"
            payment.manual_review_reason = mismatch
            session.add(
                PaymentEvent(
                    payment_id=payment.id,
                    event_type="provider_snapshot_mismatch",
                    provider_status=observed,
                    reason=mismatch,
                    source=source,
                )
            )
            return ProviderTransition("conflict", observed, mismatch)
        if current == "refunded" or payment.fulfillment_status == "reversed":
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
            return ProviderTransition("conflict", observed, "succeeded_after_refund")
        if (
            payment.reconciliation_status in ("manual_review", "mismatch")
            or payment.fulfillment_status == "manual_review"
        ):
            payment.provider_status = "succeeded"
            return ProviderTransition("conflict", observed, "manual_review_locked")
        payment.provider_status = "succeeded"
        if current == "canceled":
            payment.reconciliation_status = "mismatch"
            payment.fulfillment_status = "manual_review"
            payment.manual_review_reason = "canceled_to_succeeded"
            session.add(
                PaymentEvent(
                    payment_id=payment.id,
                    event_type="provider_transition_conflict",
                    provider_status=observed,
                    reason="canceled_to_succeeded",
                    source=source,
                )
            )
            return ProviderTransition("conflict", observed, "canceled_to_succeeded")
        elif payment.checkout_status == "abandoned":
            payment.reconciliation_status = "mismatch"
            session.add(
                PaymentEvent(
                    payment_id=payment.id,
                    event_type="paid_after_checkout_closed",
                    provider_status=observed,
                    reason="late_success_after_hidden_checkout",
                    source=source,
                )
            )
        return ProviderTransition("applied", observed)

    if current in {"succeeded", "refunded"} or payment.fulfillment_status == "reversed":
        if observed != current:
            payment.reconciliation_status = "mismatch"
            if payment.fulfillment_status != "reversed":
                payment.fulfillment_status = "manual_review"
            session.add(
                PaymentEvent(
                    payment_id=payment.id,
                    event_type="provider_transition_conflict",
                    provider_status=observed,
                    reason=f"{current}_to_{observed}",
                    source=source,
                )
            )
            return ProviderTransition("conflict", observed, "terminal_regression")
        return ProviderTransition("applied", observed)

    if current == "canceled" and observed != "canceled":
        payment.reconciliation_status = "mismatch"
        return ProviderTransition("conflict", observed, "terminal_regression")

    payment.provider_status = observed
    if observed == "canceled":
        payment.checkout_status = "abandoned"
        payment.ui_visible = False
        payment.payment_url = None
    return ProviderTransition("applied", observed)
