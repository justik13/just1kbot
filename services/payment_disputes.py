"""Manual, append-only-account-safe lifecycle for payment disputes."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select

from database.dispute_models import PaymentDispute
from database.models import (
    AccountBalanceReservation,
    AccountLedgerEntry,
    Payment,
    PaymentEvent,
    User,
)
from database.repositories.account_ledger_repo import (
    create_payment_debit,
    get_account_balance,
    get_payment_refundable_amount,
    reserve_payment_funds,
    resolve_reservation,
    whole_rubles,
)
from services.audit_service import AuditService
from utils.datetime_helpers import now_utc


ACTIVE_DISPUTE_STATUSES = ("open", "manual_review")
DISPUTE_HOLD_REASONS = {"open_payment_dispute", "chargeback_debt"}


class PaymentDisputeError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PaymentDisputeResult:
    dispute: PaymentDispute
    created: bool


def _idempotency_key(provider_case_id: str) -> str:
    digest = hashlib.sha256(provider_case_id.strip().encode()).hexdigest()
    return f"payment-dispute:{digest}"


async def _remaining_payment_exposure(session, payment: Payment) -> Decimal:
    removed = abs(
        Decimal(
            await session.scalar(
                select(func.coalesce(func.sum(AccountLedgerEntry.amount), 0)).where(
                    AccountLedgerEntry.payment_id == payment.id,
                    AccountLedgerEntry.entry_type.in_(
                        ("refund_debit", "chargeback_debit")
                    ),
                )
            )
            or 0
        )
    )
    return max(Decimal("0"), Decimal(payment.amount) - removed)


async def refresh_user_dispute_hold(session, *, user_id: int) -> None:
    """Keep dispute holds until all disputes close and chargeback debt is repaid."""
    user = await session.scalar(
        select(User).where(User.id == user_id).with_for_update()
    )
    if user is None:
        return
    active = bool(
        await session.scalar(
            select(PaymentDispute.id).where(
                PaymentDispute.user_id == user.id,
                PaymentDispute.status.in_(ACTIVE_DISPUTE_STATUSES),
            )
        )
    )
    balance = await get_account_balance(session, user_id=user.id)
    if active:
        user.financial_hold = True
        user.financial_block_reason = "open_payment_dispute"
    elif balance.debt > 0:
        user.financial_hold = True
        user.financial_block_reason = "chargeback_debt"
        from services.subscription import SubscriptionService
        await SubscriptionService._sync_access_state(session, user)
    elif user.financial_block_reason in DISPUTE_HOLD_REASONS:
        user.financial_hold = False
        user.financial_block_reason = None


async def open_payment_dispute(
    session,
    *,
    provider_payment_id: str,
    provider_case_id: str,
    amount: object,
    disputed_at: datetime,
    note: str | None,
    admin_id: int,
) -> PaymentDisputeResult:
    provider_payment_id = provider_payment_id.strip()
    provider_case_id = provider_case_id.strip()
    if not provider_payment_id:
        raise PaymentDisputeError("provider_payment_id_required")
    if not provider_case_id:
        raise PaymentDisputeError("provider_case_id_required")
    if disputed_at.tzinfo is None or disputed_at.utcoffset() is None:
        raise PaymentDisputeError("disputed_at_timezone_required")
    try:
        rubles = whole_rubles(amount)
    except ValueError as exc:
        raise PaymentDisputeError("dispute_amount_invalid") from exc

    payment = await session.scalar(
        select(Payment)
        .where(Payment.external_id == provider_payment_id)
        .with_for_update()
    )
    if payment is None:
        raise PaymentDisputeError("payment_not_found")
    if payment.currency != "RUB":
        raise PaymentDisputeError("dispute_requires_balance_topup")
    if payment.provider_status not in {"succeeded", "refunded"}:
        raise PaymentDisputeError("payment_not_settled")
    if payment.credited_at is None:
        raise PaymentDisputeError("payment_not_credited")

    existing = await session.scalar(
        select(PaymentDispute)
        .where(PaymentDispute.provider_case_id == provider_case_id)
        .with_for_update()
    )
    if existing is not None:
        if (
            existing.payment_id != payment.id
            or Decimal(existing.amount) != rubles
            or existing.disputed_at != disputed_at
        ):
            raise PaymentDisputeError("provider_case_id_conflict")
        return PaymentDisputeResult(existing, False)

    active = await session.scalar(
        select(PaymentDispute.id).where(
            PaymentDispute.payment_id == payment.id,
            PaymentDispute.status.in_(ACTIVE_DISPUTE_STATUSES),
        )
    )
    if active is not None:
        raise PaymentDisputeError("payment_has_active_dispute")
    active_refund = await session.scalar(
        select(AccountBalanceReservation.id).where(
            AccountBalanceReservation.payment_id == payment.id,
            AccountBalanceReservation.reservation_type == "refund",
            AccountBalanceReservation.status == "active",
        )
    )
    if active_refund is not None:
        raise PaymentDisputeError("refund_in_progress")

    remaining = await _remaining_payment_exposure(session, payment)
    if rubles > remaining:
        raise PaymentDisputeError("dispute_exceeds_payment_exposure")
    refundable = await get_payment_refundable_amount(
        session, payment_id=payment.id, for_update=True
    )
    balance = await get_account_balance(session, user_id=payment.user_id)
    reserve_amount = min(rubles, refundable, balance.available)
    reservation = None
    if reserve_amount > 0:
        reservation, _ = await reserve_payment_funds(
            session,
            payment_id=payment.id,
            reservation_type="dispute",
            amount=reserve_amount,
            idempotency_key=f"dispute-reservation:{_idempotency_key(provider_case_id)}",
            metadata={
                "provider_case_id": provider_case_id,
                "disputed_amount_rub": str(rubles),
            },
        )

    dispute = PaymentDispute(
        payment_id=payment.id,
        user_id=payment.user_id,
        provider_case_id=provider_case_id,
        idempotency_key=_idempotency_key(provider_case_id),
        status="open",
        amount=rubles,
        currency="RUB",
        disputed_at=disputed_at,
        reservation_id=reservation.id if reservation else None,
        note=(note or "").strip() or None,
        created_by_admin_id=admin_id,
    )
    session.add(dispute)
    await session.flush()
    user = await session.scalar(
        select(User).where(User.id == payment.user_id).with_for_update()
    )
    user.financial_hold = True
    user.financial_block_reason = "open_payment_dispute"
    payment.reconciliation_status = "manual_review"
    payment.manual_review_reason = "payment_dispute_open"
    session.add(
        PaymentEvent(
            payment_id=payment.id,
            event_type="payment_dispute_opened",
            provider_status=payment.provider_status,
            reason=provider_case_id[:100],
            source="admin",
            details=(
                f"dispute={dispute.public_id}; amount={int(rubles)} RUB; "
                f"reserved={int(reserve_amount)} RUB"
            ),
        )
    )
    await AuditService.log_action(
        session,
        admin_id=admin_id,
        action="PAYMENT_DISPUTE_OPENED",
        target_type="PaymentDispute",
        target_id=dispute.id,
        details=(
            f"payment={payment.id}, case={provider_case_id}, "
            f"amount={int(rubles)} RUB, reserved={int(reserve_amount)} RUB"
        ),
    )
    return PaymentDisputeResult(dispute, True)


async def mark_payment_dispute_manual_review(
    session,
    *,
    dispute_id: int,
    admin_id: int,
    note: str,
) -> PaymentDispute:
    dispute = await session.scalar(
        select(PaymentDispute)
        .where(PaymentDispute.id == dispute_id)
        .with_for_update()
    )
    if dispute is None:
        raise PaymentDisputeError("dispute_not_found")
    if dispute.status == "manual_review":
        return dispute
    if dispute.status != "open":
        raise PaymentDisputeError("dispute_already_resolved")
    dispute.status = "manual_review"
    dispute.note = note.strip() or dispute.note
    await refresh_user_dispute_hold(session, user_id=dispute.user_id)
    await AuditService.log_action(
        session,
        admin_id=admin_id,
        action="PAYMENT_DISPUTE_MANUAL_REVIEW",
        target_type="PaymentDispute",
        target_id=dispute.id,
        details=dispute.note,
    )
    return dispute


async def resolve_payment_dispute(
    session,
    *,
    dispute_id: int,
    outcome: str,
    admin_id: int,
    note: str | None = None,
) -> PaymentDispute:
    if outcome not in {"won_by_merchant", "lost_by_merchant"}:
        raise PaymentDisputeError("dispute_outcome_invalid")
    dispute = await session.scalar(
        select(PaymentDispute)
        .where(PaymentDispute.id == dispute_id)
        .with_for_update()
    )
    if dispute is None:
        raise PaymentDisputeError("dispute_not_found")
    if dispute.status == outcome:
        return dispute
    if dispute.status not in ACTIVE_DISPUTE_STATUSES:
        raise PaymentDisputeError("dispute_already_resolved")
    payment = await session.scalar(
        select(Payment).where(Payment.id == dispute.payment_id).with_for_update()
    )
    if payment is None:
        raise PaymentDisputeError("payment_not_found")

    if outcome == "won_by_merchant":
        if dispute.reservation_id is not None:
            reservation = await session.get(
                AccountBalanceReservation, dispute.reservation_id
            )
            if reservation is not None and reservation.status == "active":
                await resolve_reservation(
                    session,
                    reservation_id=reservation.id,
                    outcome="released",
                )
    else:
        entry, _ = await create_payment_debit(
            session,
            payment_id=payment.id,
            entry_type="chargeback_debit",
            amount=dispute.amount,
            idempotency_key=f"chargeback-dispute:{dispute.id}",
            metadata={
                "dispute_id": dispute.id,
                "provider_case_id": dispute.provider_case_id,
            },
        )
        dispute.chargeback_entry_id = entry.id
        if dispute.reservation_id is not None:
            reservation = await session.get(
                AccountBalanceReservation, dispute.reservation_id
            )
            if reservation is not None and reservation.status == "active":
                await resolve_reservation(
                    session,
                    reservation_id=reservation.id,
                    outcome="consumed",
                )
        remaining = await _remaining_payment_exposure(session, payment)
        if remaining == 0:
            payment.fulfillment_status = "reversed"
            payment.reversed_at = payment.reversed_at or now_utc()
        payment.reconciliation_status = "manual_review"
        payment.manual_review_reason = "chargeback_lost"

    dispute.status = outcome
    dispute.resolved_by_admin_id = admin_id
    dispute.resolved_at = now_utc()
    if note and note.strip():
        dispute.note = note.strip()
    await refresh_user_dispute_hold(session, user_id=dispute.user_id)
    if outcome == "won_by_merchant":
        active = await session.scalar(
            select(PaymentDispute.id).where(
                PaymentDispute.payment_id == payment.id,
                PaymentDispute.status.in_(ACTIVE_DISPUTE_STATUSES),
                PaymentDispute.id != dispute.id,
            )
        )
        if active is None and payment.manual_review_reason == "payment_dispute_open":
            payment.reconciliation_status = "ok"
            payment.manual_review_reason = None
    session.add(
        PaymentEvent(
            payment_id=payment.id,
            event_type="payment_dispute_resolved",
            provider_status=payment.provider_status,
            reason=outcome,
            source="admin",
            details=f"dispute={dispute.public_id}; case={dispute.provider_case_id}",
        )
    )
    await AuditService.log_action(
        session,
        admin_id=admin_id,
        action="PAYMENT_DISPUTE_RESOLVED",
        target_type="PaymentDispute",
        target_id=dispute.id,
        details=f"outcome={outcome}, note={dispute.note or ''}",
    )
    await session.flush()
    return dispute
