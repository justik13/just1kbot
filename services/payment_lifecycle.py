"""Single compatibility projection from durable payment state to legacy UI state."""


def project_legacy_status(payment) -> str:
    if (
        payment.reconciliation_status in {"mismatch", "manual_review"}
        or payment.provider_status == "manual_review"
        or payment.fulfillment_status == "manual_review"
    ):
        value = "requires_manual_review"
    elif payment.provider_status == "canceled":
        value = "cancelled"
    elif (
        payment.provider_status == "refunded"
        and payment.fulfillment_status == "reversed"
    ):
        value = "refunded"
    elif (
        payment.provider_status == "succeeded"
        and payment.fulfillment_status == "succeeded"
    ):
        value = "completed"
    elif payment.provider_status == "succeeded":
        value = "paid_processing"
    elif payment.provider_status in {"pending", "creating", "unknown"}:
        value = "pending"
    else:
        value = "failed"
    payment.status = value
    return value
