"""Periodic recovery and alerting for stalled balance top-ups."""

import asyncio
import logging
from datetime import timedelta

from aiogram import Bot
from cachetools import TTLCache
from sqlalchemy import or_, select

from bot import texts
from bot.constants import STALE_PAYMENT_THRESHOLD, WORKER_ERROR_SLEEP_INTERVAL
from config.settings import get_settings
from database.connection import session_scope
from database.models import Payment, User
from services.account_topup import settle_succeeded_topup_by_id
from services.payment_provider_operations import ensure_reconcile_payment_operation
from services.payment_status import payment_display_status
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
    async with session_scope() as session:
        payments = (
            await session.scalars(
                select(Payment)
                .where(Payment.created_at < now_utc() - timedelta(minutes=5))
                .order_by(Payment.id)
                .limit(100)
            )
        ).all()
        for payment in payments:
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
