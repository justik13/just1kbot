"""Provider snapshots are validated before any monetary transition."""
from decimal import Decimal, InvalidOperation
from database.models import PaymentEvent

def validate_provider_payment(payment, data: dict) -> str | None:
    try:
        amount=data.get("amount") or {}; metadata=data.get("metadata") or {}
        expected_amount=payment.snapshot_amount if payment.snapshot_amount is not None else payment.amount
        expected_currency=payment.snapshot_currency or payment.currency
        if payment.external_id and str(data.get("id"))!=str(payment.external_id): return "external_id_mismatch"
        if Decimal(str(amount.get("value")))!=expected_amount: return "amount_mismatch"
        if str(amount.get("currency","")).upper()!=str(expected_currency).upper(): return "currency_mismatch"
        if metadata.get("order_id")!=payment.public_order_id: return "order_id_mismatch"
        local_id=metadata.get("local_payment_id")
        if local_id is None: return "local_payment_id_missing"
        if str(local_id)!=str(payment.id): return "local_payment_id_mismatch"
    except (InvalidOperation,ValueError,TypeError): return "invalid_provider_snapshot"
    return None

def record_mismatch(session,payment,reason):
    payment.reconciliation_status="mismatch"; payment.fulfillment_status="manual_review"
    session.add(PaymentEvent(payment_id=payment.id,event_type="provider_snapshot_mismatch",provider_status="manual_review",reason=reason,source="provider_validation"))
