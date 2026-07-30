"""Fenced PostgreSQL queue for YooKassa provider commands."""
from dataclasses import dataclass
import uuid
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from database.models import Payment, PaymentEvent, PaymentFulfillmentOperation, PaymentProviderOperation, TariffQuote
from services.payment_lifecycle import project_legacy_status
from services.payment_queue_timing import PROVIDER_LEASE_SECONDS
from services.payment_provider_state import apply_provider_transition
from services.yookassa_service import YooKassaService, YooKassaErrorKind, YooKassaResult
from utils.datetime_helpers import now_utc
from services.payment_kind import is_tariff_change_payment

class PaymentProviderOperationOwnershipError(RuntimeError): pass
@dataclass(frozen=True)
class ProviderOperationClaim:
    operation_id:int; payment_id:int; operation_type:str; payload:dict; idempotency_key:str; worker_id:str; attempt_number:int; external_id:str|None; created_at:object
@dataclass(frozen=True)
class ProviderRetryDecision:
    accepted:bool; reason:str; operation_id:int

VALID_PROVIDER_STATUSES={"pending","waiting_for_capture","succeeded","canceled"}
def provider_transition_source(claim):
    """Describe the HTTP evidence, independently of the durable command type."""
    if claim.operation_type=="create_payment":
        return "provider_get_payment" if claim.external_id else "provider_create_payment_post"
    if claim.operation_type=="reconcile_payment": return "provider_reconcile_payment_get"
    return f"provider_{claim.operation_type}"
def classify_invalid_provider_snapshot(claim,data):
    """Classify a decoded provider object whose status is absent or unknown."""
    has_provider_id=bool(data.get("id"))
    if claim.operation_type=="create_payment":
        return YooKassaResult(False,error_kind=YooKassaErrorKind.INVALID_RESPONSE,retryable=True,ambiguous=not has_provider_id)
    # GET/reconciliation returned a response, but not a usable provider state.
    return YooKassaResult(False,error_kind=YooKassaErrorKind.INVALID_RESPONSE,retryable=True,ambiguous=False)

def create_payload(payment,description,return_url):
    return {"amount":{"value":format(payment.amount,'.2f'),"currency":payment.currency},"description":description,"confirmation":{"type":"redirect","return_url":return_url},"metadata":{"order_id":payment.public_order_id,"local_payment_id":str(payment.id)},"capture":True}
async def enqueue_create(session,payment,description,return_url):
    op=PaymentProviderOperation(payment_id=payment.id,operation_type="create_payment",status="pending",idempotency_key=payment.provider_idempotency_key,payload=create_payload(payment,description,return_url),next_attempt_at=now_utc()); session.add(op); await session.flush(); return op
async def ensure_operation(session,payment,operation_type):
    if operation_type!="cancel_payment": raise ValueError(operation_type)
    key=f"payment-cancel:{payment.id}:v1"; payload={"provider_payment_id":payment.external_id}
    stmt=insert(PaymentProviderOperation).values(payment_id=payment.id,operation_type=operation_type,status="pending",idempotency_key=key,payload=payload,next_attempt_at=now_utc()).on_conflict_do_nothing(index_elements=["idempotency_key"]).returning(PaymentProviderOperation.id)
    op_id=await session.scalar(stmt)
    return await session.get(PaymentProviderOperation,op_id) if op_id else await session.scalar(select(PaymentProviderOperation).where(PaymentProviderOperation.idempotency_key==key))
async def ensure_reconcile_payment_operation(session,payment,*,reason):
    payment=await session.scalar(select(Payment).where(Payment.id==payment.id).with_for_update())
    active=await session.scalar(select(PaymentProviderOperation).where(PaymentProviderOperation.payment_id==payment.id,PaymentProviderOperation.operation_type=="reconcile_payment",PaymentProviderOperation.status.in_(("pending","retry","processing"))).order_by(PaymentProviderOperation.id.desc()).with_for_update())
    if active:return active
    op=PaymentProviderOperation(payment_id=payment.id,operation_type="reconcile_payment",status="pending",idempotency_key=f"payment-reconcile:{payment.id}:{uuid.uuid4().hex}",payload={"provider_payment_id":payment.external_id,"reason":str(reason)[:100]},next_attempt_at=now_utc())
    session.add(op); await session.flush(); return op
