"""Provider snapshots are validated before any balance credit."""

from decimal import Decimal, InvalidOperation

from database.models import PaymentEvent


def validate_provider_payment(payment, data: dict) -> str | None:
    try:
        amount = data.get("amount") or {}
        metadata = data.get("metadata") or {}
        if payment.external_id and str(data.get("id")) != str(payment.external_id):
            return "external_id_mismatch"
        if Decimal(str(amount.get("value"))) != payment.amount:
            return "amount_mismatch"
        if str(amount.get("currency", "")).upper() != payment.currency:
            return "currency_mismatch"
        if metadata.get("order_id") != payment.public_order_id:
            return "order_id_mismatch"
        if str(metadata.get("local_payment_id") or "") != str(payment.id):
            return "local_payment_id_mismatch"
    except (InvalidOperation, ValueError, TypeError):
        return "invalid_provider_snapshot"
    return None


def record_mismatch(session, payment, reason):
    payment.reconciliation_status = "mismatch"
    payment.fulfillment_status = "manual_review"
    payment.manual_review_reason = reason
    session.add(
        PaymentEvent(
            payment_id=payment.id,
            event_type="provider_snapshot_mismatch",
            provider_status=payment.provider_status,
            reason=reason,
            source="provider_validation",
        )
    )
