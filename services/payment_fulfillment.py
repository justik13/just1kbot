"""Exactly-once entitlement ledger and fulfillment executors."""
import uuid
from dataclasses import dataclass
from datetime import timedelta
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from database.models import EntitlementEntry, PaidValueLedgerEntry, Payment, PaymentFulfillmentOperation, PaymentEvent, PaymentRefund, ReferralEligibility, ReferralReward, Tariff, TariffQuote, TariffVersion, User
from database.repositories.paid_value_repo import PaidValueLedgerConflictError, get_or_create_confirmed_payment_entry, get_or_create_payment_reversal_entry
from database.connection import queue_post_commit_task
from services.audit_service import AuditService
from services.payment_lifecycle import project_legacy_status
from services.payment_queue_timing import FULFILLMENT_LEASE_SECONDS
from services.subscription import SubscriptionService
from utils.datetime_helpers import now_utc
from services.payment_kind import is_tariff_change_payment
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
    change_quote = payment and await is_tariff_change_payment(session,payment)
    if change_quote:
        payment.fulfillment_status="not_ready"; payment.fulfillment_last_error_code="tariff_change_legacy_grant_blocked"
        op.status="dead"; op.last_error_code="tariff_change_legacy_grant_blocked"; op.completed_at=now_utc()
        return
    manual=bool(op.payload.get("manual_without_provider_confirmation"))
    if (payment.provider_status!="succeeded" and not manual) or not user or user.is_deleted or user.is_banned:
        payment.fulfillment_status="manual_review"; payment.fulfillment_last_error_code="ineligible"; op.status="dead"; op.completed_at=now_utc(); project_legacy_status(payment); return
    grant_entry_type="manual_grant" if manual else "payment_grant"
    existing=await session.scalar(select(EntitlementEntry).where(EntitlementEntry.beneficiary_user_id==user.id,EntitlementEntry.source_type=="payment",EntitlementEntry.source_id==str(payment.id),EntitlementEntry.entry_type==grant_entry_type))
    refunded=await session.scalar(select(func.coalesce(func.sum(PaymentRefund.amount),0)).where(PaymentRefund.payment_id==payment.id,PaymentRefund.provider_status=="succeeded"))
    if (not manual and payment.reconciliation_status in {"mismatch","manual_review"}) or refunded:
        payment.fulfillment_status="manual_review"; op.status="cancelled"; op.completed_at=now_utc(); return
    quote = None
    version = None
    if not manual and (payment.tariff_quote_id or payment.tariff_version_id):
        if not payment.tariff_quote_id or not payment.tariff_version_id:
            payment.fulfillment_status=payment.reconciliation_status="manual_review"; payment.fulfillment_last_error_code="incomplete_quote_snapshot"; op.status="dead"; op.completed_at=now_utc(); return
        quote=await session.scalar(select(TariffQuote).where(TariffQuote.id==payment.tariff_quote_id).with_for_update())
        version=await session.get(TariffVersion,payment.tariff_version_id)
        if (not quote or not version or payment.user_id != quote.user_id or
            quote.payment_id != payment.id or payment.tariff_quote_id != quote.id or
            payment.tariff_version_id != quote.target_tariff_version_id or
            quote.target_tariff_version_id != version.id or
            payment.tariff_id != version.tariff_id):
            if quote: quote.status="manual_review"; quote.manual_review_at=quote.manual_review_at or now_utc(); quote.diagnostic_reason="payment_tariff_version_mismatch"
            payment.fulfillment_status=payment.reconciliation_status="manual_review"; payment.fulfillment_last_error_code="payment_tariff_version_mismatch"; op.status="dead"; op.completed_at=now_utc(); return
        if (payment.amount != payment.snapshot_amount or
            payment.amount != quote.confirmed_payment_required_rub or
            payment.currency != payment.snapshot_currency or
            payment.currency != quote.currency or
            (payment.snapshot_duration_days or 0) * 24 != version.duration_hours or
            payment.snapshot_device_limit != version.device_limit):
            quote.status="manual_review"; quote.manual_review_at=quote.manual_review_at or now_utc(); quote.diagnostic_reason="quote_payment_snapshot_mismatch"
            payment.fulfillment_status=payment.reconciliation_status="manual_review"; payment.fulfillment_last_error_code="quote_payment_snapshot_mismatch"; op.status="dead"; op.completed_at=now_utc(); return
        if quote.status == "cancelled" or payment.checkout_status == "abandoned":
            payment.fulfillment_status=payment.reconciliation_status="manual_review"; payment.fulfillment_last_error_code="paid_after_checkout_cancel"; op.status="cancelled"; op.completed_at=now_utc(); return
        if quote.status not in {"active","consumed","expired"}:
            payment.fulfillment_status=payment.reconciliation_status="manual_review"; payment.fulfillment_last_error_code="quote_not_fulfillable"; op.status="cancelled"; op.completed_at=now_utc(); return
        if payment.provider_confirmed_at is None or payment.provider_confirmed_at > quote.expires_at:
            quote.status="manual_review"; quote.manual_review_at=quote.manual_review_at or now_utc(); quote.diagnostic_reason="provider_confirmation_missing_or_after_expiry"
            payment.fulfillment_status=payment.reconciliation_status="manual_review"; payment.fulfillment_last_error_code="quote_expired_at_confirmation"; op.status="cancelled"; op.completed_at=now_utc(); project_legacy_status(payment); return
        try:
            await get_or_create_confirmed_payment_entry(session,user_id=user.id,
                payment_id=payment.id,quote_id=quote.id,tariff_version_id=version.id,
                paid_hours=version.duration_hours,paid_value_rub=payment.snapshot_amount)
        except PaidValueLedgerConflictError:
            quote.status="manual_review"; quote.manual_review_at=quote.manual_review_at or now_utc(); quote.diagnostic_reason="paid_value_ledger_conflict"
            payment.fulfillment_status=payment.reconciliation_status="manual_review"; payment.fulfillment_last_error_code="paid_value_ledger_conflict"; op.status="dead"; op.completed_at=now_utc(); return
    if not existing:
        days=payment.snapshot_duration_days
        if not days or not payment.snapshot_device_limit: payment.fulfillment_status="manual_review"; op.status="dead"; op.completed_at=now_utc(); return
        await SubscriptionService.extend_subscription(session,user.telegram_id,days,payment.snapshot_device_limit,payment.tariff_id)
        await _entry(session,payment,user.id,grant_entry_type,days,limit=payment.snapshot_device_limit,tariff=payment.tariff_id)
    if quote is not None:
        if quote.status in {"active","expired"}: quote.status="consumed"
        quote.consumed_at=quote.consumed_at or now_utc()
    payment.fulfillment_status="succeeded"; payment.fulfilled_at=payment.fulfilled_at or now_utc(); op.status="succeeded"; op.completed_at=now_utc(); project_legacy_status(payment)
    # Referral is isolated: failure can never roll back this grant on a later job.
    user.last_payment_at=now_utc()
    await AuditService.log_action(session,admin_id=0,action="PAYMENT_FULFILLED",target_type="Payment",target_id=payment.id,details="durable fulfillment succeeded")
    tariff=await session.get(Tariff,payment.tariff_id)
    async def post_commit_success():
        from bot.middlewares.user_context import invalidate_user_cache
        from services.payment_service.service import _notify_payment_success
        invalidate_user_cache(user.telegram_id)
        await _notify_payment_success(user.telegram_id,tariff.name if tariff else "—",str(user.subscription_end))
    queue_post_commit_task(session,post_commit_success)
    if user.referred_by:
        await session.execute(insert(PaymentFulfillmentOperation).values(payment_id=payment.id,operation_type="grant_referral",idempotency_key=f"payment-referral:{payment.id}",status="pending",payload={},next_attempt_at=now_utc()).on_conflict_do_nothing(index_elements=["idempotency_key"]))
