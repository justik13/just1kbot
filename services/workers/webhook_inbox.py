"""Fenced durable YooKassa webhook consumer for balance top-ups."""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from database.models import Payment, PaymentEvent, WebhookInbox
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
    if row is None:
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
    if not claim.payment_external_id:
        return YooKassaResult(
            False,
            error_kind=YooKassaErrorKind.VALIDATION_FAILED,
            retryable=True,
        )
    return await transport.get_payment_result(claim.payment_external_id)


async def _lookup_payment_identity(session, claim):
    row = None
    if claim.payment_external_id:
        row = (
            await session.execute(
                select(Payment.id, Payment.user_id, Payment.external_id)
                .where(Payment.external_id == claim.payment_external_id)
            )
        ).first()
    if row is None and claim.public_order_id:
        row = (
            await session.execute(
                select(Payment.id, Payment.user_id, Payment.external_id)
                .where(Payment.public_order_id == claim.public_order_id)
            )
        ).first()
        if row and claim.payment_external_id and row.external_id != claim.payment_external_id:
            conflict = await session.scalar(
                select(Payment.id).where(
                    Payment.external_id == claim.payment_external_id,
                    Payment.id != row.id,
                )
            )
            if conflict:
                return None, None, "payment_external_id_conflict"
    if row is None:
        return None, None, None
    return row.id, row.user_id, None


async def _manual_refund_review(session, payment, *, reason):
    await place_financial_hold(session, payment=payment, reason=reason)
    session.add(
        PaymentEvent(
            payment_id=payment.id,
            event_type="refund_manual_review",
            provider_status=payment.provider_status,
            reason=reason,
            source="webhook_inbox",
        )
    )


def _schedule_retry(row, *, code: str, seconds: int = 10, force_dead=False):
    dead = force_dead or row.attempts >= row.max_attempts
    row.status = "dead" if dead else "retry"
    row.processed_at = now_utc() if dead else None
    row.last_error_code = code
    row.next_attempt_at = now_utc() + timedelta(seconds=seconds)
    row.locked_at = None
    row.locked_by = None
    return dead


