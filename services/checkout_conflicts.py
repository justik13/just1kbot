"""Fail-closed classification of financial checkouts under the user checkout lock."""
from dataclasses import dataclass

from sqlalchemy import select

from database.models import Payment, PaymentFulfillmentOperation, PaymentProviderOperation, TariffQuote

RUNNABLE = {"pending", "processing", "retry"}


@dataclass(frozen=True)
class FinancialCheckoutConflict:
    payment: Payment
    operation_type: str | None
    reason: str


async def get_unfinished_financial_checkout(session, *, user_id: int,
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
    for payment in payments:
        quote_type = await session.scalar(select(TariffQuote.operation_type).where(
            TariffQuote.id == payment.tariff_quote_id)) if payment.tariff_quote_id else None
        provider_ops = list((await session.scalars(select(PaymentProviderOperation).where(
            PaymentProviderOperation.payment_id == payment.id))).all())
        fulfillment_ops = list((await session.scalars(select(PaymentFulfillmentOperation).where(
            PaymentFulfillmentOperation.payment_id == payment.id))).all())
        if any(op.status in RUNNABLE for op in provider_ops):
            return FinancialCheckoutConflict(payment, quote_type, "provider_operation_unfinished")
        if any(op.status in RUNNABLE for op in fulfillment_ops):
            return FinancialCheckoutConflict(payment, quote_type, "fulfillment_operation_unfinished")
        if quote_type == "change" and payment.fulfillment_status not in {"succeeded", "reversed"}:
            return FinancialCheckoutConflict(payment, quote_type, "tariff_change_not_applied")
        if payment.provider_status in {"creating", "pending", "waiting_for_capture", "unknown", "manual_review"}:
            return FinancialCheckoutConflict(payment, quote_type, "provider_not_terminal")
        if payment.provider_status == "succeeded" and payment.fulfillment_status not in {"succeeded", "reversed"}:
            return FinancialCheckoutConflict(payment, quote_type, "fulfillment_not_terminal")
        # Dead/cancelled commands may follow an ambiguous POST. Only an actual
        # provider terminal state proves that no future charge/grant is possible.
        if payment.provider_status not in {"canceled", "refunded", "succeeded"}:
            no_provider_was_possible = (
                payment.provider_status == "not_created" and payment.external_id is None
                and payment.provider_idempotency_key is None and not provider_ops
                and payment.checkout_status == "abandoned"
            )
            if not no_provider_was_possible:
                return FinancialCheckoutConflict(payment, quote_type, "terminality_unproven")
    return None