async def referral(session,op):
    payment=await session.scalar(select(Payment).where(Payment.id==op.payment_id).with_for_update()); user=await session.scalar(select(User).where(User.id==payment.user_id).with_for_update())
    if await is_tariff_change_payment(session,payment): op.status="dead"; op.last_error_code="tariff_change_legacy_grant_blocked"; op.completed_at=now_utc(); return
    reversal=await session.scalar(select(EntitlementEntry.id).where(EntitlementEntry.source_type=="payment",EntitlementEntry.source_id==str(payment.id),EntitlementEntry.entry_type=="payment_reversal"))
    if not user or user.is_deleted or user.is_banned or not user.referred_by or user.referred_by==user.telegram_id or payment.provider_status!="succeeded" or payment.fulfillment_status!="succeeded" or reversal:
        op.status="cancelled"; op.completed_at=now_utc(); return
    ref=await session.scalar(select(User).where(User.telegram_id==user.referred_by).with_for_update())
    if not ref: op.status="dead"; op.completed_at=now_utc(); op.last_error_code="referrer_missing"; return
    if ref.is_deleted or ref.is_banned: op.status="cancelled"; op.completed_at=now_utc(); return
    if (payment.snapshot_duration_days or 0)<30: op.status="cancelled"; op.completed_at=now_utc(); return
    marker=await session.scalar(select(ReferralReward).where(ReferralReward.source_payment_id==payment.id).with_for_update())
    if marker and marker.reversed_at: op.status="cancelled"; op.completed_at=now_utc(); return
    eligibility=await session.scalar(select(ReferralEligibility).where(ReferralEligibility.referred_user_id==user.id).with_for_update())
    if eligibility and eligibility.status=="blocked": op.status="cancelled"; op.completed_at=now_utc(); op.last_error_code="referral_eligibility_blocked"; return
    if not marker:
        first=eligibility is None
        if first:
            eligibility=ReferralEligibility(referred_user_id=user.id,status="claimed",source_payment_id=payment.id,reason="first_durable_reward"); session.add(eligibility)
        marker=ReferralReward(referred_user_id=user.id,source_payment_id=payment.id,referrer_user_id=ref.id,is_first=first); session.add(marker); await session.flush()
    user_days=5 if marker.is_first else 0; ref_days=3 if marker.is_first else 1
    a=False
    if user_days: _,a=await _entry(session,payment,user.id,"referral_user_bonus",user_days)
    _,b=await _entry(session,payment,ref.id,"referral_referrer_bonus",ref_days)
    if a: await SubscriptionService.extend_subscription(session,user.telegram_id,user_days)
    if b:
        await SubscriptionService.extend_subscription(session,ref.telegram_id,ref_days); ref.referral_days=(ref.referral_days or 0)+ref_days
    payment.referral_user_bonus_days=user_days; payment.referral_referrer_bonus_days=ref_days; op.status="succeeded"; op.completed_at=now_utc()
