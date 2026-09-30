"""Backstop for order-credited push notifications (late top-up delivery).

Scenario: the user paid, the money was held (or the Telegram send failed),
the user left the payment screen — and the credit lands later. The webhook
and the ``order_check`` button deliver the push when the user is present;
this worker scans orders with an outstanding notification debt
(``Order.metadata_['late_notify_pending']``) and delivers the push
proactively, wherever the user is in the bot.

Safety properties (money is never touched here, only notification metadata):

* An order is notified only if a ``payment_credit`` ledger row exists for it
  — uncredited (still held) orders are skipped without consuming attempts.
* Attempts are bounded (``MAX_NOTIFY_ATTEMPTS``); afterwards the debt is
  dropped with a log instead of retrying forever (e.g. bot blocked).
* Each order is processed in its own session so one failure cannot poison
  the batch.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from sqlalchemy import func, select

from bot import texts
from bot.formatters import get_tariff_display_name
from bot.keyboards import get_payment_success_keyboard
from bot.keyboards.payment import get_topup_credit_keyboard
from config.constants import WORKER_ERROR_SLEEP_INTERVAL
from database.connection import session_scope
from database.models import AccountLedgerEntry, Order, User
from database.repositories.account_ledger_repo import get_account_balance
from services.order_notifications import (
    LATE_NOTIFY_ATTEMPTS_KEY,
    LATE_NOTIFY_PENDING_KEY,
    mark_notified,
)
from utils.telegram import EFFECT_CONFETTI, safe_send_message

logger = logging.getLogger(__name__)

CREDIT_NOTIFY_INTERVAL = 300.0
CREDIT_NOTIFY_START_DELAY = 60.0
CREDIT_NOTIFY_BATCH_SIZE = 20
MAX_NOTIFY_ATTEMPTS = 10


async def credit_notifications_loop(
    bot: Bot,
    shutdown_event: asyncio.Event,
):
    try:
        await asyncio.wait_for(
            shutdown_event.wait(),
            timeout=CREDIT_NOTIFY_START_DELAY,
        )
        logger.info("Credit-notify worker stopped during start delay (shutdown)")
        return
    except asyncio.TimeoutError:
        pass

    while not shutdown_event.is_set():
        try:
            delivered = await deliver_pending_credit_notifications(bot)
            if delivered:
                logger.info(
                    "Credit-notify worker delivered %s late push(es)", delivered
                )
        except asyncio.CancelledError:
            logger.info("Credit-notify worker cancelled")
            break
        except Exception as e:
            logger.error(
                "Credit-notify worker cycle failed: %s", e, exc_info=True
            )
            if shutdown_event.is_set():
                break
            await asyncio.sleep(WORKER_ERROR_SLEEP_INTERVAL)
            continue

        try:
            await asyncio.wait_for(
                shutdown_event.wait(),
                timeout=CREDIT_NOTIFY_INTERVAL,
            )
            break
        except asyncio.TimeoutError:
            continue

    logger.info("Credit-notify worker stopped gracefully")


async def deliver_pending_credit_notifications(bot: Bot) -> int:
    """One pass over orders with notification debt. Returns delivered count."""
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Order.id)
                .where(
                    Order.status == "paid",
                    Order.metadata_[LATE_NOTIFY_PENDING_KEY].astext == "true",
                )
                .order_by(Order.created_at.asc())
                .limit(CREDIT_NOTIFY_BATCH_SIZE)
            )
        ).scalars().all()
        order_ids = list(rows)

    delivered = 0
    for order_id in order_ids:
        try:
            if await _deliver_one(bot, order_id):
                delivered += 1
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                "Credit-notify delivery failed for order %s: %s",
                order_id,
                e,
                exc_info=True,
            )
    return delivered


async def _send_late_push(bot: Bot, session, order: Order, user: User) -> bool:
    """Compact proactive push for a credited order.

    Uses only the worker-allowed presentation adapters (``bot.texts``,
    ``bot.keyboards``) plus ``utils.telegram`` delivery — the full hub
    screens stay on the request path (webhook / order_check).
    Returns True only when Telegram accepted the message.
    """
    try:
        if order.service_type == "topup":
            balance = await get_account_balance(session, user_id=user.id)
            order_context = (
                (order.metadata_ or {}).get("context") if order.metadata_ else None
            )
            keyboard = (
                get_topup_credit_keyboard(order_context) if order_context else None
            )
            text = (
                f"{texts.TOPUP_CREDITED_NOTICE}\n\n"
                + texts.BALANCE_BALANCE.format(
                    int_snapshot_real_available=int(balance.real_available)
                )
            )
        else:
            balance = await get_account_balance(session, user_id=user.id)
            # Same tier label as the request path (credit_notify.py):
            # two pushes for one order must never name the tariff differently.
            tariff_name = get_tariff_display_name(order.device_limit or 2)
            is_change = bool(
                order.metadata_ and order.metadata_.get("is_tariff_change")
            )
            operation = (
                texts.PAYMENT_OP_TITLE_CHANGE
                if is_change
                else texts.PURCHASE_COMPLETED
            )
            text = texts.PAYMENT_PURCHASE_SUCCESS_CARD.format(
                operation_title=operation,
                tariff_name=tariff_name,
                duration_days=order.duration_days,
                charged=int(order.amount_rub),
                real_balance=int(balance.real_available),
                bonus_balance=int(balance.bonus_available),
            )
            keyboard = get_payment_success_keyboard()
        sent_id = await safe_send_message(
            bot,
            user.telegram_id,
            text,
            reply_markup=keyboard,
            message_effect_id=EFFECT_CONFETTI,
        )
        return sent_id is not None
    except Exception as exc:
        logger.warning(
            "Credit-notify send failed for order %s: %s", order.id, exc
        )
        return False


async def _deliver_one(bot: Bot, order_id) -> bool:
    """Deliver a single late push. True only on confirmed delivery."""
    async with session_scope() as session:
        order = await session.get(Order, order_id, with_for_update=True)
        if order is None:
            return False
        meta = dict(order.metadata_ or {})
        if not meta.get(LATE_NOTIFY_PENDING_KEY):
            return False

        # Status may have changed since the batch select (e.g. full refund
        # landed after settlement). A non-paid order must never receive a
        # "credited" push — drop the debt instead of delivering it.
        if order.status != "paid":
            mark_notified(order)
            logger.info(
                "Credit-notify drops order %s: status is %s, not paid",
                order.id,
                order.status,
            )
            return False

        # Still held (no release yet): money has not moved, stay silent and
        # do not consume attempts — the release will re-arm the debt.
        if meta.get("settlement_held"):
            return False

        # Never notify without an actual credit in the ledger.
        has_credit = await session.scalar(
            select(func.count(AccountLedgerEntry.id)).where(
                AccountLedgerEntry.order_id == order.id,
                AccountLedgerEntry.entry_type == "payment_credit",
            )
        )
        if not has_credit:
            logger.warning(
                "Credit-notify skips order %s: paid but no payment_credit "
                "(pre-flight 3B owns this anomaly)",
                order.id,
            )
            return False

        user = await session.get(User, order.user_id)
        if user is None or not user.telegram_id:
            mark_notified(order)
            logger.info(
                "Credit-notify drops order %s: user gone or unreachable", order.id
            )
            return False

        if await _send_late_push(bot, session, order, user):
            mark_notified(order)
            logger.info(
                "Credit-notify delivered late push for order %s to user %s",
                order.id,
                user.id,
            )
            return True

        attempts = int(meta.get(LATE_NOTIFY_ATTEMPTS_KEY, 0) or 0) + 1
        if attempts >= MAX_NOTIFY_ATTEMPTS:
            mark_notified(order)
            logger.warning(
                "Credit-notify gives up on order %s after %s attempts",
                order.id,
                attempts,
            )
            return False
        meta[LATE_NOTIFY_ATTEMPTS_KEY] = attempts
        order.metadata_ = meta
        logger.info(
            "Credit-notify attempt %s/%s failed for order %s, will retry",
            attempts,
            MAX_NOTIFY_ATTEMPTS,
            order.id,
        )
        return False
