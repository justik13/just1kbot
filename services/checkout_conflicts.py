"""Fail-closed classification of financial checkouts under the user checkout lock."""
from dataclasses import dataclass

from sqlalchemy import select

from database.models import Payment, PaymentFulfillmentOperation, PaymentProviderOperation, TariffQuote, TariffVersion

RUNNABLE = {"pending", "processing", "retry"}


@dataclass(frozen=True)
class FinancialCheckoutConflict:
    payment: Payment
    operation_type: str | None
    reason: str


async def get_unfinished_financial_checkouts(session, *, user_id: int,
                                             exclude_payment_id: int | None = None):
    """Return the first checkout lacking positive proof of harmless finality.

    Caller must already hold ``lock_checkout_user``. Quote status and
    ``checkout_status`` are intentionally irrelevant: neither prevents a late
    provider result or a queued fulfillment operation.
    """
    payments = list((await session.scalars(select(Payment).where(
        Payment.user_id == user_id,
        Payment.id != exclude_payment_id if exclude_payment_id is not None else True,
    ).order_by(Payment.id))).all())
    conflicts = []
    for payment in payments:
        quote_type = await session.scalar(select(TariffQuote.operation_type).where(
            TariffQuote.id == payment.tariff_quote_id)) if payment.tariff_quote_id else None
        provider_ops = list((await session.scalars(select(PaymentProviderOperation).where(
            PaymentProviderOperation.payment_id == payment.id))).all())
        fulfillment_ops = list((await session.scalars(select(PaymentFulfillmentOperation).where(
            PaymentFulfillmentOperation.payment_id == payment.id))).all())
        reason = None
        if any(op.status in RUNNABLE for op in provider_ops): reason = "provider_operation_unfinished"
        if any(op.status != "succeeded" for op in fulfillment_ops): reason = reason or "fulfillment_operation_retryable"
        if quote_type == "change" and payment.fulfillment_status not in {"succeeded", "reversed"}:
            reason = reason or "tariff_change_not_applied"
        if payment.provider_status in {"creating", "pending", "waiting_for_capture", "unknown", "manual_review"}:
            reason = reason or "provider_not_terminal"
        if payment.provider_status == "succeeded" and payment.fulfillment_status not in {"succeeded", "reversed"}:
            reason = reason or "fulfillment_not_terminal"
        if payment.provider_status == "refunded":
            reverse_succeeded = any(op.operation_type == "reverse_payment" and op.status == "succeeded" for op in fulfillment_ops)
            if payment.fulfillment_status != "reversed" or not reverse_succeeded:
                reason = reason or "refund_reversal_not_terminal"
        # Dead/cancelled commands may follow an ambiguous POST. Only an actual
        # provider terminal state proves that no future charge/grant is possible.
        if payment.provider_status not in {"canceled", "refunded", "succeeded"}:
            no_provider_was_possible = (
                payment.provider_status == "not_created" and payment.external_id is None
                and payment.provider_idempotency_key is None and not provider_ops
                and payment.checkout_status == "abandoned"
            )
            if not no_provider_was_possible: reason = reason or "terminality_unproven"
        if payment.provider_status == "canceled" and any(op.status != "succeeded" for op in fulfillment_ops):
            reason = reason or "canceled_has_retryable_fulfillment"
        if reason: conflicts.append(FinancialCheckoutConflict(payment, quote_type, reason))
    return conflicts


async def get_unfinished_financial_checkout(session, **kwargs):
    conflicts = await get_unfinished_financial_checkouts(session, **kwargs)
    return conflicts[0] if conflicts else None


async def is_valid_reusable_purchase_intent(session, conflict: FinancialCheckoutConflict,
                                            *, user_id: int, tariff_id: int) -> bool:
    """A matching tariff is insufficient; validate the entire frozen intent."""
    payment = conflict.payment
    if conflict.operation_type not in {"purchase", "renew"} or payment.user_id != user_id:
        return False
    quote = await session.get(TariffQuote, payment.tariff_quote_id) if payment.tariff_quote_id else None
    version = await session.get(TariffVersion, payment.tariff_version_id) if payment.tariff_version_id else None
    if not quote or not version or quote.payment_id != payment.id or quote.user_id != user_id:
        return False
    if (quote.operation_type != conflict.operation_type or quote.target_tariff_version_id != version.id or
            version.tariff_id != tariff_id or payment.tariff_id != tariff_id or
            payment.amount != quote.confirmed_payment_required_rub or payment.amount != version.price_rub or
            payment.snapshot_amount != payment.amount or payment.currency != quote.currency or
            payment.snapshot_currency != payment.currency or payment.currency != version.currency or
            payment.snapshot_duration_days != version.duration_hours // 24 or
            payment.snapshot_device_limit != version.device_limit or not payment.public_order_id or
            not payment.provider_idempotency_key):
        return False
    operations = list((await session.scalars(select(PaymentProviderOperation).where(
        PaymentProviderOperation.payment_id == payment.id,
        PaymentProviderOperation.operation_type == "create_payment"))).all())
    if len(operations) != 1 or operations[0].idempotency_key != payment.provider_idempotency_key:
        return False
    payload = operations[0].payload
    return (isinstance(payload, dict) and payload.get("amount") == {
        "value": format(payment.amount, ".2f"), "currency": payment.currency}
        and payload.get("capture") is True and payload.get("metadata") == {
            "order_id": payment.public_order_id, "local_payment_id": str(payment.id)})
