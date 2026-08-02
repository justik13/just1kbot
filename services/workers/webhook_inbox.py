"""Fenced durable YooKassa webhook consumer."""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from database.models import (
    EntitlementEntry,
    Payment,
    PaymentEvent,
    PaymentFulfillmentOperation,
    PaymentRefund,
    WebhookInbox,
)
from services.payment_kind import is_balance_topup
from services.payment_lifecycle import project_legacy_status
from services.payment_provider_state import apply_provider_transition
from services.payment_queue_timing import WEBHOOK_LEASE_SECONDS
from services.provider_refunds import (
    BalanceRefundError,
    apply_balance_topup_refund_success,
    place_financial_hold,
)
from services.yookassa_service import YooKassaErrorKind, YooKassaResult, YooKassaService
from utils.datetime_helpers import now_utc


class WebhookInboxOwnershipError(RuntimeError):
    pass


@dataclass(frozen=True)
class InboxClaim:
    inbox_id: int
    worker_id: str
    attempt_number: int
    event_type: str
    payment_external_id: str | None
    public_order_id: str | None
    payload: dict
    event_key: str


@dataclass(frozen=True)
class RefundSnapshot:
    refund_id: str
    payment_id: str
    amount: Decimal
    currency: str
    status: str


class RefundSnapshotError(ValueError):
    pass


def _parse_refund_snapshot(data: object) -> RefundSnapshot:
    if not isinstance(data, dict):
        raise RefundSnapshotError("refund_object_invalid")
    amount_obj = data.get("amount")
    if not isinstance(amount_obj, dict):
        raise RefundSnapshotError("refund_amount_invalid")

    refund_id = str(data.get("id") or "")
    payment_id = str(data.get("payment_id") or "")
    currency = str(amount_obj.get("currency") or "")
    status = str(data.get("status") or "")
    try:
        amount = Decimal(str(amount_obj.get("value")))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise RefundSnapshotError("refund_amount_invalid") from exc

    if not refund_id or not payment_id:
        raise RefundSnapshotError("refund_identity_invalid")
    if status not in {"pending", "succeeded", "canceled"}:
        raise RefundSnapshotError("refund_status_invalid")
    if amount <= 0:
        raise RefundSnapshotError("refund_amount_invalid")
    return RefundSnapshot(refund_id, payment_id, amount, currency, status)