async def reverse(session,op):
    payment=await session.scalar(select(Payment).where(Payment.id==op.payment_id).with_for_update()); user=await session.scalar(select(User).where(User.id==payment.user_id).with_for_update())
    if await is_tariff_change_payment(session,payment): payment.fulfillment_status="manual_review"; payment.fulfillment_last_error_code="tariff_change_legacy_reversal_blocked"; op.status="dead"; op.last_error_code="tariff_change_legacy_reversal_blocked"; op.completed_at=now_utc(); return
    grant_entry=await session.scalar(select(EntitlementEntry).where(EntitlementEntry.beneficiary_user_id==payment.user_id,EntitlementEntry.source_type=="payment",EntitlementEntry.source_id==str(payment.id),EntitlementEntry.entry_type.in_(("payment_grant","manual_grant"))))
    reversal=await session.scalar(select(EntitlementEntry).where(EntitlementEntry.beneficiary_user_id==payment.user_id,EntitlementEntry.source_type=="payment",EntitlementEntry.source_id==str(payment.id),EntitlementEntry.entry_type=="payment_reversal"))
    user_bonus=await session.scalar(select(EntitlementEntry).where(EntitlementEntry.beneficiary_user_id==payment.user_id,EntitlementEntry.source_type=="payment",EntitlementEntry.source_id==str(payment.id),EntitlementEntry.entry_type=="referral_user_bonus"))
    ref_bonus=await session.scalar(select(EntitlementEntry).where(EntitlementEntry.source_type=="payment",EntitlementEntry.source_id==str(payment.id),EntitlementEntry.entry_type=="referral_referrer_bonus"))
    if payment.referral_user_bonus_days>0 and not user_bonus:
        payment.fulfillment_status=payment.reconciliation_status="manual_review"; payment.fulfillment_last_error_code="referral_user_ledger_missing"; op.status="dead"; op.completed_at=now_utc(); project_legacy_status(payment); return
    if payment.referral_referrer_bonus_days>0 and not ref_bonus:
        reason="legacy_referrer_unresolved" if payment.manual_review_reason=="legacy_referrer_unresolved" else "referral_referrer_ledger_missing"
        payment.fulfillment_status=payment.reconciliation_status="manual_review"; payment.fulfillment_last_error_code=reason; op.status="dead"; op.completed_at=now_utc(); project_legacy_status(payment); return
    expected_grant=bool(payment.fulfilled_at or payment.status=="completed" or payment.manual_review_reason in {"legacy_entitlement_snapshot_missing","grant_ledger_missing"})
    if not grant_entry and expected_grant:
        payment.fulfillment_status="manual_review"; payment.reconciliation_status="manual_review"; payment.fulfillment_last_error_code="grant_ledger_missing"; payment.manual_review_reason="grant_ledger_missing"; op.status="dead"; op.completed_at=now_utc(); project_legacy_status(payment); return
    paid_entry=await session.scalar(select(PaidValueLedgerEntry).where(
        PaidValueLedgerEntry.payment_id==payment.id,
        PaidValueLedgerEntry.entry_type=="confirmed_payment").with_for_update())
    if paid_entry is not None:
        try: await get_or_create_payment_reversal_entry(session,original=paid_entry)
        except PaidValueLedgerConflictError:
            payment.fulfillment_status=payment.reconciliation_status="manual_review"; payment.fulfillment_last_error_code="paid_value_reversal_conflict"; op.status="dead"; op.completed_at=now_utc(); return
    if grant_entry and not reversal:
        days=abs(grant_entry.days_delta); now=now_utc(); user.subscription_end=max(now,user.subscription_end-timedelta(days=days)) if user.subscription_end else now
        await _entry(session,payment,user.id,"payment_reversal",-days,reversed=grant_entry.id)
        await SubscriptionService.sync_access_state(session,user)
    elif not grant_entry:
        session.add(PaymentEvent(payment_id=payment.id,event_type="payment_reversal_no_grant",provider_status=payment.provider_status,reason="refund_before_fulfillment",source="fulfillment"))
    referrals=(await session.scalars(select(PaymentFulfillmentOperation).where(PaymentFulfillmentOperation.payment_id==payment.id,PaymentFulfillmentOperation.operation_type=="grant_referral",PaymentFulfillmentOperation.status.in_(("pending","retry"))).with_for_update())).all()
    for referral_op in referrals: referral_op.status="cancelled"; referral_op.completed_at=now_utc()
    reward=await session.scalar(select(ReferralReward).where(ReferralReward.source_payment_id==payment.id).with_for_update())
    if reward: reward.reversed_at=reward.reversed_at or now_utc()
    bonus_entries=(await session.scalars(select(EntitlementEntry).where(EntitlementEntry.source_type=="payment",EntitlementEntry.source_id==str(payment.id),EntitlementEntry.entry_type.in_(("referral_user_bonus","referral_referrer_bonus"))))).all()
    for bonus in bonus_entries:
        _,created=await _entry(session,payment,bonus.beneficiary_user_id,"referral_reversal",-bonus.days_delta,reversed=bonus.id,metadata={"reversed_entry_type":bonus.entry_type})
        if created:
            beneficiary=await session.scalar(select(User).where(User.id==bonus.beneficiary_user_id).with_for_update())
            if beneficiary and beneficiary.subscription_end:
                beneficiary.subscription_end=max(now_utc(),beneficiary.subscription_end-timedelta(days=bonus.days_delta))
                if bonus.entry_type=="referral_referrer_bonus": beneficiary.referral_days=max(0,(beneficiary.referral_days or 0)-bonus.days_delta)
                await SubscriptionService.sync_access_state(session,beneficiary)
    latest=await session.scalar(select(Payment).where(Payment.user_id==payment.user_id,Payment.id!=payment.id,Payment.provider_status=="succeeded",Payment.fulfillment_status=="succeeded").order_by(Payment.fulfilled_at.desc()).limit(1))
    if latest: user.device_limit=latest.snapshot_device_limit or user.device_limit; user.current_tariff_id=latest.tariff_id
    payment.fulfillment_status="reversed"; payment.reversed_at=payment.reversed_at or now_utc(); op.status="succeeded"; op.completed_at=now_utc(); project_legacy_status(payment)