async def finalize(session, claim, result, bot=None):
    # Step 1: Non-locking identity lookup to establish canonical lock targets
    payment_id, user_id, error = await _lookup_payment_identity(session, claim)

    # Step 2: Acquire Advisory and User locks in canonical order L0 -> L1
    if user_id is not None:
        from database.repositories.tariff_quotes_repo import lock_checkout_user
        await lock_checkout_user(session, user_id)

    # Step 3: Acquire Payment lock in canonical order L2
    payment = None
    if payment_id is not None:
        payment = await session.scalar(
            select(Payment).where(Payment.id == payment_id).with_for_update()
        )

    # Step 4: Acquire WebhookInbox lock in canonical order L3
    row = await session.scalar(
        select(WebhookInbox).where(WebhookInbox.id == claim.inbox_id).with_for_update()
    )
    if (
        row is None
        or row.status != "processing"
        or row.locked_by != claim.worker_id
        or row.attempts != claim.attempt_number
    ):
        raise WebhookInboxOwnershipError(claim.inbox_id)

    if payment_id is None:
        # For payment.canceled webhooks, the payment may legitimately not exist
        # in our DB (e.g. created externally, or external_id not yet linked).
        # Retrying 30 times is wasteful — silently succeed to avoid dead queue spam.
        if claim.event_type == "payment.canceled":
            import logging
            logging.getLogger(__name__).info(
                "webhook payment.canceled: payment not found for external_id=%s order=%s — discarding silently",
                claim.payment_external_id,
                claim.public_order_id,
            )
            row.status = "succeeded"
            row.processed_at = now_utc()
            row.locked_at = None
            row.locked_by = None
            await session.flush()
            return
        _schedule_retry(row, code=error or "payment_not_visible", seconds=10)
        return

    if payment is None:
        _schedule_retry(row, code="payment_not_visible", seconds=10)
        return

    if claim.public_order_id and claim.payment_external_id and not payment.external_id:
        payment.external_id = claim.payment_external_id

    if not result or not result.ok:
        code = (
            result.error_kind.value
            if result and result.error_kind
            else "provider_error"
        )
        dead = _schedule_retry(row, code=code, seconds=10)
        if dead:
            if claim.event_type == "refund.succeeded":
                await _manual_refund_review(
                    session,
                    payment,
                    reason="refund_provider_verification_failed",
                )
            else:
                payment.reconciliation_status = "manual_review"
                payment.fulfillment_status = "manual_review"
                payment.manual_review_reason = "payment_provider_verification_failed"
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
                dead = _schedule_retry(
                    row,
                    code="refund_provider_status_not_ready",
                    seconds=10,
                )
                if dead:
                    await _manual_refund_review(
                        session,
                        payment,
                        reason="refund_provider_status_not_ready",
                    )
                return
            if provider_refund.status != "succeeded":
                await _manual_refund_review(
                    session,
                    payment,
                    reason="refund_provider_status_conflict",
                )
            elif (
                webhook_refund.status != "succeeded"
                or provider_refund.refund_id != webhook_refund.refund_id
                or provider_refund.refund_id != row.provider_object_id
                or provider_refund.payment_id != webhook_refund.payment_id
                or provider_refund.payment_id != payment.external_id
            ):
                await _manual_refund_review(
                    session,
                    payment,
                    reason="refund_identity_mismatch",
                )
            elif (
                provider_refund.amount != webhook_refund.amount
                or provider_refund.currency != webhook_refund.currency
                or provider_refund.currency != payment.currency
            ):
                await _manual_refund_review(
                    session,
                    payment,
                    reason="refund_amount_currency_mismatch",
                )
            else:
                try:
                    await apply_balance_topup_refund_success(
                        session,
                        payment=payment,
                        provider_refund_id=provider_refund.refund_id,
                        amount=provider_refund.amount,
                        currency=provider_refund.currency,
                        event_key=claim.event_key,
                    )
                except BalanceRefundError as exc:
                    await _manual_refund_review(session, payment, reason=exc.code)
    else:
        data = result.value if isinstance(result.value, dict) else {}
        observed = str(data.get("status") or "unknown")
        expected = {
            "payment.waiting_for_capture": "waiting_for_capture",
            "payment.succeeded": "succeeded",
            "payment.canceled": "canceled",
        }.get(claim.event_type)
        if expected is None:
            payment.reconciliation_status = "manual_review"
            payment.fulfillment_status = "manual_review"
            payment.manual_review_reason = "unsupported_webhook_event"
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
            _schedule_retry(
                row,
                code="webhook_provider_status_not_ready",
                seconds=10,
            )
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
                _schedule_retry(
                    row,
                    code=transition.reason or "provider_status_not_ready",
                    seconds=10,
                )
                return
            if observed != expected:
                payment.reconciliation_status = "mismatch"
                session.add(
                    PaymentEvent(
                        payment_id=payment.id,
                        event_type="webhook_event_status_conflict",
                        provider_status=observed,
                        reason=f"{claim.event_type}_expected_{expected}",
                        source="webhook_inbox",
                    )
                )
            if transition.outcome == "applied" and observed == "succeeded":
                from services.account_topup import settle_succeeded_topup

                await settle_succeeded_topup(
                    session,
                    payment=payment,
                    source="webhook_inbox",
                    bot=bot,
                )

    row.status = "succeeded"
    row.processed_at = now_utc()
    row.locked_at = None
    row.locked_by = None
    await session.flush()


async def auto_resolve_untracked_canceled_webhooks(session) -> int:
    """Auto-heal dead webhooks where the payment is untracked/missing (payment_not_visible).

    Canceled and refund webhooks for untracked payments are harmlessly discarded.
    Financial payment.succeeded webhooks for untracked payments are preserved for manual review.
    """
    import logging
    logger = logging.getLogger(__name__)

    rows = (
        await session.scalars(
            select(WebhookInbox)
            .where(
                WebhookInbox.status == "dead",
                WebhookInbox.last_error_code == "payment_not_visible",
            )
            .with_for_update(skip_locked=True)
        )
    ).all()
    resolved_count = 0
    for row in rows:
        # Safe to auto-heal: canceled payments and canceled refunds for untracked payments
        if row.event_type in {"payment.canceled", "refund.canceled"}:
            row.status = "succeeded"
            row.processed_at = now_utc()
            row.locked_at = None
            row.locked_by = None
            row.last_error = f"auto_resolved: {row.event_type} for untracked payment"
            resolved_count += 1
        else:
            # Mark as flagged for manual review so we log it ONCE instead of every loop iteration
            row.last_error_code = "payment_not_visible_flagged"
            row.last_error = f"requires_manual_review: {row.event_type} for untracked payment"
            logger.warning(
                "WebhookInbox dead entry requires manual review (untracked payment): id=%s, event_type=%s, external_id=%s",
                row.id,
                row.event_type,
                row.payment_external_id,
            )
    return resolved_count


