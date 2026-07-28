"""Durable YooKassa webhook consumer."""
import uuid
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import select
from database.models import Payment, PaymentFulfillmentOperation, WebhookInbox
from services.payment_lifecycle import project_legacy_status
from services.yookassa_service import YooKassaService
from utils.datetime_helpers import now_utc

async def claim(session,worker_id=None):
    worker_id=worker_id or uuid.uuid4().hex
    row=await session.scalar(select(WebhookInbox).where(WebhookInbox.status.in_(("pending","retry")),WebhookInbox.next_attempt_at<=now_utc()).order_by(WebhookInbox.id).with_for_update(skip_locked=True).limit(1))
    if row: row.status="processing"; row.locked_by=worker_id; row.locked_at=now_utc(); row.attempts+=1; await session.flush()
    return row
async def _retry(row,code):
    row.status="dead" if row.attempts>=row.max_attempts else "retry"; row.last_error_code=code; row.locked_at=row.locked_by=None; row.next_attempt_at=now_utc()+timedelta(seconds=min(60,2**min(row.attempts,6)))
def _ensure(session,payment,typ,key):
    session.add(PaymentFulfillmentOperation(payment_id=payment.id,operation_type=typ,idempotency_key=key,status="pending",payload={},next_attempt_at=now_utc()))
async def process(session,row,transport=YooKassaService):
    payment=await session.scalar(select(Payment).where(Payment.external_id==row.payment_external_id).with_for_update())
    if not payment and row.public_order_id:
        payment=await session.scalar(select(Payment).where(Payment.public_order_id==row.public_order_id).with_for_update())
        if payment:
            conflict=await session.scalar(select(Payment.id).where(Payment.external_id==row.payment_external_id,Payment.id!=payment.id))
            if conflict: payment.reconciliation_status="mismatch"; payment.provider_status=payment.fulfillment_status="manual_review"; await _retry(row,"external_id_conflict"); return
            payment.external_id=row.payment_external_id
    if not payment: await _retry(row,"payment_not_visible"); return
    result=await transport.get_payment_result(row.payment_external_id)
    if not result.ok: await _retry(row,result.error_kind.value if result.error_kind else "provider_error"); return
    data=result.value or {}; status=data.get("status"); amount=data.get("amount") or {}; metadata=data.get("metadata") or {}
    money_confirmed=status in {"succeeded","refunded"}
    mismatch=(str(data.get("id"))!=str(row.payment_external_id) or Decimal(str(amount.get("value","-1")))!=payment.amount or amount.get("currency")!=payment.currency or metadata.get("order_id")!=payment.public_order_id)
    if money_confirmed and not payment.paid_at: payment.paid_at=payment.provider_confirmed_at=now_utc()
    prior=payment.provider_status
    if mismatch:
        payment.reconciliation_status="mismatch"; payment.provider_status=payment.fulfillment_status="manual_review"
    elif status=="succeeded":
        if prior=="canceled": payment.reconciliation_status="mismatch"; payment.provider_status="succeeded"; payment.fulfillment_status="manual_review"
        else:
            payment.provider_status="succeeded"; payment.fulfillment_status="pending" if payment.fulfillment_status=="not_ready" else payment.fulfillment_status
            _ensure(session,payment,"grant_subscription",f"payment-grant:{payment.id}")
    elif status=="canceled":
        if prior=="succeeded" or payment.paid_at: payment.reconciliation_status="mismatch"; payment.fulfillment_status="manual_review"
        else: payment.provider_status="canceled"
    elif status=="refunded" or row.event_type in {"refund.succeeded","payment.refunded"}:
        payment.provider_status="refunded"; payment.fulfillment_status="reversal_pending"; _ensure(session,payment,"reverse_payment",f"payment-reverse:{payment.id}")
    else: payment.provider_status=status if status in {"pending"} else "unknown"
    project_legacy_status(payment); row.status="succeeded"; row.processed_at=now_utc(); row.locked_at=row.locked_by=None
    try: await session.flush()
    except Exception as exc:
        if "idempotency" in str(exc).lower(): await session.rollback()
        else: raise
async def recover_stale(session,lease_seconds=120):
    rows=(await session.scalars(select(WebhookInbox).where(WebhookInbox.status=="processing",WebhookInbox.locked_at<now_utc()-timedelta(seconds=lease_seconds)).with_for_update(skip_locked=True))).all()
    for row in rows: row.status="retry"; row.locked_at=row.locked_by=None; row.next_attempt_at=now_utc()
    return len(rows)
