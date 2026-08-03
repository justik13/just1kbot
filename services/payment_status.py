"""Read-only display classification derived from durable payment state."""


def payment_display_status(payment) -> str:
    if (
        payment.reconciliation_status in {"mismatch", "manual_review"}
        or payment.provider_status == "manual_review"
        or payment.fulfillment_status == "manual_review"
    ):
        return "requires_manual_review"
    if payment.provider_status == "refunded" or payment.fulfillment_status == "reversed":
        return "refunded"
    if payment.provider_status == "canceled":
        return "cancelled"
    if payment.provider_status == "succeeded" and payment.fulfillment_status == "succeeded":
        return "completed"
    if payment.provider_status == "succeeded":
        return "paid_processing"
    if payment.provider_status in {
        "not_created",
        "creating",
        "pending",
        "waiting_for_capture",
        "unknown",
    }:
        return "pending"
    return "failed"
