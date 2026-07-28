"""Fenced durable YooKassa webhook consumer."""
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from database.models import Payment, PaymentFulfillmentOperation, PaymentRefund, WebhookInbox
from services.payment_lifecycle import project_legacy_status
from services.payment_provider_validation import record_mismatch, validate_provider_payment
from database.models import PaymentEvent
from services.yookassa_service import YooKassaService
from utils.datetime_helpers import now_utc
class WebhookInboxOwnershipError(RuntimeError): pass
@dataclass(frozen=True)
class InboxClaim: inbox_id:int; worker_id:str; attempt_number:int; event_type:str; payment_external_id:str|None; public_order_id:str|None; payload:dict; event_key:str
async def ensure_fulfillment(session,payment,typ):
 key={"grant_subscription":"payment-grant","reverse_payment":"payment-reverse","grant_referral":"payment-referral"}[typ]+f":{payment.id}"
 await session.execute(insert(PaymentFulfillmentOperation).values(payment_id=payment.id,operation_type=typ,idempotency_key=key,status="pending",payload={},next_attempt_at=now_utc()).on_conflict_do_nothing(index_elements=["idempotency_key"]))
async def claim(session,worker_id):
 row=await session.scalar(select(WebhookInbox).where(WebhookInbox.status.in_(("pending","retry")),WebhookInbox.next_attempt_at<=now_utc(),WebhookInbox.attempts<WebhookInbox.max_attempts).order_by(WebhookInbox.id).with_for_update(skip_locked=True).limit(1))
 if not row:return None
 row.status="processing"; row.locked_by=worker_id; row.locked_at=now_utc(); row.attempts+=1; await session.flush()
 return InboxClaim(row.id,worker_id,row.attempts,row.event_type,row.payment_external_id,row.public_order_id,dict(row.payload),row.event_key)
async def fetch_provider(claim,transport=YooKassaService):
 # Refund objects are validated from the signed/provider-delivered event; payment state is independently GET-verified for payment events.
 if claim.event_type=="refund.succeeded": return None
 return await transport.get_payment_result(claim.payment_external_id)
async def _find_payment(session,claim):
 payment=await session.scalar(select(Payment).where(Payment.external_id==claim.payment_external_id).with_for_update())
 if not payment and claim.public_order_id:
  payment=await session.scalar(select(Payment).where(Payment.public_order_id==claim.public_order_id).with_for_update())
  if payment:
   conflict=await session.scalar(select(Payment.id).where(Payment.external_id==claim.payment_external_id,Payment.id!=payment.id))
   if conflict:return None,"external_id_conflict"
   payment.external_id=claim.payment_external_id
 return payment,None