async def retry_dead_provider_operation(session,operation_id,*,reset_attempts,reason):
    op=await session.scalar(select(PaymentProviderOperation).where(PaymentProviderOperation.id==operation_id).with_for_update())
    if not op or op.status!="dead": raise ValueError("operation is not dead")
    payment=await session.scalar(select(Payment).where(Payment.id==op.payment_id).with_for_update())
    if op.operation_type=="create_payment" and not payment.external_id and now_utc()-op.created_at>=timedelta(hours=24):
        payment.reconciliation_status="manual_review"; payment.fulfillment_status="manual_review"; payment.manual_review_reason="create_idempotency_window_expired"
        session.add(PaymentEvent(payment_id=payment.id,event_type="provider_operation_admin_retry_rejected",provider_status=payment.provider_status,reason="create_idempotency_window_expired",source="admin_retry"))
        project_legacy_status(payment)
        return ProviderRetryDecision(False,"create_idempotency_window_expired",op.id)
    # payload is the immutable provider command; audit metadata must never be mixed
    # into a request which may be replayed byte-for-byte.
    session.add(PaymentEvent(payment_id=payment.id,event_type="provider_operation_admin_retry",provider_status=payment.provider_status,reason=str(reason)[:255],source="admin_retry"))
    op.status="retry"; op.completed_at=op.locked_at=op.locked_by=op.last_error_code=op.last_error=None; op.next_attempt_at=now_utc()
    if reset_attempts:op.attempts=0
    elif op.attempts>=op.max_attempts:raise ValueError("reset_attempts required for exhausted operation")
    return ProviderRetryDecision(True,"retry_scheduled",op.id)
async def ensure_cancel_payment_operation(session,payment):
    if payment.provider_status!="waiting_for_capture": return None
    return await ensure_operation(session,payment,"cancel_payment")
async def claim(session,worker_id):
    op=await session.scalar(select(PaymentProviderOperation).where(PaymentProviderOperation.status.in_(("pending","retry")),PaymentProviderOperation.next_attempt_at<=now_utc(),PaymentProviderOperation.attempts<PaymentProviderOperation.max_attempts).order_by(PaymentProviderOperation.id).with_for_update(skip_locked=True).limit(1))
    if not op:return None
    op.status="processing"; op.locked_by=worker_id; op.locked_at=now_utc(); op.attempts+=1
    payment=await session.get(Payment,op.payment_id)
    await session.flush()
    return ProviderOperationClaim(op.id,op.payment_id,op.operation_type,dict(op.payload),op.idempotency_key,worker_id,op.attempts,payment.external_id if payment else None,op.created_at)
async def perform_http(claim,transport=YooKassaService):
    if claim.operation_type=="create_payment":
        if claim.external_id:return await transport.get_payment_result(claim.external_id)
        if now_utc()-claim.created_at>=timedelta(hours=24):return YooKassaResult(False,error_kind=YooKassaErrorKind.IDEMPOTENCY_WINDOW_EXPIRED,retryable=False,ambiguous=True)
        return await transport.create_payment_result(claim.payload,idempotency_key=claim.idempotency_key)
    provider_id=claim.payload.get("provider_payment_id") or claim.external_id
    if not provider_id:return YooKassaResult(False,error_kind=YooKassaErrorKind.VALIDATION_FAILED)
    if claim.operation_type=="cancel_payment":
        result=await transport.cancel_payment_result(provider_id,idempotency_key=claim.idempotency_key)
        status=(result.value or {}).get("status") if result.ok else None
        if result.ambiguous or (result.ok and status not in {"canceled","succeeded"}):
            return await transport.get_payment_result(provider_id)
        return result
    if claim.operation_type=="reconcile_payment": return await transport.get_payment_result(provider_id)
    return YooKassaResult(False,error_kind=YooKassaErrorKind.VALIDATION_FAILED)
