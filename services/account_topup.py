"""Balance top-up creation, hiding, and exactly-once settlement."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.models import Payment, PaymentEvent
from database.repositories.account_ledger_repo import (
    AccountBalanceSnapshot,
    credit_succeeded_topup,
    get_account_balance,
    whole_rubles,
)
from database.repositories.tariff_quotes_repo import lock_checkout_user
from services.payment_disputes import refresh_user_dispute_hold
from services.payment_provider_operations import enqueue_create
from utils.datetime_helpers import now_utc


UNFINISHED_TOPUP_PROVIDER_STATUSES = (
    "not_created",
    "creating",
    "pending",
    "waiting_for_capture",
    "unknown",
    "manual_review",
    "succeeded",
)


class AccountTopupError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class TopupCreationResult:
    payment: Payment
    created: bool
    balance: AccountBalanceSnapshot


async def _visible_topup_for_update(
    session: AsyncSession, user_id: int
) -> Payment | None:
    return await session.scalar(
        select(Payment)
        .where(
            Payment.user_id == user_id,
            Payment.ui_visible.is_(True),
            Payment.checkout_status == "active",
            Payment.provider_status.not_in(("succeeded", "canceled", "refunded")),
        )
        .order_by(Payment.id.desc())
        .with_for_update()
        .limit(1)
    )


async def get_visible_balance_topup(
    session: AsyncSession, *, user_id: int, for_update: bool = False
) -> Payment | None:
    statement = (
        select(Payment)
        .where(
            Payment.user_id == user_id,
            Payment.ui_visible.is_(True),
            Payment.checkout_status == "active",
            Payment.provider_status.not_in(("succeeded", "canceled", "refunded")),
        )
        .order_by(Payment.id.desc())
        .limit(1)
    )
    if for_update:
        statement = statement.with_for_update()
    return await session.scalar(statement)


async def _pending_topup_exposure(
    session: AsyncSession, user_id: int
) -> Decimal:
    amount = await session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.user_id == user_id,
            Payment.credited_at.is_(None),
            Payment.provider_status.in_(UNFINISHED_TOPUP_PROVIDER_STATUSES),
        )
    )
    return Decimal(amount or 0)


async def create_balance_topup(
    session: AsyncSession,
    *,
    user_id: int,
    amount: object,
    bot_username: str,
    context: dict | None = None,
    settings=None,
) -> TopupCreationResult:
    """Create one durable provider command without performing provider HTTP."""
    cfg = settings or get_settings()
    try:
        rubles = whole_rubles(amount)
    except ValueError as exc:
        raise AccountTopupError("topup_amount_must_be_whole_rubles") from exc
    if rubles < Decimal(cfg.BALANCE_MIN_TOPUP_RUB):
        raise AccountTopupError("topup_below_minimum")
    if rubles > Decimal(cfg.BALANCE_MAX_CUSTOM_TOPUP_RUB):
        raise AccountTopupError("topup_above_maximum")

    user = await lock_checkout_user(session, user_id)
    if user is None or user.is_deleted:
        raise AccountTopupError("topup_user_missing")
    if user.is_banned:
        raise AccountTopupError("topup_user_banned")
    if user.topup_blocked:
        raise AccountTopupError("topup_blocked")

    balance = await get_account_balance(
        session, user_id=user.id, locked_user=user
    )
    existing = await _visible_topup_for_update(session, user.id)
    if existing is not None:
        return TopupCreationResult(existing, False, balance)

    unfinished = int(
        await session.scalar(
            select(func.count(Payment.id)).where(
                Payment.user_id == user.id,
                Payment.credited_at.is_(None),
                Payment.provider_status.in_(UNFINISHED_TOPUP_PROVIDER_STATUSES),
            )
        )
        or 0
    )
    if unfinished >= cfg.BALANCE_MAX_UNFINISHED_TOPUPS:
        raise AccountTopupError("too_many_unfinished_topups")

    created_since = now_utc() - timedelta(hours=24)
    creation_count = int(
        await session.scalar(
            select(func.count(Payment.id)).where(
                Payment.user_id == user.id,
                Payment.created_at >= created_since,
            )
        )
        or 0
    )
    if creation_count >= cfg.BALANCE_MAX_TOPUP_CREATIONS_24H:
        raise AccountTopupError("topup_creation_rate_limited")

    pending = await _pending_topup_exposure(session, user.id)
    projected_position = balance.accounting_position + pending + rubles
    if max(Decimal("0"), projected_position) > Decimal(
        cfg.BALANCE_MAX_AVAILABLE_RUB
    ):
        raise AccountTopupError("topup_balance_limit_exceeded")

    payment = Payment(
        user_id=user.id,
        amount=rubles,
        currency="RUB",
        public_order_id="topup_" + uuid.uuid4().hex,
        provider_idempotency_key=uuid.uuid4().hex,
        provider_status="creating",
        fulfillment_status="not_ready",
        reconciliation_status="ok",
        checkout_status="active",
        ui_visible=True,
        topup_context=dict(context or {}),
    )
    session.add(payment)
    await session.flush()
    bot_username_clean = (bot_username or "").lstrip("@")
    return_url = cfg.YOOKASSA_RETURN_URL.format(
        bot_username=bot_username_clean
    )
    await enqueue_create(
        session,
        payment,
        description="Пополнение баланса",
        return_url=return_url,
    )
    session.add(
        PaymentEvent(
            payment_id=payment.id,
            event_type="balance_topup_created",
            provider_status=payment.provider_status,
            source="account_topup",
        )
    )
    await session.flush()
    return TopupCreationResult(payment, True, balance)


async def hide_balance_topup(
    session: AsyncSession, *, user_id: int, payment_id: int
) -> Payment:
    """Hide only the checkout UI; provider truth continues to reconcile."""
    payment = await session.scalar(
        select(Payment).where(Payment.id == payment_id).with_for_update()
    )
    if payment is None or payment.user_id != user_id:
        raise AccountTopupError("topup_not_found")
    await lock_checkout_user(session, user_id)
    if payment.provider_status in {"succeeded", "canceled", "refunded"}:
        raise AccountTopupError("topup_already_terminal")
    payment.ui_visible = False
    payment.user_cancel_requested_at = payment.user_cancel_requested_at or now_utc()
    session.add(
        PaymentEvent(
            payment_id=payment.id,
            event_type="balance_topup_hidden",
            provider_status=payment.provider_status,
            source="telegram",
        )
    )
    await session.flush()
    return payment


async def cancel_all_unfinished_topups(
    session: AsyncSession, *, user_id: int
) -> int:
    """Force cancel all unfinished topups for a user."""
    await lock_checkout_user(session, user_id)
    payments = (
        await session.scalars(
            select(Payment)
            .where(
                Payment.user_id == user_id,
                Payment.credited_at.is_(None),
                Payment.provider_status.in_(UNFINISHED_TOPUP_PROVIDER_STATUSES),
            )
            .with_for_update()
        )
    ).all()

    count = 0
    for payment in payments:
        if payment.provider_status in {"succeeded", "canceled", "refunded"}:
            continue
        payment.provider_status = "canceled"
        payment.checkout_status = "abandoned"
        payment.ui_visible = False
        payment.user_cancel_requested_at = payment.user_cancel_requested_at or now_utc()
        session.add(
            PaymentEvent(
                payment_id=payment.id,
                event_type="balance_topup_hidden",
                provider_status="canceled",
                source="telegram",
            )
        )
        count += 1
    
    if count > 0:
        await session.flush()
    return count


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
        # Hard top-up blocks must never be bypassed. A chargeback debt hold is
        # intentionally recoverable: the user needs to top up in order to settle
        # the debt that caused the hold.
        hard_block = user.topup_blocked
        recovery_topup = (
            user.financial_hold
            and user.financial_block_reason == "chargeback_debt"
        )
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
            return False, await get_account_balance(
                session, user_id=payment.user_id
            )

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
        try:
            async with session.begin_nested():
                from services.referral_bonus import grant_referral_bonus_for_topup
                await grant_referral_bonus_for_topup(
                    session,
                    purchaser_user_id=payment.user_id,
                    payment_id=payment.id,
                    topup_amount=payment.amount,
                )
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(
                f"Failed to grant referral bonus for topup {payment.id}: {e}"
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
    if user is not None:
        recovery_topup = (
            user.financial_hold
            and user.financial_block_reason == "chargeback_debt"
        )
        if user.topup_blocked or (user.financial_hold and not recovery_topup):
            payment.fulfillment_status = "manual_review"
            payment.reconciliation_status = "manual_review"
            payment.manual_review_reason = "user_financially_blocked"
            return payment

    if payment.external_id and payment.provider_status not in {
        "refunded",
        "canceled",
    }:
        from services.payment_provider_operations import (
            ensure_reconcile_payment_operation,
        )

        await ensure_reconcile_payment_operation(
            session,
            payment,
            reason=source,
        )
    if (
        payment.provider_status == "succeeded"
        and payment.provider_confirmed_at is not None
        and payment.fulfillment_status
        not in {"succeeded", "reversed", "manual_review"}
    ):
        await settle_succeeded_topup(
            session,
            payment=payment,
            source=f"{source}_recovery",
        )
    return payment
