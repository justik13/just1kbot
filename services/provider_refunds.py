"""Durable, fail-closed YooKassa refund lifecycle for balance top-ups."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select

from database.models import (
    AccountBalanceReservation,
    Payment,
    PaymentEvent,
    PaymentRefund,
    User,
)
from database.refund_models import ProviderRefundOperation
from database.repositories.account_ledger_repo import (
    create_payment_debit,
    get_payment_refundable_amount,
    reserve_payment_funds,
    resolve_reservation,
    whole_rubles,
)
from services.audit_service import AuditService
from services.yookassa_service import YooKassaErrorKind, YooKassaResult, YooKassaService
from utils.datetime_helpers import now_utc


REFUND_LEASE_SECONDS = 60
ACTIVE_STATUSES = ("pending", "processing", "retry")


class BalanceRefundError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ProviderRefundOwnershipError(RuntimeError):
    pass


@dataclass(frozen=True)
class BalanceRefundRequest:
    operation: ProviderRefundOperation
    reservation: AccountBalanceReservation
    created: bool


@dataclass(frozen=True)
class ProviderRefundClaim:
    operation_id: int
    payment_id: int
    reservation_id: int
    public_operation_id: uuid.UUID
    idempotency_key: str
    amount: Decimal
    currency: str
    provider_payment_id: str
    provider_refund_id: str | None
    worker_id: str
    attempt_number: int
    created_at: object


async def request_balance_topup_refund(
    session,
    *,
    payment_id: int,
    requested_by_admin_id: int | None,
) -> BalanceRefundRequest:
    """Reserve every currently refundable ruble and enqueue one provider command."""
    payment = await session.scalar(
        select(Payment).where(Payment.id == payment_id).with_for_update()
    )
    if payment is None:
        raise BalanceRefundError("payment_not_found")
    if payment.payment_kind != "balance_topup":
        raise BalanceRefundError("refund_requires_balance_topup")
    if payment.provider_status not in {"succeeded", "refunded"}:
        raise BalanceRefundError("payment_not_refundable")
    if not payment.external_id:
        raise BalanceRefundError("provider_payment_id_missing")
    if payment.currency != "RUB":
        raise BalanceRefundError("refund_currency_invalid")

    active = await session.scalar(
        select(ProviderRefundOperation)
        .where(
            ProviderRefundOperation.payment_id == payment.id,
            ProviderRefundOperation.status.in_(ACTIVE_STATUSES),
        )
        .order_by(ProviderRefundOperation.id.desc())
        .with_for_update()
    )
    if active is not None:
        reservation = await session.get(
            AccountBalanceReservation, active.reservation_id
        )
        if reservation is None or reservation.status != "active":
            raise BalanceRefundError("active_refund_reservation_missing")
        return BalanceRefundRequest(active, reservation, False)

    refundable = whole_rubles(
        await get_payment_refundable_amount(
            session, payment_id=payment.id, for_update=True
        ),
        allow_zero=True,
    )
    if refundable <= 0:
        raise BalanceRefundError("no_refundable_balance")

    public_id = uuid.uuid4()
    idempotency_key = f"refund-{public_id.hex}"
    reservation, _ = await reserve_payment_funds(
        session,
        payment_id=payment.id,
        reservation_type="refund",
        amount=refundable,
        idempotency_key=f"refund-reservation:{public_id.hex}",
        metadata={
            "operation_id": str(public_id),
            "requested_by_admin_id": requested_by_admin_id,
        },
    )
    operation = ProviderRefundOperation(
        operation_id=public_id,
        payment_id=payment.id,
        reservation_id=reservation.id,
        idempotency_key=idempotency_key,
        amount=refundable,
        currency="RUB",
        provider_payment_id=payment.external_id,
        status="pending",
        next_attempt_at=now_utc(),
        requested_by_admin_id=requested_by_admin_id,
    )
    session.add(operation)
    await session.flush()
    session.add(
        PaymentEvent(
            payment_id=payment.id,
            event_type="balance_refund_requested",
            provider_status=payment.provider_status,
            reason="provider_refund_outbox_created",
            source="admin",
            details=(
                f"operation={public_id}; reservation={reservation.id}; "
                f"amount={int(refundable)} RUB"
            ),
        )
    )
    await AuditService.log_action(
        session,
        admin_id=requested_by_admin_id or 0,
        action="BALANCE_REFUND_REQUESTED",
        target_type="Payment",
        target_id=payment.id,
        details=(
            f"operation={public_id}, reservation={reservation.id}, "
            f"amount={int(refundable)} RUB"
        ),
    )
    return BalanceRefundRequest(operation, reservation, True)


async def claim(session, worker_id: str) -> ProviderRefundClaim | None:
    operation = await session.scalar(
        select(ProviderRefundOperation)
        .where(
            ProviderRefundOperation.status.in_(("pending", "retry")),
            ProviderRefundOperation.next_attempt_at <= now_utc(),
            ProviderRefundOperation.attempts
            < ProviderRefundOperation.max_attempts,
        )
        .order_by(ProviderRefundOperation.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if operation is None:
        return None
    operation.status = "processing"
    operation.locked_by = worker_id
    operation.locked_at = now_utc()
    operation.attempts += 1
    await session.flush()
    return ProviderRefundClaim(
        operation.id,
        operation.payment_id,
        operation.reservation_id,
        operation.operation_id,
        operation.idempotency_key,
        Decimal(operation.amount),
        operation.currency,
        operation.provider_payment_id,
        operation.provider_refund_id,
        worker_id,
        operation.attempts,
        operation.created_at,
    )


def _refund_payload(claim: ProviderRefundClaim) -> dict:
    return {
        "payment_id": claim.provider_payment_id,
        "amount": {
            "value": format(claim.amount, ".2f"),
            "currency": claim.currency,
        },
        "description": f"Возврат пополнения #{claim.payment_id}",
    }


async def perform_http(
    claim: ProviderRefundClaim,
    transport=YooKassaService,
) -> YooKassaResult[dict]:
    if claim.provider_refund_id:
        return await transport.get_refund_result(claim.provider_refund_id)
    if now_utc() - claim.created_at >= timedelta(hours=24):
        return YooKassaResult(
            False,
            error_kind=YooKassaErrorKind.IDEMPOTENCY_WINDOW_EXPIRED,
            retryable=False,
            ambiguous=True,
        )
    return await transport.create_refund_result(
        _refund_payload(claim), idempotency_key=claim.idempotency_key
    )


def _parse_provider_refund(data: dict) -> tuple[str, str, Decimal, str, str]:
    refund_id = str(data.get("id") or "")
    payment_id = str(data.get("payment_id") or "")
    status = str(data.get("status") or "")
    amount_obj = data.get("amount") or {}
    currency = str(amount_obj.get("currency") or "")
    try:
        amount = Decimal(str(amount_obj.get("value")))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BalanceRefundError("provider_refund_amount_invalid") from exc
    if not refund_id or not payment_id:
        raise BalanceRefundError("provider_refund_identity_missing")
    if status not in {"pending", "succeeded", "canceled"}:
        raise BalanceRefundError("provider_refund_status_invalid")
    if amount <= 0 or amount != amount.to_integral_value():
        raise BalanceRefundError("provider_refund_amount_invalid")
    return refund_id, payment_id, amount, currency, status


async def _get_or_create_payment_refund(
    session,
    *,
    payment: Payment,
    provider_refund_id: str,
    amount: Decimal,
    currency: str,
    provider_status: str,
    event_key: str,
) -> PaymentRefund:
    refund = await session.scalar(
        select(PaymentRefund)
        .where(PaymentRefund.provider_refund_id == provider_refund_id)
        .with_for_update()
    )
    if refund is None:
        refund = PaymentRefund(
            payment_id=payment.id,
            provider_refund_id=provider_refund_id,
            amount=amount,
            currency=currency,
            provider_status=provider_status,
            event_key=event_key[:64],
            processed_at=now_utc() if provider_status != "pending" else None,
        )
        session.add(refund)
        await session.flush()
    elif (
        refund.payment_id != payment.id
        or Decimal(refund.amount) != amount
        or refund.currency != currency
    ):
        raise BalanceRefundError("provider_refund_idempotency_conflict")
    else:
        refund.provider_status = provider_status
        if provider_status != "pending":
            refund.processed_at = refund.processed_at or now_utc()
    return refund


async def _consume_matching_reservation(
    session,
    *,
    payment_id: int,
    amount: Decimal,
    reservation_id: int | None,
) -> AccountBalanceReservation | None:
    reservation = None
    if reservation_id is not None:
        reservation = await session.scalar(
            select(AccountBalanceReservation)
            .where(AccountBalanceReservation.id == reservation_id)
            .with_for_update()
        )
    if reservation is None:
        reservation = await session.scalar(
            select(AccountBalanceReservation)
            .where(
                AccountBalanceReservation.payment_id == payment_id,
                AccountBalanceReservation.reservation_type == "refund",
                AccountBalanceReservation.amount == amount,
                AccountBalanceReservation.status == "active",
            )
            .order_by(AccountBalanceReservation.id)
            .with_for_update()
        )
    if reservation is not None and reservation.status == "active":
        await resolve_reservation(
            session, reservation_id=reservation.id, outcome="consumed"
        )
    return reservation


async def _update_topup_after_refund(session, payment: Payment) -> Decimal:
    total = Decimal(
        await session.scalar(
            select(func.coalesce(func.sum(PaymentRefund.amount), 0)).where(
                PaymentRefund.payment_id == payment.id,
                PaymentRefund.provider_status == "succeeded",
            )
        )
        or 0
    )
    if total > Decimal(payment.amount):
        raise BalanceRefundError("provider_refund_total_exceeds_payment")
    if total == Decimal(payment.amount):
        payment.provider_status = "refunded"
        payment.fulfillment_status = "reversed"
        payment.reversed_at = payment.reversed_at or now_utc()
    else:
        payment.provider_status = "succeeded"
        payment.fulfillment_status = "succeeded"
    if payment.reconciliation_status not in {"mismatch", "manual_review"}:
        payment.reconciliation_status = "ok"
    if payment.manual_review_reason == "partial_refund":
        payment.manual_review_reason = None
    return total


async def apply_balance_topup_refund_success(
    session,
    *,
    payment: Payment,
    provider_refund_id: str,
    amount: Decimal,
    currency: str,
    event_key: str,
    reservation_id: int | None = None,
    operation: ProviderRefundOperation | None = None,
) -> PaymentRefund:
    """Apply provider truth once; webhook and outbox finalizers share this path."""
    if payment.payment_kind != "balance_topup":
        raise BalanceRefundError("refund_requires_balance_topup")
    amount = whole_rubles(amount)
    if currency != "RUB" or payment.currency != "RUB":
        raise BalanceRefundError("refund_currency_invalid")
    already = Decimal(
        await session.scalar(
            select(func.coalesce(func.sum(PaymentRefund.amount), 0)).where(
                PaymentRefund.payment_id == payment.id,
                PaymentRefund.provider_status == "succeeded",
                PaymentRefund.provider_refund_id != provider_refund_id,
            )
        )
        or 0
    )
    if already + amount > Decimal(payment.amount):
        raise BalanceRefundError("provider_refund_total_exceeds_payment")
    refund = await _get_or_create_payment_refund(
        session,
        payment=payment,
        provider_refund_id=provider_refund_id,
        amount=amount,
        currency=currency,
        provider_status="succeeded",
        event_key=event_key,
    )
    await create_payment_debit(
        session,
        payment_id=payment.id,
        entry_type="refund_debit",
        amount=amount,
        idempotency_key=f"provider-refund-debit:{provider_refund_id}",
        metadata={
            "provider_refund_id": provider_refund_id,
            "event_key": event_key,
            "source": "provider_refund",
        },
    )
    await _consume_matching_reservation(
        session,
        payment_id=payment.id,
        amount=amount,
        reservation_id=reservation_id,
    )
    await _update_topup_after_refund(session, payment)
    if operation is not None:
        operation.provider_refund_id = provider_refund_id
        operation.provider_status = "succeeded"
        operation.status = "completed"
        operation.completed_at = now_utc()
        operation.last_error_code = None
        operation.last_error = None
    session.add(
        PaymentEvent(
            payment_id=payment.id,
            event_type="balance_refund_applied",
            provider_status="succeeded",
            reason="provider_refund_confirmed",
            source="provider_refund",
            details=f"refund={provider_refund_id}; amount={int(amount)} RUB",
        )
    )
    return refund


async def place_financial_hold(
    session,
    *,
    payment: Payment,
    reason: str,
) -> None:
    user = await session.scalar(
        select(User).where(User.id == payment.user_id).with_for_update()
    )
    if user is not None:
        user.financial_hold = True
        user.topup_blocked = True
        user.financial_block_reason = str(reason)[:255]
    payment.reconciliation_status = "manual_review"
    payment.fulfillment_status = "manual_review"
    payment.manual_review_reason = str(reason)[:255]


async def finalize(
    session,
    claim: ProviderRefundClaim,
    result: YooKassaResult[dict],
) -> None:
    payment = await session.scalar(
        select(Payment).where(Payment.id == claim.payment_id).with_for_update()
    )
    operation = await session.scalar(
        select(ProviderRefundOperation)
        .where(ProviderRefundOperation.id == claim.operation_id)
        .with_for_update()
    )
    if (
        operation is None
        or operation.status != "processing"
        or operation.locked_by != claim.worker_id
        or operation.attempts != claim.attempt_number
    ):
        raise ProviderRefundOwnershipError(claim.operation_id)
    if payment is None:
        await place_financial_hold_for_missing_payment(session, operation)
        return

    if result.ok:
        try:
            refund_id, provider_payment_id, amount, currency, status = (
                _parse_provider_refund(result.value or {})
            )
            if (
                provider_payment_id != operation.provider_payment_id
                or amount != Decimal(operation.amount)
                or currency != operation.currency
            ):
                raise BalanceRefundError("provider_refund_economics_mismatch")
        except BalanceRefundError as exc:
            result = YooKassaResult(
                False,
                error_kind=YooKassaErrorKind.INVALID_RESPONSE,
                retryable=True,
                ambiguous=True,
            )
            operation.last_error_code = exc.code
        else:
            operation.provider_refund_id = refund_id
            operation.provider_status = status
            await _get_or_create_payment_refund(
                session,
                payment=payment,
                provider_refund_id=refund_id,
                amount=amount,
                currency=currency,
                provider_status=status,
                event_key=f"provider-operation:{operation.operation_id}",
            )
            if status == "succeeded":
                await apply_balance_topup_refund_success(
                    session,
                    payment=payment,
                    provider_refund_id=refund_id,
                    amount=amount,
                    currency=currency,
                    event_key=f"provider-operation:{operation.operation_id}",
                    reservation_id=operation.reservation_id,
                    operation=operation,
                )
            elif status == "pending":
                operation.status = "retry"
                operation.next_attempt_at = now_utc() + timedelta(seconds=10)
            else:
                await resolve_reservation(
                    session,
                    reservation_id=operation.reservation_id,
                    outcome="released",
                )
                operation.status = "failed"
                operation.completed_at = now_utc()
                operation.last_error_code = "provider_refund_canceled"
                session.add(
                    PaymentEvent(
                        payment_id=payment.id,
                        event_type="balance_refund_failed",
                        provider_status=status,
                        reason="provider_refund_canceled",
                        source="provider_refund",
                    )
                )
    if not result.ok:
        operation.last_error_code = operation.last_error_code or (
            result.error_kind.value if result.error_kind else "provider_refund_error"
        )
        operation.last_error = None
        exhausted = operation.attempts >= operation.max_attempts
        terminal = exhausted or not result.retryable
        operation.status = "failed" if terminal else "retry"
        operation.next_attempt_at = now_utc() + timedelta(
            seconds=min(300, 2 ** min(operation.attempts, 8))
        )
        if terminal:
            operation.completed_at = now_utc()
            if result.ambiguous:
                await place_financial_hold(
                    session,
                    payment=payment,
                    reason="provider_refund_outcome_ambiguous",
                )
            else:
                await resolve_reservation(
                    session,
                    reservation_id=operation.reservation_id,
                    outcome="released",
                )
            session.add(
                PaymentEvent(
                    payment_id=payment.id,
                    event_type="balance_refund_failed",
                    provider_status=payment.provider_status,
                    reason=operation.last_error_code,
                    source="provider_refund",
                )
            )
    operation.locked_at = None
    operation.locked_by = None
    await session.flush()


async def place_financial_hold_for_missing_payment(
    session, operation: ProviderRefundOperation
) -> None:
    operation.status = "failed"
    operation.completed_at = now_utc()
    operation.last_error_code = "refund_payment_missing"
    operation.locked_at = None
    operation.locked_by = None


async def recover_stale(session, lease_seconds=REFUND_LEASE_SECONDS) -> int:
    operations = (
        await session.scalars(
            select(ProviderRefundOperation)
            .where(
                ProviderRefundOperation.status == "processing",
                ProviderRefundOperation.locked_at
                < now_utc() - timedelta(seconds=lease_seconds),
            )
            .with_for_update(skip_locked=True)
        )
    ).all()
    for operation in operations:
        dead = operation.attempts >= operation.max_attempts
        operation.status = "failed" if dead else "retry"
        operation.completed_at = now_utc() if dead else None
        operation.next_attempt_at = now_utc()
        operation.locked_at = None
        operation.locked_by = None
        if dead:
            payment = await session.scalar(
                select(Payment)
                .where(Payment.id == operation.payment_id)
                .with_for_update()
            )
            if payment is not None:
                await place_financial_hold(
                    session,
                    payment=payment,
                    reason="provider_refund_lease_exhausted",
                )
    return len(operations)


async def finalize_provider_failure(
    session,
    claim: ProviderRefundClaim,
    *,
    error_code: str,
    retryable: bool,
) -> None:
    payment = await session.scalar(
        select(Payment).where(Payment.id == claim.payment_id).with_for_update()
    )
    operation = await session.scalar(
        select(ProviderRefundOperation)
        .where(ProviderRefundOperation.id == claim.operation_id)
        .with_for_update()
    )
    if (
        operation is None
        or operation.status != "processing"
        or operation.locked_by != claim.worker_id
        or operation.attempts != claim.attempt_number
    ):
        raise ProviderRefundOwnershipError(claim.operation_id)
    dead = (not retryable) or operation.attempts >= operation.max_attempts
    operation.status = "failed" if dead else "retry"
    operation.completed_at = now_utc() if dead else None
    operation.next_attempt_at = now_utc() + timedelta(
        seconds=min(300, 2 ** min(operation.attempts, 8))
    )
    operation.last_error_code = str(error_code)[:100]
    operation.last_error = None
    operation.locked_at = None
    operation.locked_by = None
    if dead and payment is not None:
        await place_financial_hold(
            session,
            payment=payment,
            reason="provider_refund_worker_failure",
        )