async def finalize(session,claim,result):
 row=await session.scalar(select(WebhookInbox).where(WebhookInbox.id==claim.inbox_id).with_for_update())
 if not row or row.status!="processing" or row.locked_by!=claim.worker_id or row.attempts!=claim.attempt_number: raise WebhookInboxOwnershipError(claim.inbox_id)
 payment,error=await _find_payment(session,claim)
 if not payment:
  row.status="retry" if row.attempts<row.max_attempts else "dead"; row.last_error_code=error or "payment_not_visible"; row.next_attempt_at=now_utc()+timedelta(seconds=min(60,2**min(row.attempts,6))); row.locked_at=row.locked_by=None; return
 if claim.event_type=="refund.succeeded":
  obj=claim.payload.get("object") or {}; amount_obj=obj.get("amount") or {}; refund_id=obj.get("id"); currency=amount_obj.get("currency"); amount=Decimal(str(amount_obj.get("value","-1")))
  if not refund_id or (obj.get("payment_id") or obj.get("payment",{}).get("id"))!=payment.external_id: payment.reconciliation_status="manual_review"
  elif currency!=payment.currency or amount<=0: payment.reconciliation_status="manual_review"
  else:
   await session.execute(insert(PaymentRefund).values(payment_id=payment.id,provider_refund_id=str(refund_id),amount=amount,currency=currency,provider_status="succeeded",event_key=claim.event_key,processed_at=now_utc()).on_conflict_do_nothing(index_elements=["provider_refund_id"]))
   await session.flush(); total=await session.scalar(select(func.coalesce(func.sum(PaymentRefund.amount),0)).where(PaymentRefund.payment_id==payment.id,PaymentRefund.provider_status=="succeeded"))
   if total==payment.amount:
    payment.provider_status="refunded"; payment.fulfillment_status="reversal_pending"
    pending=(await session.scalars(select(PaymentFulfillmentOperation).where(PaymentFulfillmentOperation.payment_id==payment.id,PaymentFulfillmentOperation.operation_type.in_(("grant_subscription","grant_referral")),PaymentFulfillmentOperation.status.in_(("pending","retry"))).with_for_update())).all()
    for queued in pending: queued.status="cancelled"; queued.completed_at=now_utc()
    await ensure_fulfillment(session,payment,"reverse_payment")
   elif total<payment.amount:
    payment.reconciliation_status="manual_review"; payment.fulfillment_status="manual_review"
    grants=(await session.scalars(select(PaymentFulfillmentOperation).where(PaymentFulfillmentOperation.payment_id==payment.id,PaymentFulfillmentOperation.operation_type=="grant_subscription",PaymentFulfillmentOperation.status.in_(("pending","retry"))).with_for_update())).all()
    for grant_op in grants: grant_op.status="cancelled"; grant_op.completed_at=now_utc()
   else: payment.reconciliation_status="manual_review"; payment.fulfillment_status="manual_review"
 elif not result or not result.ok:
  dead=row.attempts>=row.max_attempts; row.status="dead" if dead else "retry"; row.processed_at=now_utc() if dead else None; row.last_error_code=result.error_kind.value if result and result.error_kind else "provider_error"; row.next_attempt_at=now_utc()+timedelta(seconds=10); row.locked_at=row.locked_by=None
  if dead: payment.reconciliation_status="required"; project_legacy_status(payment)
  return
 else:
  data=result.value or {}; amount=data.get("amount") or {}; metadata=data.get("metadata") or {}; status=data.get("status")
  money=status=="succeeded"
  if money and not payment.paid_at: payment.paid_at=payment.provider_confirmed_at=now_utc()
  mismatch=validate_provider_payment(payment,data)
  if mismatch: record_mismatch(session,payment,mismatch); payment.provider_status="manual_review"
  elif claim.event_type=="payment.succeeded" and status=="succeeded":
   prior_status=payment.provider_status; payment.provider_status="succeeded"
   if payment.checkout_status=="abandoned" or prior_status=="canceled":
    payment.reconciliation_status="mismatch"; payment.fulfillment_status="manual_review"
    session.add(PaymentEvent(payment_id=payment.id,event_type="paid_after_cancel",provider_status="succeeded",reason="webhook_after_abandoned_checkout",source="webhook_inbox"))
    queued=(await session.scalars(select(PaymentFulfillmentOperation).where(PaymentFulfillmentOperation.payment_id==payment.id,PaymentFulfillmentOperation.operation_type.in_(("grant_subscription","grant_referral")),PaymentFulfillmentOperation.status.in_(("pending","retry"))).with_for_update())).all()
    for operation in queued: operation.status="cancelled"; operation.completed_at=now_utc()
   else: payment.fulfillment_status="pending" if payment.fulfillment_status=="not_ready" else payment.fulfillment_status; await ensure_fulfillment(session,payment,"grant_subscription")
  elif claim.event_type=="payment.canceled" and status=="canceled":
   if payment.paid_at: payment.reconciliation_status="mismatch"; payment.fulfillment_status="manual_review"
   else: payment.provider_status="canceled"
 project_legacy_status(payment); row.status="succeeded"; row.processed_at=now_utc(); row.locked_at=row.locked_by=None; await session.flush()
async def recover_stale(session,lease_seconds=120):
 rows=(await session.scalars(select(WebhookInbox).where(WebhookInbox.status=="processing",WebhookInbox.locked_at<now_utc()-timedelta(seconds=lease_seconds)).with_for_update(skip_locked=True))).all()
 for row in rows:
  dead=row.attempts>=row.max_attempts; row.status="dead" if dead else "retry"; row.processed_at=now_utc() if dead else None; row.locked_at=row.locked_by=None; row.next_attempt_at=now_utc()
  if dead:
   payment=await session.scalar(select(Payment).where(Payment.external_id==row.payment_external_id).with_for_update())
   if payment: payment.reconciliation_status="required"; project_legacy_status(payment)
 return len(rows)
async def finalize_webhook_failure(session,claim,*,error_code,retryable=True):
 row=await session.scalar(select(WebhookInbox).where(WebhookInbox.id==claim.inbox_id).with_for_update())
 if not row or row.status!="processing" or row.locked_by!=claim.worker_id or row.attempts!=claim.attempt_number: raise WebhookInboxOwnershipError(claim.inbox_id)
 dead=(not retryable) or row.attempts>=row.max_attempts; row.status="dead" if dead else "retry"; row.processed_at=now_utc() if dead else None; row.next_attempt_at=now_utc()+timedelta(seconds=min(300,2**min(row.attempts,8))); row.last_error_code=str(error_code)[:100]; row.last_error=None; row.locked_at=row.locked_by=None
 if dead:
  payment=await session.scalar(select(Payment).where(Payment.external_id==row.payment_external_id).with_for_update())
  if payment: payment.reconciliation_status="required"; project_legacy_status(payment)

async def retry_dead_webhook_operation(session,inbox_id,*,reset_attempts,reason):
 row=await session.scalar(select(WebhookInbox).where(WebhookInbox.id==inbox_id).with_for_update())
 if not row or row.status!="dead": raise ValueError("webhook operation is not dead")
 if not reset_attempts and row.attempts>=row.max_attempts: raise ValueError("reset_attempts required")
 if reset_attempts: row.attempts=0
 row.status="retry"; row.next_attempt_at=now_utc(); row.locked_at=row.locked_by=None; row.processed_at=None; row.last_error_code=None; row.last_error="manual_retry:"+str(reason)[:200]
 return row