async def ensure_fulfillment(session, payment, typ):
    if is_balance_topup(payment):
        return None
    key = {
        "grant_subscription": "payment-grant",
        "reverse_payment": "payment-reverse",
        "grant_referral": "payment-referral",
    }[typ] + f":{payment.id}"
    await session.execute(
        insert(PaymentFulfillmentOperation)
        .values(
            payment_id=payment.id,
            operation_type=typ,
            idempotency_key=key,
            status="pending",
            payload={},
            next_attempt_at=now_utc(),
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
    )


async def claim(session, worker_id):
    row = await session.scalar(
        select(WebhookInbox)
        .where(
            WebhookInbox.status.in_(("pending", "retry")),
            WebhookInbox.next_attempt_at <= now_utc(),
            WebhookInbox.attempts < WebhookInbox.max_attempts,
        )
        .order_by(WebhookInbox.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if not row:
        return None
    row.status = "processing"
    row.locked_by = worker_id
    row.locked_at = now_utc()
    row.attempts += 1
    await session.flush()
    return InboxClaim(
        row.id,
        worker_id,
        row.attempts,
        row.event_type,
        row.payment_external_id,
        row.public_order_id,
        dict(row.payload),
        row.event_key,
    )


async def fetch_provider(claim, transport=YooKassaService):
    # Independently GET-verify the current provider object before any financial
    # effect. The webhook itself is only a durable wake-up signal.
    if claim.event_type == "refund.succeeded":
        obj = claim.payload.get("object") if isinstance(claim.payload, dict) else None
        refund_id = obj.get("id") if isinstance(obj, dict) else None
        if not refund_id:
            return YooKassaResult(
                False,
                error_kind=YooKassaErrorKind.VALIDATION_FAILED,
                retryable=False,
            )
        return await transport.get_refund_result(str(refund_id))
    return await transport.get_payment_result(claim.payment_external_id)


async def _find_payment(session, claim):
    payment = await session.scalar(
        select(Payment)
        .where(Payment.external_id == claim.payment_external_id)
        .with_for_update()
    )
    if not payment and claim.public_order_id:
        payment = await session.scalar(
            select(Payment)
            .where(Payment.public_order_id == claim.public_order_id)
            .with_for_update()
        )
        if payment:
            conflict = await session.scalar(
                select(Payment.id).where(
                    Payment.external_id == claim.payment_external_id,
                    Payment.id != payment.id,
                )
            )
            if conflict:
                return None, "external_id_conflict"
            payment.external_id = claim.payment_external_id
    return payment, None


async def _manual_refund_review(session, payment, *, reason):
    if is_balance_topup(payment):
        await place_financial_hold(session, payment=payment, reason=reason)
    else:
        payment.reconciliation_status = "manual_review"
        payment.fulfillment_status = "manual_review"
        payment.manual_review_reason = reason
    session.add(
        PaymentEvent(
            payment_id=payment.id,
            event_type="refund_manual_review",
            provider_status=payment.provider_status,
            reason=reason,
            source="webhook_inbox",
        )
    )


async def finalize(session, claim, result):
    row = await session.scalar(
        select(WebhookInbox).where(WebhookInbox.id == claim.inbox_id).with_for_update()
    )
    if (
        not row
        or row.status != "processing"
        or row.locked_by != claim.worker_id
        or row.attempts != claim.attempt_number
    ):
        raise WebhookInboxOwnershipError(claim.inbox_id)
    payment, error = await _find_payment(session, claim)
    if not payment:
        dead = row.attempts >= row.max_attempts
        row.status = "dead" if dead else "retry"
        row.processed_at = now_utc() if dead else None
        row.last_error_code = error or "payment_not_visible"
        row.next_attempt_at = now_utc() + timedelta(
            seconds=min(60, 2 ** min(row.attempts, 6))
        )
        row.locked_at = row.locked_by = None
        return
    if (
        result is None
        and claim.event_type == "refund.succeeded"
        and not is_balance_topup(payment)
    ):
        # Compatibility for quote-less legacy rows. The greenfield balance-topup
        # path never trusts webhook data without an independent provider GET.
        result = YooKassaResult(True, value=(claim.payload or {}).get("object"))
    if not result or not result.ok:
        dead = row.attempts >= row.max_attempts
        row.status = "dead" if dead else "retry"
        row.processed_at = now_utc() if dead else None
        row.last_error_code = (
            result.error_kind.value
            if result and result.error_kind
            else "provider_error"
        )
        row.next_attempt_at = now_utc() + timedelta(seconds=10)
        row.locked_at = row.locked_by = None
        if dead:
            if claim.event_type == "refund.succeeded":
                await _manual_refund_review(
                    session, payment, reason="refund_provider_verification_failed"
                )
            else:
                payment.reconciliation_status = "required"
                project_legacy_status(payment)
        return
    if claim.event_type == "refund.succeeded":
        try:
            webhook_refund = _parse_refund_snapshot(
                (claim.payload or {}).get("object")
            )
            provider_refund = _parse_refund_snapshot(result.value)
        except RefundSnapshotError as exc:
            await _manual_refund_review(session, payment, reason=str(exc))
        else:
            if provider_refund.status == "pending":
                dead = row.attempts >= row.max_attempts
                row.status = "dead" if dead else "retry"
                row.processed_at = now_utc() if dead else None
                row.last_error_code = "refund_provider_status_not_ready"
                row.next_attempt_at = now_utc() + timedelta(seconds=10)
                row.locked_at = row.locked_by = None
                if dead:
                    await _manual_refund_review(
                        session, payment, reason="refund_provider_status_not_ready"
                    )
                return
            if provider_refund.status != "succeeded":
                await _manual_refund_review(
                    session, payment, reason="refund_provider_status_conflict"
                )
            elif (
                webhook_refund.status != "succeeded"
                or provider_refund.refund_id != webhook_refund.refund_id
                or provider_refund.refund_id != row.provider_object_id
                or provider_refund.payment_id != webhook_refund.payment_id
                or provider_refund.payment_id != payment.external_id
            ):
                await _manual_refund_review(
                    session, payment, reason="refund_identity_mismatch"
                )
            elif (
                provider_refund.amount != webhook_refund.amount
                or provider_refund.currency != webhook_refund.currency
                or provider_refund.currency != payment.currency
            ):
                await _manual_refund_review(
                    session, payment, reason="refund_amount_currency_mismatch"
                )
            else:
                refund_id = provider_refund.refund_id
                amount = provider_refund.amount
                currency = provider_refund.currency
                if is_balance_topup(payment):
                    try:
                        await apply_balance_topup_refund_success(
                            session,
                            payment=payment,
                            provider_refund_id=str(refund_id),
                            amount=amount,
                            currency=str(currency),
                            event_key=claim.event_key,
                        )
                    except BalanceRefundError as exc:
                        await _manual_refund_review(
                            session,
                            payment,
                            reason=exc.code,
                        )
                else:
                    await session.execute(
                        insert(PaymentRefund)
                        .values(
                            payment_id=payment.id,
                            provider_refund_id=str(refund_id),
                            amount=amount,
                            currency=currency,
                            provider_status="succeeded",
                            event_key=claim.event_key,
                            processed_at=now_utc(),
                        )
                        .on_conflict_do_nothing(index_elements=["provider_refund_id"])
                    )
                    await session.flush()
                    total = Decimal(
                        await session.scalar(
                            select(func.coalesce(func.sum(PaymentRefund.amount), 0)).where(
                                PaymentRefund.payment_id == payment.id,
                                PaymentRefund.provider_status == "succeeded",
                            )
                        )
                        or 0
                    )
                    if total == payment.amount:
                        payment.provider_status = "refunded"
                        reverse_key = f"payment-reverse:{payment.id}"
                        reverse_op = await session.scalar(
                            select(PaymentFulfillmentOperation)
                            .where(PaymentFulfillmentOperation.idempotency_key == reverse_key)
                            .with_for_update()
                        )
                        reversal_entry = await session.scalar(
                            select(EntitlementEntry.id).where(
                                EntitlementEntry.source_type == "payment",
                                EntitlementEntry.source_id == str(payment.id),
                                EntitlementEntry.entry_type == "payment_reversal",
                            )
                        )
                        reversal_done = (
                            payment.fulfillment_status == "reversed"
                            or (reverse_op is not None and reverse_op.status == "succeeded")
                            or reversal_entry is not None
                        )
                        if reversal_done:
                            payment.fulfillment_status = "reversed"
                        elif reverse_op is not None and reverse_op.status in {
                            "pending",
                            "retry",
                            "processing",
                        }:
                            payment.fulfillment_status = "reversal_pending"
                        elif reverse_op is not None and reverse_op.status in {
                            "dead",
                            "cancelled",
                        }:
                            payment.fulfillment_status = "manual_review"
                            payment.reconciliation_status = "manual_review"
                            payment.fulfillment_last_error_code = (
                                "reverse_operation_not_runnable"
                            )
                        else:
                            payment.fulfillment_status = "reversal_pending"
                            await ensure_fulfillment(session, payment, "reverse_payment")
                        if (
                            payment.manual_review_reason == "partial_refund"
                            and payment.fulfillment_status != "manual_review"
                        ):
                            payment.reconciliation_status = "ok"
                            payment.manual_review_reason = None
                        pending = (
                            await session.scalars(
                                select(PaymentFulfillmentOperation)
                                .where(
                                    PaymentFulfillmentOperation.payment_id == payment.id,
                                    PaymentFulfillmentOperation.operation_type.in_(
                                        ("grant_subscription", "grant_referral")
                                    ),
                                    PaymentFulfillmentOperation.status.in_(
                                        ("pending", "retry")
                                    ),
                                )
                                .with_for_update()
                            )
                        ).all()
                        for queued in pending:
                            queued.status = "cancelled"
                            queued.completed_at = now_utc()
                    elif total < payment.amount:
                        if payment.reconciliation_status not in {
                            "mismatch",
                            "manual_review",
                        } or payment.manual_review_reason in {None, "partial_refund"}:
                            payment.reconciliation_status = "manual_review"
                            payment.manual_review_reason = "partial_refund"
                        payment.fulfillment_status = "manual_review"
                        session.add(
                            PaymentEvent(
                                payment_id=payment.id,
                                event_type="partial_refund_recorded",
                                provider_status=payment.provider_status,
                                reason="partial_refund",
                                source="webhook_inbox",
                            )
                        )
                        grants = (
                            await session.scalars(
                                select(PaymentFulfillmentOperation)
                                .where(
                                    PaymentFulfillmentOperation.payment_id == payment.id,
                                    PaymentFulfillmentOperation.operation_type
                                    == "grant_subscription",
                                    PaymentFulfillmentOperation.status.in_(
                                        ("pending", "retry")
                                    ),
                                )
                                .with_for_update()
                            )
                        ).all()
                        for grant_op in grants:
                            grant_op.status = "cancelled"
                            grant_op.completed_at = now_utc()
                    else:
                        await _manual_refund_review(
                            session, payment, reason="over_refund"
                        )
    else:
        data = result.value or {}
        observed = str(data.get("status") or "unknown")
        expected = {
            "payment.waiting_for_capture": "waiting_for_capture",
            "payment.succeeded": "succeeded",
            "payment.canceled": "canceled",
        }.get(claim.event_type)
        if expected is None:
            payment.reconciliation_status = "manual_review"
            payment.fulfillment_status = "manual_review"
            session.add(
                PaymentEvent(
                    payment_id=payment.id,
                    event_type="unsupported_webhook_event",
                    provider_status=observed,
                    reason=claim.event_type,
                    source="webhook_inbox",
                )
            )
        elif observed != expected and observed in {"pending", "waiting_for_capture"}:
            dead = row.attempts >= row.max_attempts
            row.status = "dead" if dead else "retry"
            row.processed_at = now_utc() if dead else None
            row.last_error_code = "webhook_provider_status_not_ready"
            row.next_attempt_at = now_utc() + timedelta(seconds=10)
            row.locked_at = row.locked_by = None
            return
        else:
            transition = await apply_provider_transition(
                session,
                payment,
                data,
                source="webhook_inbox",
                event_type=claim.event_type,
            )
            if transition.outcome == "retry":
                dead = row.attempts >= row.max_attempts
                row.status = "dead" if dead else "retry"
                row.processed_at = now_utc() if dead else None
                row.last_error_code = transition.reason
                row.next_attempt_at = now_utc() + timedelta(seconds=10)
                row.locked_at = row.locked_by = None
                return
            if observed != expected:
                payment.reconciliation_status = "mismatch"
                if (
                    is_balance_topup(payment)
                    and observed == "succeeded"
                    and transition.outcome == "applied"
                    and payment.fulfillment_status
                    not in {"manual_review", "reversed"}
                ):
                    from services.account_topup import settle_succeeded_topup

                    await settle_succeeded_topup(
                        session,
                        payment=payment,
                        source="webhook_status_conflict",
                    )
                    payment.reconciliation_status = "mismatch"
                else:
                    payment.fulfillment_status = (
                        "manual_review"
                        if payment.fulfillment_status != "reversed"
                        else payment.fulfillment_status
                    )
                session.add(
                    PaymentEvent(
                        payment_id=payment.id,
                        event_type="webhook_event_status_conflict",
                        provider_status=observed,
                        reason=f"{claim.event_type}_expected_{expected}",
                        source="webhook_inbox",
                    )
                )
            elif (
                is_balance_topup(payment)
                and observed == "succeeded"
                and transition.outcome == "applied"
                and payment.fulfillment_status not in {"manual_review", "reversed"}
            ):
                from services.account_topup import settle_succeeded_topup

                await settle_succeeded_topup(
                    session,
                    payment=payment,
                    source="webhook_inbox",
                )
            elif transition.grant_allowed:
                payment.fulfillment_status = "pending"
                await ensure_fulfillment(session, payment, "grant_subscription")
            elif transition.reason == "paid_after_cancel":
                queued = (
                    await session.scalars(
                        select(PaymentFulfillmentOperation)
                        .where(
                            PaymentFulfillmentOperation.payment_id == payment.id,
                            PaymentFulfillmentOperation.operation_type.in_(
                                ("grant_subscription", "grant_referral")
                            ),
                            PaymentFulfillmentOperation.status.in_(
                                ("pending", "retry")
                            ),
                        )
                        .with_for_update()
                    )
                ).all()
                for operation in queued:
                    operation.status = "cancelled"
                    operation.completed_at = now_utc()

    project_legacy_status(payment)
    row.status = "succeeded"
    row.processed_at = now_utc()
    row.locked_at = row.locked_by = None
    await session.flush()


async def recover_stale(session, lease_seconds=WEBHOOK_LEASE_SECONDS):
    rows = (
        await session.scalars(
            select(WebhookInbox)
            .where(
                WebhookInbox.status == "processing",
                WebhookInbox.locked_at < now_utc() - timedelta(seconds=lease_seconds),
            )
            .with_for_update(skip_locked=True)
        )
    ).all()
    for row in rows:
        dead = row.attempts >= row.max_attempts
        row.status = "dead" if dead else "retry"
        row.processed_at = now_utc() if dead else None
        row.locked_at = row.locked_by = None
        row.next_attempt_at = now_utc()
        if dead:
            payment = await session.scalar(
                select(Payment)
                .where(Payment.external_id == row.payment_external_id)
                .with_for_update()
            )
            if payment:
                payment.reconciliation_status = "required"
                project_legacy_status(payment)
    return len(rows)


async def finalize_webhook_failure(session, claim, *, error_code, retryable=True):
    row = await session.scalar(
        select(WebhookInbox).where(WebhookInbox.id == claim.inbox_id).with_for_update()
    )
    if (
        not row
        or row.status != "processing"
        or row.locked_by != claim.worker_id
        or row.attempts != claim.attempt_number
    ):
        raise WebhookInboxOwnershipError(claim.inbox_id)
    dead = (not retryable) or row.attempts >= row.max_attempts
    row.status = "dead" if dead else "retry"
    row.processed_at = now_utc() if dead else None
    row.next_attempt_at = now_utc() + timedelta(
        seconds=min(300, 2 ** min(row.attempts, 8))
    )
    row.last_error_code = str(error_code)[:100]
    row.last_error = None
    row.locked_at = row.locked_by = None
    if dead:
        payment = await session.scalar(
            select(Payment)
            .where(Payment.external_id == row.payment_external_id)
            .with_for_update()
        )
        if payment:
            payment.reconciliation_status = "required"
            project_legacy_status(payment)


async def retry_dead_webhook_operation(session, inbox_id, *, reset_attempts, reason):
    row = await session.scalar(
        select(WebhookInbox).where(WebhookInbox.id == inbox_id).with_for_update()
    )
    if not row or row.status != "dead":
        raise ValueError("webhook operation is not dead")
    if not reset_attempts and row.attempts >= row.max_attempts:
        raise ValueError("reset_attempts required")
    if reset_attempts:
        row.attempts = 0
    row.status = "retry"
    row.next_attempt_at = now_utc()
    row.locked_at = row.locked_by = None
    row.processed_at = None
    row.last_error_code = None
    row.last_error = "manual_retry:" + str(reason)[:200]
    return row
