"""Fail-closed classification of financial checkouts under the user checkout lock."""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from utils.datetime_helpers import now_utc

from database.models import (
    EntitlementEntry,
    PaidValueLedgerEntry,
    Payment,
    PaymentFulfillmentOperation,
    PaymentProviderOperation,
    PaymentRefund,
    TariffQuote,
    TariffVersion,
)

RUNNABLE = {"pending", "processing", "retry"}


@dataclass(frozen=True)
class FinancialCheckoutConflict:
    payment: Payment
    operation_type: str | None
    reason: str


async def get_unfinished_financial_checkouts(
    session, *, user_id: int, exclude_payment_id: int | None = None
):
    """Return the first checkout lacking positive proof of harmless finality.

    Caller must already hold ``lock_checkout_user``. Quote status and
    ``checkout_status`` are intentionally irrelevant: neither prevents a late
    provider result or a queued fulfillment operation.
    """
    payments = list(
        (
            await session.scalars(
                select(Payment)
                .where(
                    Payment.user_id == user_id,
                    Payment.id != exclude_payment_id
                    if exclude_payment_id is not None
                    else True,
                )
                .order_by(Payment.id)
            )
        ).all()
    )
    conflicts = []
    for payment in payments:
        quote = (
            await session.get(TariffQuote, payment.tariff_quote_id)
            if payment.tariff_quote_id
            else None
        )
        quote_type = quote.operation_type if quote else None
        provider_ops = list(
            (
                await session.scalars(
                    select(PaymentProviderOperation).where(
                        PaymentProviderOperation.payment_id == payment.id
                    )
                )
            ).all()
        )
        fulfillment_ops = list(
            (
                await session.scalars(
                    select(PaymentFulfillmentOperation).where(
                        PaymentFulfillmentOperation.payment_id == payment.id
                    )
                )
            ).all()
        )
        reason = None
        # Strongest local proof: no HTTP could have happened and no command can
        # ever mutate provider or User state. A zero change intent may use this
        # path after explicit local abandonment.
        harmless_create = (not payment.provider_required and not provider_ops) or (
            payment.provider_required
            and len(provider_ops) == 1
            and provider_ops[0].operation_type == "create_payment"
            and provider_ops[0].status == "cancelled"
            and provider_ops[0].attempts == 0
        )
        locally_abandoned = (
            payment.checkout_status == "abandoned"
            and payment.provider_status == "not_created"
            and payment.external_id is None
            and payment.payment_url is None
            and payment.paid_at is None
            and payment.provider_confirmed_at is None
            and not fulfillment_ops
            and harmless_create
        )
        if locally_abandoned and payment.reconciliation_status == "ok":
            continue
        if payment.reconciliation_status in {"required", "mismatch", "manual_review"}:
            reason = "reconciliation_not_terminal"
        if any(op.status in RUNNABLE for op in provider_ops):
            reason = "provider_operation_unfinished"
        if any(op.status != "succeeded" for op in fulfillment_ops):
            reason = reason or "fulfillment_operation_retryable"
        if quote_type == "change" and payment.fulfillment_status not in {
            "succeeded",
            "reversed",
        }:
            reason = reason or "tariff_change_not_applied"
        if payment.provider_status in {
            "creating",
            "pending",
            "waiting_for_capture",
            "unknown",
            "manual_review",
        }:
            reason = reason or "provider_not_terminal"
        if (
            payment.provider_status == "succeeded"
            and payment.fulfillment_status not in {"succeeded", "reversed"}
        ):
            reason = reason or "fulfillment_not_terminal"
        if (
            payment.provider_status in {"refunded", "succeeded"}
            and payment.fulfillment_status == "reversed"
        ):
            reverse_succeeded = any(
                op.operation_type == "reverse_payment" and op.status == "succeeded"
                for op in fulfillment_ops
            )
            grant = await session.scalar(
                select(EntitlementEntry.id).where(
                    EntitlementEntry.source_type == "payment",
                    EntitlementEntry.source_id == str(payment.id),
                    EntitlementEntry.entry_type.in_(("payment_grant", "manual_grant")),
                )
            )
            entitlement_reversal = await session.scalar(
                select(EntitlementEntry.id).where(
                    EntitlementEntry.source_type == "payment",
                    EntitlementEntry.source_id == str(payment.id),
                    EntitlementEntry.entry_type == "payment_reversal",
                )
            )
            paid = await session.scalar(
                select(PaidValueLedgerEntry.id).where(
                    PaidValueLedgerEntry.payment_id == payment.id,
                    PaidValueLedgerEntry.entry_type == "confirmed_payment",
                )
            )
            paid_reversal = (
                await session.scalar(
                    select(PaidValueLedgerEntry.id).where(
                        PaidValueLedgerEntry.reversal_of_id == paid,
                        PaidValueLedgerEntry.entry_type == "payment_reversal",
                    )
                )
                if paid
                else None
            )
            if (
                not reverse_succeeded
                or (grant is not None and entitlement_reversal is None)
                or (paid is not None and paid_reversal is None)
            ):
                reason = reason or "refund_reversal_not_terminal"
        elif payment.provider_status == "refunded":
            reason = reason or "refund_reversal_not_terminal"
        if payment.provider_status == "refunded":
            refunded_total = await session.scalar(
                select(func.coalesce(func.sum(PaymentRefund.amount), 0)).where(
                    PaymentRefund.payment_id == payment.id,
                    PaymentRefund.provider_status == "succeeded",
                )
            )
            if refunded_total != payment.amount:
                reason = reason or "refund_total_incomplete"
        # Dead/cancelled commands may follow an ambiguous POST. Only an actual
        # provider terminal state proves that no future charge/grant is possible.
        if payment.provider_status not in {"canceled", "refunded", "succeeded"}:
            no_provider_was_possible = (
                payment.provider_status == "not_created"
                and payment.external_id is None
                and payment.provider_idempotency_key is None
                and not provider_ops
                and payment.checkout_status == "abandoned"
            )
            if not no_provider_was_possible:
                reason = reason or "terminality_unproven"
        if payment.provider_status == "canceled" and (
            fulfillment_ops
            or payment.checkout_status != "abandoned"
            or (quote is not None and quote.status not in {"cancelled", "expired"})
        ):
            reason = reason or "canceled_not_closed"
        if reason:
            conflicts.append(FinancialCheckoutConflict(payment, quote_type, reason))
    return conflicts