async def recover_stale(session=None, lease_seconds=WEBHOOK_LEASE_SECONDS):
    """Recover stale webhook items (delegates to isolated per-user transaction handler when session is None)."""
    if session is not None:
        await auto_resolve_untracked_canceled_webhooks(session)
        threshold = now_utc() - timedelta(seconds=lease_seconds)
        stale_rows = (
            await session.execute(
                select(
                    WebhookInbox.id,
                    Payment.id.label("payment_id"),
                    Payment.user_id,
                )
                .outerjoin(Payment, Payment.external_id == WebhookInbox.payment_external_id)
                .where(
                    WebhookInbox.status == "processing",
                    WebhookInbox.locked_at < threshold,
                )
            )
        ).all()
        for wid, _, _ in stale_rows:
            w_row = await session.get(WebhookInbox, wid)
            if w_row and w_row.status == "processing":
                w_row.status = "pending"
                w_row.locked_at = None
                w_row.locked_by = None
        return len(stale_rows)

    return await recover_stale_isolated(lease_seconds=lease_seconds)


async def recover_stale_isolated(lease_seconds=WEBHOOK_LEASE_SECONDS) -> int:
    """Recover stale webhook items using isolated short transactions per user."""
    from database.connection import session_scope

    threshold = now_utc() - timedelta(seconds=lease_seconds)
    async with session_scope() as session:
        await auto_resolve_untracked_canceled_webhooks(session)
        stale_rows = (
            await session.execute(
                select(
                    WebhookInbox.id,
                    Payment.id.label("payment_id"),
                    Payment.user_id,
                )
                .outerjoin(Payment, Payment.external_id == WebhookInbox.payment_external_id)
                .where(
                    WebhookInbox.status == "processing",
                    WebhookInbox.locked_at < threshold,
                )
                .order_by(
                    Payment.user_id.asc().nulls_last(),
                    Payment.id.asc().nulls_last(),
                    WebhookInbox.id.asc(),
                )
                .limit(20)
            )
        ).all()

    count = 0
    for inbox_id, payment_id, user_id in stale_rows:
        async with session_scope() as session:
            if user_id is not None:
                from database.repositories.tariff_quotes_repo import lock_checkout_user
                await lock_checkout_user(session, user_id)
            payment = None
            if payment_id is not None:
                payment = await session.scalar(
                    select(Payment).where(Payment.id == payment_id).with_for_update(skip_locked=True)
                )
            row = await session.scalar(
                select(WebhookInbox)
                .where(
                    WebhookInbox.id == inbox_id,
                    WebhookInbox.status == "processing",
                    WebhookInbox.locked_at < threshold,
                )
                .with_for_update(skip_locked=True)
            )
            if row is None:
                continue
            dead = _schedule_retry(row, code="lease_expired", seconds=0)
            if dead and payment is not None:
                payment.reconciliation_status = "manual_review"
                payment.fulfillment_status = "manual_review"
                payment.manual_review_reason = "webhook_lease_exhausted"
            count += 1
    return count


async def finalize_webhook_failure(session, claim, *, error_code, retryable=True):
    payment_id, user_id, _ = await _lookup_payment_identity(session, claim)
    if user_id is not None:
        from database.repositories.tariff_quotes_repo import lock_checkout_user
        await lock_checkout_user(session, user_id)
    payment = None
    if payment_id is not None:
        payment = await session.scalar(
            select(Payment).where(Payment.id == payment_id).with_for_update()
        )
    row = await session.scalar(
        select(WebhookInbox).where(WebhookInbox.id == claim.inbox_id).with_for_update()
    )
    if (
        row is None
        or row.status != "processing"
        or row.locked_by != claim.worker_id
        or row.attempts != claim.attempt_number
    ):
        raise WebhookInboxOwnershipError(claim.inbox_id)
    dead = _schedule_retry(
        row,
        code=str(error_code)[:100],
        seconds=min(300, 2 ** min(row.attempts, 8)),
        force_dead=not retryable,
    )
    if dead and payment:
        payment.reconciliation_status = "manual_review"
        payment.fulfillment_status = "manual_review"
        payment.manual_review_reason = "webhook_worker_failure"


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
    row.locked_at = None
    row.locked_by = None
    row.processed_at = None
    row.last_error_code = None
    row.last_error = "manual_retry:" + str(reason)[:200]
    return row
