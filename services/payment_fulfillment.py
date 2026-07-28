"""Exactly-once entitlement ledger and fulfillment executors."""
import uuid
from dataclasses import dataclass
from datetime import timedelta
from sqlalchemy import select
from database.models import EntitlementEntry, Payment, PaymentFulfillmentOperation, User
from services.payment_lifecycle import project_legacy_status
from services.subscription import SubscriptionService
from utils.datetime_helpers import now_utc
class PaymentFulfillmentOperationOwnershipError(RuntimeError): pass
@dataclass(frozen=True)
class FulfillmentClaim: operation_id:int; worker_id:str; attempt_number:int; operation_type:str
async def claim(session,worker_id=None):
    worker_id=worker_id or uuid.uuid4().hex
    op=await session.scalar(select(PaymentFulfillmentOperation).where(PaymentFulfillmentOperation.status.in_(("pending","retry")),PaymentFulfillmentOperation.next_attempt_at<=now_utc(),PaymentFulfillmentOperation.attempts<PaymentFulfillmentOperation.max_attempts).order_by(PaymentFulfillmentOperation.id).with_for_update(skip_locked=True).limit(1))
    if not op:return None
    op.status="processing"; op.locked_at=now_utc(); op.locked_by=worker_id; op.attempts+=1; await session.flush()
    return FulfillmentClaim(op.id,worker_id,op.attempts,op.operation_type)
async def _entry(session,payment,user_id,typ,days,**kw):
    existing=await session.scalar(select(EntitlementEntry).where(EntitlementEntry.beneficiary_user_id==user_id,EntitlementEntry.source_type=="payment",EntitlementEntry.source_id==str(payment.id),EntitlementEntry.entry_type==typ))
    if existing:return existing,False
    item=EntitlementEntry(beneficiary_user_id=user_id,source_type="payment",source_id=str(payment.id),entry_type=typ,days_delta=days,device_limit_snapshot=kw.get("limit"),tariff_id_snapshot=kw.get("tariff"),metadata_=kw.get("metadata",{}),reversed_entry_id=kw.get("reversed"))
    session.add(item); await session.flush(); return item,True
async def grant(session,op):
    payment=await session.scalar(select(Payment).where(Payment.id==op.payment_id).with_for_update()); user=await session.scalar(select(User).where(User.id==payment.user_id).with_for_update())
    manual=bool(op.payload.get("manual_without_provider_confirmation"))
    if (payment.provider_status!="succeeded" and not manual) or not user or user.is_deleted or user.is_banned:
        payment.fulfillment_status="manual_review"; payment.fulfillment_last_error_code="ineligible"; op.status="dead"; project_legacy_status(payment); return
    existing=await session.scalar(select(EntitlementEntry).where(EntitlementEntry.beneficiary_user_id==user.id,EntitlementEntry.source_type=="payment",EntitlementEntry.source_id==str(payment.id),EntitlementEntry.entry_type=="payment_grant"))
    if not existing:
        days=payment.snapshot_duration_days
        if not days or not payment.snapshot_device_limit: payment.fulfillment_status="manual_review"; op.status="dead"; return
        await SubscriptionService.extend_subscription(session,user.telegram_id,days,payment.snapshot_device_limit,payment.tariff_id)
        await _entry(session,payment,user.id,"manual_grant" if manual else "payment_grant",days,limit=payment.snapshot_device_limit,tariff=payment.tariff_id)
    payment.fulfillment_status="succeeded"; payment.fulfilled_at=payment.fulfilled_at or now_utc(); op.status="succeeded"; op.completed_at=now_utc(); project_legacy_status(payment)
    # Referral is isolated: failure can never roll back this grant on a later job.
    if user.referred_by:
        session.add(PaymentFulfillmentOperation(payment_id=payment.id,operation_type="grant_referral",idempotency_key=f"payment-referral:{payment.id}",status="pending",payload={},next_attempt_at=now_utc()))
async def referral(session,op):
    payment=await session.scalar(select(Payment).where(Payment.id==op.payment_id).with_for_update()); user=await session.scalar(select(User).where(User.id==payment.user_id).with_for_update())
    reversal=await session.scalar(select(EntitlementEntry.id).where(EntitlementEntry.source_type=="payment",EntitlementEntry.source_id==str(payment.id),EntitlementEntry.entry_type=="payment_reversal"))
    if not user or not user.referred_by or payment.provider_status!="succeeded" or payment.fulfillment_status!="succeeded" or reversal:
        op.status="cancelled"; op.completed_at=now_utc(); return
    ref=await session.scalar(select(User).where(User.telegram_id==user.referred_by).with_for_update())
    if not ref: op.status="dead"; op.last_error_code="referrer_missing"; return
    # Current product grants seven days to both parties on the first paid order.
    previous=await session.scalar(select(Payment.id).where(Payment.user_id==user.id,Payment.provider_status=="succeeded",Payment.id< payment.id).limit(1))
    if previous: op.status="succeeded"; return
    _,a=await _entry(session,payment,user.id,"referral_user_bonus",7); _,b=await _entry(session,payment,ref.id,"referral_referrer_bonus",7)
    if a: await SubscriptionService.extend_subscription(session,user.telegram_id,7)
    if b: await SubscriptionService.extend_subscription(session,ref.telegram_id,7)
    payment.referral_user_bonus_days=7; payment.referral_referrer_bonus_days=7; op.status="succeeded"; op.completed_at=now_utc()
