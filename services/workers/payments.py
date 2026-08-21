"""Periodic recovery and alerting for stalled balance top-ups."""

import asyncio
import logging
from datetime import timedelta

from aiogram import Bot
from cachetools import TTLCache
from sqlalchemy import and_, or_, select

from bot import texts
from bot.constants import STALE_PAYMENT_THRESHOLD, WORKER_ERROR_SLEEP_INTERVAL
from config.settings import get_settings
from database.connection import session_scope
from database.models import Payment, User
from database.repositories.tariff_quotes_repo import lock_checkout_user
from services.account_topup import settle_succeeded_topup_by_id
from services.payment_provider_operations import ensure_reconcile_payment_operation
from services.payment_status import payment_display_status
from services.referral_bonus import grant_referral_bonus_for_topup
from utils.datetime_helpers import now_utc

logger = logging.getLogger("BackgroundWorker")
_alerted_stale_payments: TTLCache[int, bool] = TTLCache(maxsize=50000, ttl=7200)
PAYMENTS_START_DELAY = 60.0


def _needs_attention():
    return or_(
        Payment.provider_status.in_(("creating", "pending", "waiting_for_capture", "unknown")),
        Payment.reconciliation_status.in_(("required", "mismatch", "manual_review")),
        Payment.fulfillment_status.in_(("failed", "manual_review")),
    )


def _needs_recovery():
    from sqlalchemy import or_, select, text
    from sqlalchemy.orm import aliased

    from database.models import User

    purchaser = aliased(User)
    referrer = aliased(User)

    user_subquery = (
        select(1)
        .select_from(purchaser)
        .join(referrer, referrer.telegram_id == purchaser.referred_by)
        .where(
            purchaser.id == Payment.user_id,
            purchaser.is_deleted.is_(False),
            purchaser.referred_by.is_not(None),
            purchaser.referred_by != purchaser.telegram_id,
            referrer.is_deleted.is_(False),
            referrer.is_banned.is_(False),
            referrer.id != purchaser.id,
        )
    )

    needs_bonus_retry = and_(
        text("payments.provider_status = 'succeeded'"),
        Payment.provider_confirmed_at.is_not(None),
        text("payments.fulfillment_status = 'succeeded'"),
        user_subquery.exists(),
        text("NOT (COALESCE(payments.topup_context, '{}'::jsonb) @> '{\"referral_bonus_processed\": true}'::jsonb)"),
    )

    return or_(
        text("payments.external_id IS NOT NULL AND payments.provider_status IN ('creating', 'pending', 'waiting_for_capture', 'unknown')"),
        text("payments.provider_status = 'succeeded' AND payments.provider_confirmed_at IS NOT NULL AND payments.fulfillment_status NOT IN ('succeeded', 'reversed', 'manual_review')"),
        needs_bonus_retry,
    )


async def _preload_alerted_stale_payments():
    try:
        async with session_scope() as session:
            threshold = now_utc() - timedelta(hours=1)
            ids = await session.scalars(
                select(Payment.id)
                .where(_needs_attention(), Payment.created_at < threshold)
                .order_by(Payment.created_at.desc())
                .limit(10000)
            )
            for payment_id in ids:
                _alerted_stale_payments[payment_id] = True
    except Exception as exc:
        logger.warning("Failed to preload stale payment IDs: %s", exc)


async def stale_payments_checker_loop(bot: Bot, shutdown_event: asyncio.Event):
    settings = get_settings()
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=PAYMENTS_START_DELAY)
        return
    except asyncio.TimeoutError:
        pass
    await _preload_alerted_stale_payments()
    while not shutdown_event.is_set():
        try:
            await _recover_stale_topups(bot)
            await _alert_new_stale_payments(bot, settings)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Stale top-up worker failed")
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(), timeout=WORKER_ERROR_SLEEP_INTERVAL
                )
                break
            except asyncio.TimeoutError:
                continue
        try:
            await asyncio.wait_for(
                shutdown_event.wait(), timeout=STALE_PAYMENT_THRESHOLD
            )
            break
        except asyncio.TimeoutError:
            continue