async def execute(session,claim):
    snapshot=await session.get(PaymentFulfillmentOperation,claim.operation_id)
    if not snapshot: raise PaymentFulfillmentOperationOwnershipError(claim.operation_id)
    await session.scalar(select(Payment).where(Payment.id==snapshot.payment_id).with_for_update())
    op=await session.scalar(select(PaymentFulfillmentOperation).where(PaymentFulfillmentOperation.id==claim.operation_id).with_for_update())
    if not op or op.status!="processing" or op.locked_by!=claim.worker_id or op.attempts!=claim.attempt_number: raise PaymentFulfillmentOperationOwnershipError(claim.operation_id)
    if op.operation_type=="grant_subscription": await grant(session,op)
    elif op.operation_type=="grant_referral": await referral(session,op)
    elif op.operation_type=="reverse_payment": await reverse(session,op)
    op.locked_at=op.locked_by=None; await session.flush()
async def finalize_fulfillment_failure(session,claim,*,error_code,retryable=True):
    op=await session.scalar(select(PaymentFulfillmentOperation).where(PaymentFulfillmentOperation.id==claim.operation_id).with_for_update())
    if not op or op.status!="processing" or op.locked_by!=claim.worker_id or op.attempts!=claim.attempt_number: raise PaymentFulfillmentOperationOwnershipError(claim.operation_id)
    payment=await session.scalar(select(Payment).where(Payment.id==op.payment_id).with_for_update()); dead=(not retryable) or op.attempts>=op.max_attempts
    op.status="dead" if dead else "retry"; op.completed_at=now_utc() if dead else None; op.next_attempt_at=now_utc()+timedelta(seconds=min(300,2**min(op.attempts,8))); op.last_error_code=str(error_code)[:100]; op.last_error=None; op.locked_at=op.locked_by=None
    if dead: payment.fulfillment_status="failed"; payment.reconciliation_status="manual_review"; project_legacy_status(payment)
