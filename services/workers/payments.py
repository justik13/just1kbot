import asyncio
import logging
from datetime import timedelta

from aiogram import Bot
from cachetools import TTLCache
from sqlalchemy import select

from bot.constants import STALE_PAYMENT_THRESHOLD, WORKER_ERROR_SLEEP_INTERVAL
from config.settings import get_settings
from database.connection import session_scope
from database.models import Payment, User
from services.payment_provider_operations import ensure_reconcile_payment_operation
from services.workers.webhook_inbox import ensure_fulfillment
from utils.datetime_helpers import now_utc

logger = logging.getLogger("BackgroundWorker")

_alerted_stale_payments: TTLCache[int, bool] = TTLCache(maxsize=50000, ttl=7200)

PAYMENTS_START_DELAY = 60.0

ORPHAN_PENDING_THRESHOLD_HOURS = 1


async def _preload_alerted_stale_payments():
    try:
        async with session_scope() as session:
            threshold = now_utc() - timedelta(hours=1)
            stmt = (
                select(Payment.id)
                .where(
                    Payment.status.in_(["pending", "requires_manual_review"]),
                    Payment.created_at < threshold,
                )
                .order_by(Payment.created_at.desc())
                .limit(10000)
            )
            result = await session.execute(stmt)
            for (payment_id,) in result.all():
                _alerted_stale_payments[payment_id] = True

        if _alerted_stale_payments:
            logger.info(
                "Preloaded %s existing stale payment IDs to suppress duplicate alerts after restart",
                len(_alerted_stale_payments),
            )
    except Exception as e:
        logger.warning("Failed to preload stale payment IDs: %s", e)


async def stale_payments_checker_loop(bot: Bot, shutdown_event: asyncio.Event):
    settings = get_settings()

    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=PAYMENTS_START_DELAY)
        logger.info("Stale payments worker stopped during start delay (shutdown)")
        return
    except asyncio.TimeoutError:
        pass

    await _preload_alerted_stale_payments()

    while not shutdown_event.is_set():
        try:
            await _cleanup_orphan_pending_payments()
            await _process_stale_payments(bot, settings)
        except asyncio.CancelledError:
            logger.info("Stale payments worker cancelled")
            break
        except Exception as e:
            logger.error(
                "Критическая ошибка в stale_payments_checker: %s", e, exc_info=True
            )
            if shutdown_event.is_set():
                break
            await asyncio.sleep(WORKER_ERROR_SLEEP_INTERVAL)
            continue

        try:
            await asyncio.wait_for(
                shutdown_event.wait(), timeout=STALE_PAYMENT_THRESHOLD
            )
            break
        except asyncio.TimeoutError:
            continue

    logger.info("Stale payments worker stopped gracefully")


async def _cleanup_orphan_pending_payments():
    """Ensure durable creation commands; never infer provider failure locally."""
    async with session_scope() as session:
        rows = (
            await session.scalars(
                select(Payment)
                .where(
                    Payment.provider_status.in_(("creating", "unknown")),
                    Payment.provider_idempotency_key.is_not(None),
                )
                .limit(100)
            )
        ).all()
        # create commands are created atomically with new payments; missing legacy rows require review, not failure.
        for payment in rows:
            if payment.external_id:
                await ensure_reconcile_payment_operation(
                    session, payment, reason="stale_worker"
                )


async def _process_stale_payments(bot: Bot, settings):
    async with session_scope() as session:
        rows = (
            await session.scalars(
                select(Payment)
                .where(Payment.created_at < now_utc() - timedelta(hours=1))
                .limit(100)
            )
        ).all()
        for payment in rows:
            if payment.external_id and payment.provider_status in {
                "pending",
                "unknown",
            }:
                await ensure_reconcile_payment_operation(
                    session, payment, reason="stale_worker"
                )
            if (
                payment.provider_status == "succeeded"
                and payment.fulfillment_status
                not in {"succeeded", "reversed", "manual_review"}
            ):
                if (
                    payment.payment_kind == "balance_topup"
                    and payment.provider_confirmed_at is not None
                ):
                    from services.account_topup import settle_succeeded_topup_by_id

                    await settle_succeeded_topup_by_id(
                        session,
                        payment_id=payment.id,
                        source="stale_worker_recovery",
                    )
                elif payment.payment_kind != "balance_topup":
                    await ensure_fulfillment(
                        session, payment, "grant_subscription"
                    )
            if (
                payment.provider_status == "refunded"
                and payment.fulfillment_status != "reversed"
            ):
                await ensure_fulfillment(session, payment, "reverse_payment")
    await _alert_new_stale_payments(bot, settings)


async def _alert_new_stale_payments(bot: Bot, settings):
    current_time = now_utc()
    threshold = current_time - timedelta(hours=1)

    async with session_scope() as session:
        fresh_stmt = (
            select(Payment, User.telegram_id)
            .join(User, Payment.user_id == User.id)
            .where(
                Payment.status.in_(["pending", "requires_manual_review"]),
                Payment.created_at < threshold,
            )
            .order_by(Payment.created_at.desc())
            .limit(1000)
        )
        fresh_result = await session.execute(fresh_stmt)
        fresh_stale = [
            (payment, telegram_id) for payment, telegram_id in fresh_result.all()
        ]

    new_stale_for_alert = [
        (p, tg) for p, tg in fresh_stale if p.id not in _alerted_stale_payments
    ]

    if not new_stale_for_alert:
        return

    msg = (
        "⚠️ <b>Новые зависшие платежи (pending/review > 1ч)</b>\n"
        "────────────────────\n"
        f"Количество: <b>{len(new_stale_for_alert)}</b>\n"
    )

    for payment, telegram_id in new_stale_for_alert[:10]:
        method = payment.payment_method or "—"
        status_icon = "🧪" if payment.status == "requires_manual_review" else "⏳"
        msg += (
            f"{status_icon} ID: <code>{payment.id}</code> · "
            f"User: <code>{telegram_id}</code> · "
            f"{payment.amount} {payment.currency} · "
            f"{method}\n"
        )

    if len(new_stale_for_alert) > 10:
        msg += f"\n<i>... и ещё {len(new_stale_for_alert) - 10}</i>"

    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, msg, parse_mode="HTML")
        except Exception as e:
            logger.error("Stale alert failed to %s: %s", admin_id, e)

    for payment, _ in new_stale_for_alert:
        _alerted_stale_payments[payment.id] = True
