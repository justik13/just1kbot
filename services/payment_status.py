"""Read-only display classification derived from durable payment state."""

from config.enums import (
    PaymentFulfillmentStatus,
    PaymentProviderStatus,
    PaymentReconciliationStatus,
)


def payment_display_status(payment) -> str:
    if (
        payment.reconciliation_status in {PaymentReconciliationStatus.MISMATCH, PaymentReconciliationStatus.MANUAL_REVIEW}
        or payment.provider_status == PaymentProviderStatus.MANUAL_REVIEW
        or payment.fulfillment_status == PaymentFulfillmentStatus.MANUAL_REVIEW
    ):
        return "requires_manual_review"
    if (
        payment.provider_status == PaymentProviderStatus.REFUNDED
        or payment.fulfillment_status == PaymentFulfillmentStatus.REVERSED
    ):
        return "refunded"
    if payment.provider_status == PaymentProviderStatus.CANCELED:
        return "cancelled"
    if (
        payment.provider_status == PaymentProviderStatus.SUCCEEDED
        and payment.fulfillment_status == PaymentFulfillmentStatus.SUCCEEDED
    ):
        return "completed"
    if payment.provider_status == PaymentProviderStatus.SUCCEEDED:
        return "paid_processing"
    if payment.provider_status in {
        PaymentProviderStatus.NOT_CREATED,
        PaymentProviderStatus.CREATING,
        PaymentProviderStatus.PENDING,
        PaymentProviderStatus.WAITING_FOR_CAPTURE,
        PaymentProviderStatus.UNKNOWN,
    }:
        return "pending"
    return "failed"