async def get_unfinished_financial_checkout(session, **kwargs):
    conflicts = await get_unfinished_financial_checkouts(session, **kwargs)
    return conflicts[0] if conflicts else None


async def is_valid_reusable_purchase_intent(
    session, conflict: FinancialCheckoutConflict, *, user_id: int, tariff_id: int
) -> bool:
    """Validate an in-progress or provider-created purchase intent for reuse.

    A ready provider payment remains the only safe checkout to show again: creating
    a second payment would leave two independently payable links.  The narrowly
    defined abandoned state below exists only to recover rows produced by the old
    misleading user-cancel button.
    """
    payment = conflict.payment
    if (
        conflict.operation_type not in {"purchase", "renew"}
        or payment.user_id != user_id
    ):
        return False
    quote = (
        await session.get(TariffQuote, payment.tariff_quote_id)
        if payment.tariff_quote_id
        else None
    )
    version = (
        await session.get(TariffVersion, payment.tariff_version_id)
        if payment.tariff_version_id
        else None
    )
    if (
        not quote
        or not version
        or quote.payment_id != payment.id
        or quote.user_id != user_id
    ):
        return False
    if (
        quote.operation_type != conflict.operation_type
        or quote.target_tariff_version_id != version.id
        or version.tariff_id != tariff_id
        or payment.tariff_id != tariff_id
        or payment.amount != quote.confirmed_payment_required_rub
        or payment.amount != version.price_rub
        or payment.snapshot_amount != payment.amount
        or payment.currency != quote.currency
        or payment.snapshot_currency != payment.currency
        or payment.currency != version.currency
        or payment.snapshot_duration_days != version.duration_hours // 24
        or payment.snapshot_device_limit != version.device_limit
        or not payment.public_order_id
        or not payment.provider_idempotency_key
        or not payment.provider_required
        or payment.fulfillment_status != "not_ready"
        or payment.reconciliation_status in {"mismatch", "manual_review"}
        or quote.manual_review_at is not None
    ):
        return False
    operations = list(
        (
            await session.scalars(
                select(PaymentProviderOperation).where(
                    PaymentProviderOperation.payment_id == payment.id,
                    PaymentProviderOperation.operation_type == "create_payment",
                )
            )
        ).all()
    )
    if (
        len(operations) != 1
        or operations[0].idempotency_key != payment.provider_idempotency_key
    ):
        return False
    operation = operations[0]
    payload = operation.payload
    payload_valid = (
        isinstance(payload, dict)
        and set(payload)
        == {"amount", "description", "confirmation", "metadata", "capture"}
        and payload.get("amount")
        == {"value": format(payment.amount, ".2f"), "currency": payment.currency}
        and payload.get("capture") is True
        and payload.get("metadata")
        == {"order_id": payment.public_order_id, "local_payment_id": str(payment.id)}
        and isinstance(payload.get("description"), str)
        and bool(payload["description"])
        and isinstance(payload.get("confirmation"), dict)
        and set(payload["confirmation"]) == {"type", "return_url"}
        and payload["confirmation"].get("type") == "redirect"
        and bool(payload["confirmation"].get("return_url"))
    )
    if not payload_valid:
        return False

    operation_available = operation.status == "succeeded" or (
        operation.status in RUNNABLE
        and operation.attempts < operation.max_attempts
        and now_utc() - operation.created_at < timedelta(hours=24)
    )
    if not operation_available:
        return False

    creating_intent = (
        quote.status == "active"
        and quote.diagnostic_reason is None
        and quote.expires_at > now_utc()
        and payment.checkout_status == "active"
        and payment.provider_status == "creating"
        and operation.status in RUNNABLE
        and payment.external_id is None
        and payment.payment_url is None
    )
    ready_provider_payment = (
        quote.status == "active"
        and quote.diagnostic_reason is None
        and quote.expires_at > now_utc()
        and payment.checkout_status == "active"
        and payment.provider_status in {"pending", "waiting_for_capture"}
        and bool(payment.external_id)
        and bool(payment.payment_url)
    )
    recoverable_old_user_cancel = (
        quote.status == "cancelled"
        and quote.diagnostic_reason == "checkout_abandoned_by_user"
        and quote.expires_at > now_utc()
        and payment.checkout_status == "abandoned"
        and payment.user_cancel_requested_at is not None
        and payment.provider_status in {"pending", "waiting_for_capture"}
        and bool(payment.external_id)
    )
    return creating_intent or ready_provider_payment or recoverable_old_user_cancel
