"""The single monotonic provider-state transition boundary.

Lock order for payment pipeline transactions is always Payment, then queue row,
then User/entitlement rows. Callers that initially claim a queue row must release
that claim transaction before entering this transition/finalization transaction.
"""
from dataclasses import dataclass
from sqlalchemy import select
from database.models import PaymentEvent, PaymentFulfillmentOperation
from services.payment_provider_validation import validate_provider_payment
from utils.datetime_helpers import now_utc

@dataclass(frozen=True)
class ProviderTransition:
    outcome: str  # applied, conflict, retry
    observed_status: str
    grant_allowed: bool = False
    reason: str | None = None

async def apply_provider_transition(session,payment,data,*,source,event_type=None):
    observed=str((data or {}).get("status") or "unknown")
    current=payment.provider_status
    if observed not in {"pending","waiting_for_capture","succeeded","canceled"}:
        return ProviderTransition("retry",observed,reason="unknown_provider_status")
    if observed=="succeeded":
        # A successful provider snapshot is authoritative financial evidence.  Record
        # it before identity validation so a bad command correlation cannot erase the
        # fact that money was received.
        payment.paid_at=payment.paid_at or now_utc(); payment.provider_confirmed_at=payment.provider_confirmed_at or now_utc()
        terminal_reversal=current=="refunded" or payment.fulfillment_status=="reversed"
        if not terminal_reversal: payment.provider_status="succeeded"
        mismatch=validate_provider_payment(payment,data)
        if mismatch:
            payment.reconciliation_status="mismatch"
            if not terminal_reversal: payment.fulfillment_status="manual_review"
            queued=(await session.scalars(select(PaymentFulfillmentOperation).where(PaymentFulfillmentOperation.payment_id==payment.id,PaymentFulfillmentOperation.operation_type.in_(("grant_subscription","grant_referral")),PaymentFulfillmentOperation.status.in_(("pending","retry"))).with_for_update())).all()
            for operation in queued:
                operation.status="cancelled"; operation.completed_at=now_utc()
            session.add(PaymentEvent(payment_id=payment.id,event_type="provider_snapshot_mismatch",provider_status=observed,reason=mismatch,source=source))
            return ProviderTransition("conflict",observed,reason=mismatch)
        if terminal_reversal:
            payment.reconciliation_status="mismatch"
            session.add(PaymentEvent(payment_id=payment.id,event_type="provider_transition_conflict",provider_status=observed,reason=f"{current}_to_succeeded",source=source))
            return ProviderTransition("conflict",observed,reason="succeeded_after_refund")
        if current=="canceled" or payment.checkout_status=="abandoned" or source=="provider_cancel_payment":
            payment.reconciliation_status="mismatch"; payment.fulfillment_status="manual_review"
            session.add(PaymentEvent(payment_id=payment.id,event_type="paid_after_cancel",provider_status=observed,reason=f"{current}_to_succeeded",source=source))
            return ProviderTransition("conflict",observed,reason="paid_after_cancel")
        return ProviderTransition("applied",observed,grant_allowed=payment.fulfillment_status not in {"succeeded","reversed","manual_review"})
    if current in {"succeeded","refunded"} or payment.fulfillment_status=="reversed":
        if observed!=current:
            payment.reconciliation_status="mismatch"; payment.fulfillment_status="manual_review" if payment.fulfillment_status!="reversed" else payment.fulfillment_status
            session.add(PaymentEvent(payment_id=payment.id,event_type="provider_transition_conflict",provider_status=observed,reason=f"{current}_to_{observed}",source=source))
            return ProviderTransition("conflict",observed,reason="terminal_regression")
        return ProviderTransition("applied",observed)
    if current=="canceled" and observed in {"pending","waiting_for_capture"}:
        payment.reconciliation_status="mismatch"; session.add(PaymentEvent(payment_id=payment.id,event_type="provider_transition_conflict",provider_status=observed,reason=f"canceled_to_{observed}",source=source)); return ProviderTransition("conflict",observed,reason="terminal_regression")
    payment.provider_status=observed
    return ProviderTransition("applied",observed)
