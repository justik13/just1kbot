"""Balance top-up settlement and provider reconciliation."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.models import Payment, PaymentEvent
from database.repositories.account_ledger_repo import (
    AccountBalanceSnapshot,
    credit_succeeded_topup,
    get_account_balance,
)
from database.repositories.tariff_quotes_repo import lock_checkout_user
from services.payment_disputes import refresh_user_dispute_hold
from utils.datetime_helpers import now_utc


class AccountTopupError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


async def settle_succeeded_topup(
    session: AsyncSession,
    *,
    payment: Payment,
    source: str,
    settings=None,
) -> tuple[bool, AccountBalanceSnapshot]:
    """Credit a verified top-up and close its non-subscription lifecycle."""
    if payment.provider_confirmed_at is None:
        raise AccountTopupError("topup_provider_not_verified")

    user = await lock_checkout_user(session, payment.user_id)
    if user is not None:
        # A hard top-up block must never be bypassed. A chargeback-debt hold is
        # different: the user must be able to restore the account by topping up
        # the exact debt amount, so that recovery payment is allowed to settle it.
        hard_block = user.topup_blocked
        recovery_topup = user.financial_hold and user.financial_block_reason == "chargeback_debt"
        if hard_block or (user.financial_hold and not recovery_topup):
            payment.fulfillment_status = "manual_review"
            payment.reconciliation_status = "manual_review"
            payment.manual_review_reason = "user_financially_blocked"
            session.add(
                PaymentEvent(
                    payment_id=payment.id,
                    event_type="topup_blocked_by_hold",
                    provider_status=payment.provider_status,
                    reason="user_financially_blocked",
                    source=source,
                )
            )
            await session.flush()
            return False, await get_account_balance(session, user_id=payment.user_id)

    entry, created = await credit_succeeded_topup(
        session,
        locked_payment=payment,
        metadata={"settlement_source": source},
    )
    payment.fulfillment_status = "succeeded"
    payment.fulfilled_at = payment.fulfilled_at or now_utc()
    payment.ui_visible = False
    payment.fulfillment_last_error_code = None
    payment.fulfillment_last_error = None
    if payment.reconciliation_status not in {"mismatch", "manual_review"}:
        payment.reconciliation_status = "ok"
    balance = await get_account_balance(session, user_id=payment.user_id)
    await refresh_user_dispute_hold(session, user_id=payment.user_id)
    cfg = settings or get_settings()
    if balance.accounting_position > Decimal(cfg.BALANCE_MAX_AVAILABLE_RUB):
        user = await lock_checkout_user(session, payment.user_id)
        user.topup_blocked = True
        user.financial_block_reason = "balance_limit_exceeded_by_late_payment"
        session.add(
            PaymentEvent(
                payment_id=payment.id,
                event_type="balance_limit_exceeded_by_late_payment",
                provider_status=payment.provider_status,
                reason=str(balance.accounting_position),
                source=source,
            )
        )
    if created:
        session.add(
            PaymentEvent(
                payment_id=payment.id,
                event_type="balance_topup_credited",
                provider_status=payment.provider_status,
                reason=str(entry.amount),
                source=source,
            )
        )
    await session.flush()
    return created, balance


async def settle_succeeded_topup_by_id(
    session: AsyncSession,
    *,
    payment_id: int,
    source: str,
    settings=None,
) -> tuple[bool, AccountBalanceSnapshot]:
    payment = await session.scalar(
        select(Payment).where(Payment.id == payment_id).with_for_update()
    )
    if payment is None:
        raise AccountTopupError("topup_not_found")
    return await settle_succeeded_topup(
        session, payment=payment, source=source, settings=settings
    )


async def request_topup_status_refresh(
    session: AsyncSession,
    *,
    payment_id: int,
    source: str = "user_refresh",
) -> Payment:
    """Queue provider reconciliation and recover a verified but uncredited top-up."""
    payment = await session.scalar(
        select(Payment).where(Payment.id == payment_id).with_for_update()
    )
    if payment is None:
        raise AccountTopupError("topup_not_found")
    user = await lock_checkout_user(session, payment.user_id)
    if user is not None and user.topup_blocked:
        payment.fulfillment_status = "manual_review"
        payment.reconciliation_status = "manual_review"
        payment.manual_review_reason = "user_financially_blocked"
        return payment

    if payment.external_id and payment.provider_status not in {
        "refunded",
        "canceled",
        "succeeded",
    }:
        payment.provider_status = "pending"
    payment.updated_at = now_utc()
    return payment


async def get_topup_payment(
    session: AsyncSession,
    *,
    payment_id: int,
) -> Payment | None:
    return await session.scalar(
        select(Payment).where(Payment.id == payment_id)
    )