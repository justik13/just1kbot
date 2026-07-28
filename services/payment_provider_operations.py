import uuid
from datetime import timedelta
from sqlalchemy import select
from database.models import Payment, PaymentProviderOperation
from services.payment_lifecycle import project_legacy_status
from services.yookassa_service import YooKassaService
from utils.datetime_helpers import now_utc

def create_payload(payment, description, return_url):
    return {"amount":{"value":format(payment.amount,'.2f'),"currency":payment.currency},"description":description,"return_url":return_url,"confirmation":{"type":"redirect","return_url":return_url},"metadata":{"order_id":payment.public_order_id,"local_payment_id":str(payment.id)},"capture":True}
async def enqueue_create(session,payment,description,return_url):
    op=PaymentProviderOperation(payment_id=payment.id,operation_type="create_payment",status="pending",idempotency_key=payment.provider_idempotency_key,payload=create_payload(payment,description,return_url),next_attempt_at=now_utc())
    session.add(op); await session.flush(); return op
async def claim(session, worker_id):
    stmt=(select(PaymentProviderOperation).where(PaymentProviderOperation.status.in_(("pending","retry")),PaymentProviderOperation.next_attempt_at<=now_utc()).order_by(PaymentProviderOperation.id).with_for_update(skip_locked=True).limit(1))
    op=await session.scalar(stmt)
    if op: op.status="processing"; op.locked_by=worker_id; op.locked_at=now_utc(); op.attempts+=1; await session.flush()
    return op
async def execute(session,op,transport=YooKassaService):
    payment=await session.scalar(select(Payment).where(Payment.id==op.payment_id).with_for_update())
    result=await (transport.get_payment_result(payment.external_id) if payment.external_id else transport.create_payment_result(op.payload,idempotency_key=op.idempotency_key))
    if result.ok:
        data=result.value or {}; provider_id=data.get("id"); confirmation=data.get("confirmation") or {}; url=confirmation.get("confirmation_url") or confirmation.get("url")
        if not provider_id or (op.operation_type=="create_payment" and not url): result=type(result)(False,error_kind=getattr(__import__('services.yookassa_service',fromlist=['YooKassaErrorKind']),'YooKassaErrorKind').INVALID_RESPONSE,retryable=True,ambiguous=True)
        else:
            payment.external_id=provider_id; payment.payment_url=url or payment.payment_url; payment.payment_method="yookassa"; payment.provider_status=data.get("status","pending"); payment.reconciliation_status="ok"; op.status="succeeded"; op.completed_at=now_utc(); op.locked_at=op.locked_by=None; project_legacy_status(payment); await session.flush(); return result
    payment.provider_last_error_code=result.error_kind.value if result.error_kind else "unknown"; payment.provider_status="unknown" if result.ambiguous else "manual_review"; payment.reconciliation_status="required" if result.retryable else "manual_review"
    op.last_error_code=payment.provider_last_error_code; op.status="retry" if result.retryable else "dead"; op.next_attempt_at=now_utc()+timedelta(seconds=min(300,2**min(op.attempts,8))); op.locked_at=op.locked_by=None; project_legacy_status(payment); await session.flush(); return result
async def recover_stale(session,lease_seconds=120):
    cutoff=now_utc()-timedelta(seconds=lease_seconds)
    rows=(await session.scalars(select(PaymentProviderOperation).where(PaymentProviderOperation.status=="processing",PaymentProviderOperation.locked_at<cutoff).with_for_update(skip_locked=True))).all()
    for op in rows: op.status="retry"; op.locked_at=op.locked_by=None; op.next_attempt_at=now_utc()
    return len(rows)