async def _recover_stale_topups(bot: Bot | None = None):
    max_batches = 10
    batch_size = 100
    last_processed_id = 0

    for _ in range(max_batches):
        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(Payment.id, Payment.user_id)
                    .where(
                        Payment.id > last_processed_id,
                        _needs_recovery(),
                        Payment.created_at < now_utc() - timedelta(minutes=5),
                    )
                    .order_by(Payment.id.asc())
                    .limit(batch_size)
                )
            ).all()

        if not rows:
            break

        for pid, uid in rows:
            last_processed_id = pid
            try:
                async with session_scope() as session:
                    payment = await session.scalar(
                        select(Payment)
                        .where(Payment.id == pid)
                        .with_for_update(skip_locked=True)
                    )
                    if not payment:
                        continue

                    ctx = payment.topup_context or {}
                    
                    if payment.external_id and payment.provider_status in {
                        "creating",
                        "pending",
                        "waiting_for_capture",
                        "unknown",
                    }:
                        await ensure_reconcile_payment_operation(
                            session, payment, reason="stale_topup_worker"
                        )

                    if (
                        payment.provider_status == "succeeded"
                        and payment.provider_confirmed_at is not None
                        and payment.fulfillment_status
                        not in {"succeeded", "reversed", "manual_review"}
                    ):
                        await settle_succeeded_topup_by_id(
                            session,
                            payment_id=payment.id,
                            source="stale_topup_recovery",
                            bot=bot,
                        )

                    # Referral bonuses are ledger entries with their own idempotency key.
                    # Retry them in a savepoint so a transient bonus failure cannot roll
                    # back a verified user payment. The next worker pass retries it.
                    if (
                        payment.provider_status == "succeeded"
                        and payment.provider_confirmed_at is not None
                        and payment.fulfillment_status == "succeeded"
                        and not (payment.topup_context and payment.topup_context.get("referral_bonus_processed"))
                    ):
                        try:
                            async with session.begin_nested():
                                await grant_referral_bonus_for_topup(
                                    session,
                                    purchaser_user_id=payment.user_id,
                                    payment_id=payment.id,
                                    topup_amount=payment.amount,
                                )
                                ctx = payment.topup_context or {}
                                payment.topup_context = {**ctx, "referral_bonus_processed": True}
                        except Exception:
                            logger.exception(
                                "Referral bonus retry failed for topup payment %s",
                                payment.id,
                            )
            except Exception:
                logger.exception("Failed to recover stale topup payment %s", pid)


async def _alert_new_stale_payments(bot: Bot, settings):
    threshold = now_utc() - timedelta(hours=1)
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Payment, User.telegram_id)
                .join(User, Payment.user_id == User.id)
                .where(_needs_attention(), Payment.created_at < threshold)
                .order_by(Payment.created_at.desc())
                .limit(1000)
            )
        ).all()
    new_rows = [
        (payment, telegram_id)
        for payment, telegram_id in rows
        if payment.id not in _alerted_stale_payments
    ]
    if not new_rows:
        return
    details = []
    for payment, telegram_id in new_rows[:10]:
        details.append(
            texts.STALE_TOPUP_ALERT_ROW.format(
                icon=(
                    texts.RUNTIME_SERVICES_WORKERS_PAYMENTS_L139_1
                    if payment_display_status(payment) == "requires_manual_review"
                    else texts.RUNTIME_SERVICES_WORKERS_PAYMENTS_L141_1
                ),
                payment_id=payment.id,
                telegram_id=telegram_id,
                amount=payment.amount,
                currency=payment.currency,
                method=payment.payment_method or texts.RUNTIME_SERVICES_WORKERS_PAYMENTS_L147_1,
            )
        )
    if len(new_rows) > 10:
        details.append(
            texts.STALE_TOPUP_ALERT_MORE.format(count=len(new_rows) - 10)
        )
    message = texts.STALE_TOPUP_ALERT.format(
        count=len(new_rows),
        details="".join(details),
    )
    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, message, parse_mode="HTML")
        except Exception as exc:
            logger.error("Stale alert failed to %s: %s", admin_id, exc)
    for payment, _ in new_rows:
        _alerted_stale_payments[payment.id] = True