async def finalize(session,claim,result,transport=YooKassaService):
    payment=await session.scalar(select(Payment).where(Payment.id==claim.payment_id).with_for_update())
    op=await session.scalar(select(PaymentProviderOperation).where(PaymentProviderOperation.id==claim.operation_id).with_for_update())
    if not op or op.status!="processing" or op.locked_by!=claim.worker_id or op.attempts!=claim.attempt_number: raise PaymentProviderOperationOwnershipError(claim.operation_id)
    if result.ok:
        data=result.value or {}; status=data.get("status")
        invalid_snapshot_code=None
        if claim.operation_type=="create_payment":
            confirmation=data.get("confirmation") or {}; url=confirmation.get("confirmation_url") or confirmation.get("url")
            if data.get("id"):
                payment.external_id=str(data["id"]); payment.payment_url=url or payment.payment_url; payment.payment_method="yookassa"
        if status not in VALID_PROVIDER_STATUSES:
            invalid_snapshot_code="provider_status_missing" if status is None else "provider_status_invalid"
            result=classify_invalid_provider_snapshot(claim,data); payment.reconciliation_status="required"
        # A cancel is only confirmed by the provider's terminal cancellation state.
        # `succeeded` is terminal too, but represents money received after cancel and
        # must flow through the financial-truth/manual-review transition below.
        if result.ok and claim.operation_type=="cancel_payment" and status in {"pending","waiting_for_capture"}:
            # This is a trustworthy GET snapshot even though it does not confirm the
            # cancellation.  Preserve the observed provider state at every attempt.
            payment.provider_status=status
            result=YooKassaResult(False,error_kind=YooKassaErrorKind.INVALID_RESPONSE,retryable=True,ambiguous=False)
            cancel_not_confirmed=True
        else: cancel_not_confirmed=False
        if result.ok and claim.operation_type=="create_payment":
            confirmation=data.get("confirmation") or {}; url=confirmation.get("confirmation_url") or confirmation.get("url")
            primary=claim.external_id is None
            if not data.get("id") or (primary and not url): result=YooKassaResult(False,error_kind=YooKassaErrorKind.INVALID_RESPONSE,retryable=True,ambiguous=primary)
            else:
                if not primary and status in {"pending","waiting_for_capture"} and not payment.payment_url: payment.reconciliation_status="required"
        if result.ok:
            transition=await apply_provider_transition(session,payment,data,source=provider_transition_source(claim))
            if transition.outcome=="retry": result=YooKassaResult(False,error_kind=YooKassaErrorKind.INVALID_RESPONSE,retryable=True,ambiguous=False)
            change_quote = await is_tariff_change_payment(session,payment)
            if change_quote and transition.grant_allowed:
                # Financial evidence is durable, but phase 6 deliberately has no
                # entitlement/application route.
                payment.fulfillment_status="not_ready"
            elif transition.grant_allowed:
                payment.fulfillment_status="pending"
                await session.execute(insert(PaymentFulfillmentOperation).values(payment_id=payment.id,operation_type="grant_subscription",idempotency_key=f"payment-grant:{payment.id}",status="pending",payload={},next_attempt_at=now_utc()).on_conflict_do_nothing(index_elements=["idempotency_key"]))
            elif transition.reason=="paid_after_cancel":
                queued=(await session.scalars(select(PaymentFulfillmentOperation).where(PaymentFulfillmentOperation.payment_id==payment.id,PaymentFulfillmentOperation.operation_type.in_(("grant_subscription","grant_referral")),PaymentFulfillmentOperation.status.in_(("pending","retry"))).with_for_update())).all()
                for queued_op in queued: queued_op.status="cancelled"; queued_op.completed_at=now_utc()
        if result.ok: op.status="succeeded"; op.completed_at=now_utc(); payment.reconciliation_status="ok" if payment.reconciliation_status not in {"mismatch","manual_review"} else payment.reconciliation_status
    if not result.ok:
        op.last_error_code="cancel_not_confirmed" if locals().get("cancel_not_confirmed",False) else (locals().get("invalid_snapshot_code") or (result.error_kind.value if result.error_kind else "unknown")); op.last_error=None
        exhausted=op.attempts>=op.max_attempts; op.status="dead" if exhausted or not result.retryable else "retry"; op.next_attempt_at=now_utc()+timedelta(seconds=min(300,2**min(op.attempts,8)))
        if payment.provider_status in {"succeeded","refunded","canceled"}:
            payment.reconciliation_status="manual_review" if op.status=="dead" else "required"
            session.add(PaymentEvent(payment_id=payment.id,event_type="provider_operation_error_after_terminal",provider_status=payment.provider_status,reason=op.last_error_code,source="provider_finalizer"))
        else:
            payment.provider_status="unknown" if result.ambiguous else ("manual_review" if op.status=="dead" else payment.provider_status); payment.reconciliation_status="required" if op.status=="retry" else "manual_review"
        if op.status=="dead": op.completed_at=now_utc()
        if locals().get("cancel_not_confirmed",False) and op.status=="dead":
            payment.provider_status=status; payment.reconciliation_status="manual_review"
            session.add(PaymentEvent(payment_id=payment.id,event_type="cancel_not_confirmed_at_attempt_limit",provider_status=status,reason="cancel_not_confirmed",source="provider_finalizer"))
    op.locked_at=op.locked_by=None; project_legacy_status(payment); await session.flush()