async def retry_dead_fulfillment_operation(session,operation_id,*,reset_attempts,reason):
    op=await session.scalar(select(PaymentFulfillmentOperation).where(PaymentFulfillmentOperation.id==operation_id).with_for_update())
    if not op or op.status!="dead": raise ValueError("operation is not dead")
    if not reset_attempts and op.attempts>=op.max_attempts: raise ValueError("reset_attempts required")
    op.status="retry"; op.completed_at=op.locked_at=op.locked_by=op.last_error_code=op.last_error=None; op.next_attempt_at=now_utc(); op.payload={**op.payload,"admin_retry_reason":str(reason)[:100]}
    if reset_attempts:op.attempts=0
    return op
async def recover_stale(session,lease_seconds=FULFILLMENT_LEASE_SECONDS):
    rows=(await session.scalars(select(PaymentFulfillmentOperation).where(PaymentFulfillmentOperation.status=="processing",PaymentFulfillmentOperation.locked_at<now_utc()-timedelta(seconds=lease_seconds)).with_for_update(skip_locked=True))).all()
    for op in rows:
        dead=op.attempts>=op.max_attempts; op.status="dead" if dead else "retry"; op.completed_at=now_utc() if dead else None; op.locked_at=op.locked_by=None; op.next_attempt_at=now_utc()
        if dead:
            payment=await session.get(Payment,op.payment_id); payment.fulfillment_status="failed"; payment.reconciliation_status="manual_review"; project_legacy_status(payment)
    return len(rows)
