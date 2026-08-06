"""Fenced PostgreSQL queue for YooKassa top-up provider commands."""

from dataclasses import dataclass
from datetime import timedelta
import uuid

from sqlalchemy import select

from database.models import Payment, PaymentEvent, PaymentProviderOperation
from services.payment_provider_state import apply_provider_transition
from services.payment_queue_timing import PROVIDER_LEASE_SECONDS
from services.yookassa_service import YooKassaErrorKind, YooKassaResult, YooKassaService
from utils.datetime_helpers import now_utc


class PaymentProviderOperationOwnershipError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderOperationClaim:
    operation_id: int
    payment_id: int
    operation_type: str
    payload: dict
    idempotency_key: str
    worker_id: str
    attempt_number: int
    external_id: str | None
    created_at: object


@dataclass(frozen=True)
class ProviderRetryDecision:
    accepted: bool
    reason: str
    operation_id: int


VALID_PROVIDER_STATUSES = {"pending", "waiting_for_capture", "succeeded", "canceled"}


def provider_transition_source(claim):
    if claim.operation_type == "create_payment":
        return "provider_get_payment" if claim.external_id else "provider_create_payment_post"
    return "provider_reconcile_payment_get"


def create_payload(payment, description, return_url):
    return {
        "amount": {
            "value": format(payment.amount, ".2f"),
            "currency": payment.currency,
        },
        "description": description,
        "confirmation": {"type": "redirect", "return_url": return_url},
        "metadata": {
            "order_id": payment.public_order_id,
            "local_payment_id": str(payment.id),
        },
        "capture": True,
    }


async def enqueue_create(session, payment, description, return_url):
    operation = PaymentProviderOperation(
        payment_id=payment.id,
        operation_type="create_payment",
        status="pending",
        idempotency_key=payment.provider_idempotency_key,
        payload=create_payload(payment, description, return_url),
        next_attempt_at=now_utc(),
    )
    session.add(operation)
    await session.flush()
    return operation


async def ensure_reconcile_payment_operation(session, payment, *, reason):
    payment = await session.scalar(
        select(Payment).where(Payment.id == payment.id).with_for_update()
    )
    if payment is None or not payment.external_id:
        return None
    active = await session.scalar(
        select(PaymentProviderOperation)
        .where(
            PaymentProviderOperation.payment_id == payment.id,
            PaymentProviderOperation.operation_type == "reconcile_payment",
            PaymentProviderOperation.status.in_(("pending", "retry", "processing")),
        )
        .order_by(PaymentProviderOperation.id.desc())
        .with_for_update()
    )
    if active:
        return active
    operation = PaymentProviderOperation(
        payment_id=payment.id,
        operation_type="reconcile_payment",
        status="pending",
        idempotency_key=f"payment-reconcile:{payment.id}:{uuid.uuid4().hex}",
        payload={
            "provider_payment_id": payment.external_id,
            "reason": str(reason)[:100],
        },
        next_attempt_at=now_utc(),
    )
    session.add(operation)
    await session.flush()
    return operation


async def retry_dead_provider_operation(
    session, operation_id, *, reset_attempts, reason
):
    operation = await session.scalar(
        select(PaymentProviderOperation)
        .where(PaymentProviderOperation.id == operation_id)
        .with_for_update()
    )
    if not operation or operation.status != "dead":
        raise ValueError("operation is not dead")
    payment = await session.scalar(
        select(Payment).where(Payment.id == operation.payment_id).with_for_update()
    )
    if payment is None:
        raise ValueError("payment not found")
    if (
        operation.operation_type == "create_payment"
        and not payment.external_id
        and now_utc() - operation.created_at >= timedelta(hours=24)
    ):
        payment.reconciliation_status = "manual_review"
        payment.fulfillment_status = "manual_review"
        payment.manual_review_reason = "create_idempotency_window_expired"
        session.add(
            PaymentEvent(
                payment_id=payment.id,
                event_type="provider_operation_admin_retry_rejected",
                provider_status=payment.provider_status,
                reason="create_idempotency_window_expired",
                source="admin_retry",
            )
        )
        return ProviderRetryDecision(False, "create_idempotency_window_expired", operation.id)
    if not reset_attempts and operation.attempts >= operation.max_attempts:
        raise ValueError("reset_attempts required for exhausted operation")
    session.add(
        PaymentEvent(
            payment_id=payment.id,
            event_type="provider_operation_admin_retry",
            provider_status=payment.provider_status,
            reason=str(reason)[:255],
            source="admin_retry",
        )
    )
    operation.status = "retry"
    operation.completed_at = None
    operation.locked_at = None
    operation.locked_by = None
    operation.last_error_code = None
    operation.last_error = None
    operation.next_attempt_at = now_utc()
    if reset_attempts:
        operation.attempts = 0
    return ProviderRetryDecision(True, "retry_scheduled", operation.id)