async def reverse(session,op):
    payment=await session.scalar(select(Payment).where(Payment.id==op.payment_id).with_for_update()); user=await session.scalar(select(User).where(User.id==payment.user_id).with_for_update())
    grant_entry=await session.scalar(select(EntitlementEntry).where(EntitlementEntry.beneficiary_user_id==payment.user_id,EntitlementEntry.source_type=="payment",EntitlementEntry.source_id==str(payment.id),EntitlementEntry.entry_type=="payment_grant"))
    reversal=await session.scalar(select(EntitlementEntry).where(EntitlementEntry.beneficiary_user_id==payment.user_id,EntitlementEntry.source_type=="payment",EntitlementEntry.source_id==str(payment.id),EntitlementEntry.entry_type=="payment_reversal"))
    if grant_entry and not reversal:
        days=payment.snapshot_duration_days or 0; now=now_utc(); user.subscription_end=max(now,user.subscription_end-timedelta(days=days)) if user.subscription_end else now
        await _entry(session,payment,user.id,"payment_reversal",-days,reversed=grant_entry.id)
        await SubscriptionService.sync_access_state(session,user)
    referrals=(await session.scalars(select(PaymentFulfillmentOperation).where(PaymentFulfillmentOperation.payment_id==payment.id,PaymentFulfillmentOperation.operation_type=="grant_referral",PaymentFulfillmentOperation.status.in_(("pending","retry"))).with_for_update())).all()
    for referral_op in referrals: referral_op.status="cancelled"; referral_op.completed_at=now_utc()
    bonus_entries=(await session.scalars(select(EntitlementEntry).where(EntitlementEntry.source_type=="payment",EntitlementEntry.source_id==str(payment.id),EntitlementEntry.entry_type.in_(("referral_user_bonus","referral_referrer_bonus"))))).all()
    for bonus in bonus_entries:
        _,created=await _entry(session,payment,bonus.beneficiary_user_id,"referral_reversal",-bonus.days_delta,reversed=bonus.id,metadata={"reversed_entry_type":bonus.entry_type})
        if created:
            beneficiary=await session.scalar(select(User).where(User.id==bonus.beneficiary_user_id).with_for_update())
            if beneficiary and beneficiary.subscription_end: beneficiary.subscription_end=max(now_utc(),beneficiary.subscription_end-timedelta(days=bonus.days_delta)); await SubscriptionService.sync_access_state(session,beneficiary)
    latest=await session.scalar(select(Payment).where(Payment.user_id==payment.user_id,Payment.id!=payment.id,Payment.provider_status=="succeeded",Payment.fulfillment_status=="succeeded").order_by(Payment.fulfilled_at.desc()).limit(1))
    if latest: user.device_limit=latest.snapshot_device_limit or user.device_limit; user.current_tariff_id=latest.tariff_id
    payment.fulfillment_status="reversed"; payment.reversed_at=payment.reversed_at or now_utc(); op.status="succeeded"; op.completed_at=now_utc(); project_legacy_status(payment)
async def execute(session,claim):
    op=await session.scalar(select(PaymentFulfillmentOperation).where(PaymentFulfillmentOperation.id==claim.operation_id).with_for_update())
    if not op or op.status!="processing" or op.locked_by!=claim.worker_id or op.attempts!=claim.attempt_number: raise PaymentFulfillmentOperationOwnershipError(claim.operation_id)
    try:
        if op.operation_type=="grant_subscription": await grant(session,op)
        elif op.operation_type=="grant_referral": await referral(session,op)
        elif op.operation_type=="reverse_payment": await reverse(session,op)
        op.locked_at=op.locked_by=None; await session.flush()
    except Exception as exc:
        op.status="dead" if op.attempts>=op.max_attempts else "retry"; op.last_error_code=type(exc).__name__; op.last_error=str(exc)[:2000]; op.next_attempt_at=now_utc()+timedelta(seconds=min(300,2**min(op.attempts,8))); op.locked_at=op.locked_by=None; raise
async def recover_stale(session,lease_seconds=120):
    rows=(await session.scalars(select(PaymentFulfillmentOperation).where(PaymentFulfillmentOperation.status=="processing",PaymentFulfillmentOperation.locked_at<now_utc()-timedelta(seconds=lease_seconds)).with_for_update(skip_locked=True))).all()
    for op in rows: op.status="retry"; op.locked_at=op.locked_by=None; op.next_attempt_at=now_utc()
    return len(rows)