async def recover_stale(session,lease_seconds=PROVIDER_LEASE_SECONDS):
    rows=(await session.scalars(select(PaymentProviderOperation).where(PaymentProviderOperation.status=="processing",PaymentProviderOperation.locked_at<now_utc()-timedelta(seconds=lease_seconds)).with_for_update(skip_locked=True))).all()
    for op in rows:
        dead=op.attempts>=op.max_attempts; op.status="dead" if dead else "retry"; op.completed_at=now_utc() if dead else None; op.locked_at=op.locked_by=None; op.next_attempt_at=now_utc()
        payment=await session.get(Payment,op.payment_id)
        if payment.provider_status in {"succeeded","refunded","canceled"}:
            payment.reconciliation_status="manual_review" if dead else "required"; session.add(PaymentEvent(payment_id=payment.id,event_type="stale_provider_operation_after_terminal",provider_status=payment.provider_status,reason="lease_expired_at_attempt_limit" if dead else "lease_expired",source="provider_recovery"))
        elif dead: payment.provider_status="manual_review"; payment.reconciliation_status="manual_review"
        else: payment.reconciliation_status="required"
        project_legacy_status(payment)
    return len(rows)

async def finalize_provider_failure(session,claim,*,error_code,retryable):
    payment=await session.scalar(select(Payment).where(Payment.id==claim.payment_id).with_for_update())
    op=await session.scalar(select(PaymentProviderOperation).where(PaymentProviderOperation.id==claim.operation_id).with_for_update())
    if not op or op.status!="processing" or op.locked_by!=claim.worker_id or op.attempts!=claim.attempt_number: raise PaymentProviderOperationOwnershipError(claim.operation_id)
    dead=(not retryable) or op.attempts>=op.max_attempts; op.status="dead" if dead else "retry"; op.completed_at=now_utc() if dead else None; op.next_attempt_at=now_utc()+timedelta(seconds=min(300,2**min(op.attempts,8))); op.last_error_code=str(error_code)[:100]; op.last_error=None; op.locked_at=op.locked_by=None
    if payment.provider_status in {"succeeded","refunded","canceled"}:
        payment.reconciliation_status="manual_review" if dead else "required"
        session.add(PaymentEvent(payment_id=payment.id,event_type="provider_failure_after_terminal",provider_status=payment.provider_status,reason=str(error_code)[:100],source="provider_failure_finalizer"))
    elif dead: payment.provider_status="manual_review"; payment.reconciliation_status="manual_review"
    else: payment.reconciliation_status="required"
    project_legacy_status(payment); return op