async def claim(session, worker_id):
    operation = await session.scalar(
        select(PaymentProviderOperation)
        .where(
            PaymentProviderOperation.status.in_(("pending", "retry")),
            PaymentProviderOperation.next_attempt_at <= now_utc(),
            PaymentProviderOperation.attempts < PaymentProviderOperation.max_attempts,
        )
        .order_by(PaymentProviderOperation.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if operation is None:
        return None
    operation.status = "processing"
    operation.locked_by = worker_id
    operation.locked_at = now_utc()
    operation.attempts += 1
    payment = await session.get(Payment, operation.payment_id)
    await session.flush()
    return ProviderOperationClaim(
        operation.id,
        operation.payment_id,
        operation.operation_type,
        dict(operation.payload),
        operation.idempotency_key,
        worker_id,
        operation.attempts,
        payment.external_id if payment else None,
        operation.created_at,
    )


async def perform_http(claim, transport=YooKassaService):
    if claim.operation_type == "create_payment":
        if claim.external_id:
            return await transport.get_payment_result(claim.external_id)
        if now_utc() - claim.created_at >= timedelta(hours=24):
            return YooKassaResult(
                False,
                error_kind=YooKassaErrorKind.IDEMPOTENCY_WINDOW_EXPIRED,
                retryable=False,
                ambiguous=True,
            )
        return await transport.create_payment_result(
            claim.payload, idempotency_key=claim.idempotency_key
        )
    if claim.operation_type == "reconcile_payment":
        provider_id = claim.payload.get("provider_payment_id") or claim.external_id
        if not provider_id:
            return YooKassaResult(
                False,
                error_kind=YooKassaErrorKind.VALIDATION_FAILED,
                retryable=False,
            )
        return await transport.get_payment_result(provider_id)
    return YooKassaResult(
        False,
        error_kind=YooKassaErrorKind.VALIDATION_FAILED,
        retryable=False,
    )


def _retry_delay(attempts: int) -> timedelta:
    return timedelta(seconds=min(300, 2 ** min(attempts, 8)))


async def finalize(session, claim, result):
    payment = await session.scalar(
        select(Payment).where(Payment.id == claim.payment_id).with_for_update()
    )
    operation = await session.scalar(
        select(PaymentProviderOperation)
        .where(PaymentProviderOperation.id == claim.operation_id)
        .with_for_update()
    )
    if (
        payment is None
        or operation is None
        or operation.status != "processing"
        or operation.locked_by != claim.worker_id
        or operation.attempts != claim.attempt_number
    ):
        raise PaymentProviderOperationOwnershipError(claim.operation_id)

    if result and result.ok:
        data = result.value if isinstance(result.value, dict) else {}
        status = str(data.get("status") or "")
        provider_id = str(data.get("id") or "")
        if status not in VALID_PROVIDER_STATUSES or not provider_id:
            result = YooKassaResult(
                False,
                error_kind=YooKassaErrorKind.INVALID_RESPONSE,
                retryable=True,
                ambiguous=claim.operation_type == "create_payment" and not claim.external_id,
            )
        else:
            if payment.external_id is None:
                payment.external_id = provider_id
            elif payment.external_id != provider_id:
                payment.reconciliation_status = "mismatch"
                payment.fulfillment_status = "manual_review"
                payment.manual_review_reason = "external_id_mismatch"
                result = YooKassaResult(
                    False,
                    error_kind=YooKassaErrorKind.INVALID_RESPONSE,
                    retryable=False,
                )

            if result.ok:
                if claim.operation_type == "create_payment":
                    confirmation = data.get("confirmation") or {}
                    payment.payment_url = (
                        confirmation.get("confirmation_url")
                        or confirmation.get("url")
                        or payment.payment_url
                    )
                    if status in {"pending", "waiting_for_capture"} and not payment.payment_url:
                        result = YooKassaResult(
                            False,
                            error_kind=YooKassaErrorKind.INVALID_RESPONSE,
                            retryable=True,
                            ambiguous=False,
                        )
            if result.ok:
                transition = await apply_provider_transition(
                    session,
                    payment,
                    data,
                    source=provider_transition_source(claim),
                )
                if transition.outcome == "retry":
                    result = YooKassaResult(
                        False,
                        error_kind=YooKassaErrorKind.INVALID_RESPONSE,
                        retryable=True,
                        ambiguous=False,
                    )
                elif transition.outcome == "applied" and status == "succeeded":
                    from services.account_topup import settle_succeeded_topup

                    await settle_succeeded_topup(
                        session,
                        payment=payment,
                        source=provider_transition_source(claim),
                    )

    if result and result.ok:
        operation.status = "succeeded"
        operation.completed_at = now_utc()
        operation.last_error_code = None
        payment.reconciliation_status = (
            payment.reconciliation_status
            if payment.reconciliation_status in {"mismatch", "manual_review"}
            else "ok"
        )
    else:
        retryable = bool(result and result.retryable)
        ambiguous = bool(result and result.ambiguous)
        exhausted = operation.attempts >= operation.max_attempts
        operation.status = "dead" if exhausted or not retryable else "retry"
        operation.completed_at = now_utc() if operation.status == "dead" else None
        operation.next_attempt_at = now_utc() + _retry_delay(operation.attempts)
        operation.last_error_code = (
            result.error_kind.value
            if result and result.error_kind
            else "provider_error"
        )
        operation.last_error = None
        if payment.provider_status in {"succeeded", "refunded", "canceled"}:
            payment.reconciliation_status = (
                "manual_review" if operation.status == "dead" else "required"
            )
        elif operation.status == "dead":
            payment.provider_status = "manual_review"
            payment.reconciliation_status = "manual_review"
            payment.fulfillment_status = "manual_review"
            payment.manual_review_reason = operation.last_error_code
        else:
            payment.provider_status = "unknown" if ambiguous else payment.provider_status
            payment.reconciliation_status = "required"
    operation.locked_at = None
    operation.locked_by = None
    await session.flush()


async def recover_stale(session, lease_seconds=PROVIDER_LEASE_SECONDS):
    operations = (
        await session.scalars(
            select(PaymentProviderOperation)
            .where(
                PaymentProviderOperation.status == "processing",
                PaymentProviderOperation.locked_at
                < now_utc() - timedelta(seconds=lease_seconds),
            )
            .with_for_update(skip_locked=True)
        )
    ).all()
    for operation in operations:
        dead = operation.attempts >= operation.max_attempts
        operation.status = "dead" if dead else "retry"
        operation.completed_at = now_utc() if dead else None
        operation.locked_at = None
        operation.locked_by = None
        operation.next_attempt_at = now_utc()
        payment = await session.get(Payment, operation.payment_id)
        if payment is None:
            continue
        if payment.provider_status in {"succeeded", "refunded", "canceled"}:
            payment.reconciliation_status = "manual_review" if dead else "required"
        elif dead:
            payment.provider_status = "manual_review"
            payment.reconciliation_status = "manual_review"
            payment.fulfillment_status = "manual_review"
        else:
            payment.reconciliation_status = "required"
    return len(operations)


async def finalize_provider_failure(session, claim, *, error_code, retryable):
    payment = await session.scalar(
        select(Payment).where(Payment.id == claim.payment_id).with_for_update()
    )
    operation = await session.scalar(
        select(PaymentProviderOperation)
        .where(PaymentProviderOperation.id == claim.operation_id)
        .with_for_update()
    )
    if (
        payment is None
        or operation is None
        or operation.status != "processing"
        or operation.locked_by != claim.worker_id
        or operation.attempts != claim.attempt_number
    ):
        raise PaymentProviderOperationOwnershipError(claim.operation_id)
    dead = (not retryable) or operation.attempts >= operation.max_attempts
    operation.status = "dead" if dead else "retry"
    operation.completed_at = now_utc() if dead else None
    operation.next_attempt_at = now_utc() + _retry_delay(operation.attempts)
    operation.last_error_code = str(error_code)[:100]
    operation.last_error = None
    operation.locked_at = None
    operation.locked_by = None
    if payment.provider_status in {"succeeded", "refunded", "canceled"}:
        payment.reconciliation_status = "manual_review" if dead else "required"
    elif dead:
        payment.provider_status = "manual_review"
        payment.reconciliation_status = "manual_review"
        payment.fulfillment_status = "manual_review"
        payment.manual_review_reason = str(error_code)[:100]
    else:
        payment.reconciliation_status = "required"
    return operation
