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
from bot.keyboards.notifications import get_referral_bonus_keyboard
from bot.keyboards.payment import get_topup_credit_keyboard
from config.constants import WORKER_ERROR_SLEEP_INTERVAL
from database.connection import session_scope
from database.models import AccountLedgerEntry, Order, User
from database.repositories.account_ledger_repo import get_account_balance
from services.order_notifications import (
    LATE_NOTIFY_ATTEMPTS_KEY,
    LATE_NOTIFY_PENDING_KEY,
    REFERRER_NOTIFIED_KEY,
    mark_notified,
    mark_referrer_notified,
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


async def _send_late_push(
    bot: Bot,
    service_type: str,
    fields: dict,
    telegram_id: int,
    real_available: int,
    bonus_available: int,
) -> bool:
    """Compact proactive push for a credited order (no DB access).

    Uses only the worker-allowed presentation adapters (``bot.texts``,
    ``bot.keyboards``) plus ``utils.telegram`` delivery — the full hub
    screens stay on the request path (webhook / order_check).
    Returns True only when Telegram accepted the message.
    """
    try:
        if service_type == "topup":
            keyboard = (
                get_topup_credit_keyboard(fields["context"])
                if fields["context"]
                else None
            )
            text = (
                f"{texts.TOPUP_CREDITED_NOTICE}\n\n"
                + texts.BALANCE_BALANCE.format(
                    int_snapshot_real_available=real_available
                )
            )
        else:
            # Same tier label as the request path (credit_notify.py):
            # two pushes for one order must never name the tariff differently.
            tariff_name = get_tariff_display_name(fields["device_limit"] or 2)
            operation = (
                texts.PAYMENT_OP_TITLE_CHANGE
                if fields["is_tariff_change"]
                else texts.PURCHASE_COMPLETED
            )
            text = texts.PAYMENT_PURCHASE_SUCCESS_CARD.format(
                operation_title=operation,
                tariff_name=tariff_name,
                duration_days=fields["duration_days"],
                charged=int(fields["amount_rub"]),
                real_balance=real_available,
                bonus_balance=bonus_available,
            )
            keyboard = get_payment_success_keyboard()
        sent_id = await safe_send_message(
            bot,
            telegram_id,
            text,
            reply_markup=keyboard,
            message_effect_id=EFFECT_CONFETTI,
        )
        return sent_id is not None
    except Exception as exc:
        logger.warning("Credit-notify send failed: %s", exc)
        return False


async def _send_referrer_push(bot: Bot, referrer: dict) -> bool:
    """Proactive push for a referral bonus (no DB access)."""
    try:
        text = texts.REFERRAL_BONUS_ACCREDITED.format(
            bonus=int(referrer["bonus"])
        )
        sent_id = await safe_send_message(
            bot,
            referrer["telegram_id"],
            text,
            reply_markup=get_referral_bonus_keyboard(),
            message_effect_id=EFFECT_CONFETTI,
        )
        return sent_id is not None
    except Exception as exc:
        logger.warning("Credit-notify referrer send failed: %s", exc)
        return False


async def _deliver_one(bot: Bot, order_id) -> bool:
    """Deliver a single late push. True only on confirmed delivery.

    Three phases so the row lock is never held during Telegram I/O:

    1. Snapshot (short read-only session): eligibility + plain scalars.
    2. Send (no session at all): owner push, then referrer push.
    3. Finalize (brief locked session): re-check the debt is still open
       (a concurrent webhook/button may have delivered first), then clear
       markers or record the failed attempt.
    """
    snap = await _snapshot_eligible(order_id)
    if snap is None:
        return False
    if snap["outcome"] == "wait":
        return False
    if snap["outcome"] == "anomaly":
        await _finalize_attempt(order_id)
        return False
    if snap["outcome"] == "drop":
        await _drop_debt(order_id)
        return False

    owner_ok = await _send_late_push(
        bot,
        snap["service_type"],
        snap["order_fields"],
        snap["telegram_id"],
        snap["real_available"],
        snap["bonus_available"],
    )
    referrer_ok = True
    if owner_ok and snap["referrer"] is not None:
        referrer_ok = await _send_referrer_push(bot, snap["referrer"])
    elif snap["referrer"] is not None:
        # Owner push failed and will be retried; the referrer push rides
        # along with the next attempt instead of spamming separately.
        referrer_ok = False

    return await _finalize_delivery(
        order_id, owner_ok=owner_ok, referrer_ok=referrer_ok,
        referrer_pending=snap["referrer"] is not None,
    )


async def _snapshot_eligible(order_id) -> dict | None:
    """Read-only eligibility snapshot. None = nothing to do (no writes)."""
    async with session_scope() as session:
        order = await session.get(Order, order_id)
        if order is None:
            return None
        meta = dict(order.metadata_ or {})
        if not meta.get(LATE_NOTIFY_PENDING_KEY):
            return None

        # Status is re-checked under lock at finalize time as well; an early
        # drop here avoids wasting a send on an already-refunded order.
        if order.status != "paid":
            return {"outcome": "drop"}

        # Still held (no release yet): money has not moved, stay silent and
        # do not consume attempts — the release will re-arm the debt.
        if meta.get("settlement_held"):
            return {"outcome": "wait"}

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
            return {"outcome": "anomaly"}

        user = await session.get(User, order.user_id)
        # Already synced as blocked by another path (broadcast, WI traffic,
        # expiry notifications): do not burn attempts on a walled user.
        if user is None or not user.telegram_id or user.is_bot_blocked:
            return {"outcome": "drop"}

        referrer = None
        if (
            order.service_type == "topup"
            and not meta.get(REFERRER_NOTIFIED_KEY)
        ):
            referrer = await _find_unnotified_referrer(session, order)

        balance = await get_account_balance(session, user_id=user.id)
        return {
            "outcome": "send",
            "service_type": order.service_type,
            "order_fields": {
                "id": order.id,
                "amount_rub": order.amount_rub,
                "duration_days": order.duration_days,
                "device_limit": order.device_limit,
                "context": (order.metadata_ or {}).get("context"),
                "is_tariff_change": bool(
                    (order.metadata_ or {}).get("is_tariff_change")
                ),
            },
            "user_id": user.id,
            "telegram_id": user.telegram_id,
            "real_available": int(balance.real_available),
            "bonus_available": int(balance.bonus_available),
            "referrer": referrer,
        }


async def _find_unnotified_referrer(session, order: Order) -> dict | None:
    """Find a referral bonus minted by this order whose owner is reachable."""
    rows = (
        await session.execute(
            select(AccountLedgerEntry).where(
                AccountLedgerEntry.entry_type == "admin_adjustment",
                AccountLedgerEntry.amount > 0,
                AccountLedgerEntry.user_id != order.user_id,
                AccountLedgerEntry.metadata_["topup_order_id"].astext
                == str(order.id),
            )
        )
    ).scalars().all()
    for entry in rows:
        user = await session.get(User, entry.user_id)
        if user is None or not user.telegram_id or user.is_bot_blocked:
            continue
        return {
            "user_id": user.id,
            "telegram_id": user.telegram_id,
            "bonus": entry.amount,
        }
    return None


async def _finalize_delivery(
    order_id, *, owner_ok: bool, referrer_ok: bool, referrer_pending: bool
) -> bool:
    """Re-check the debt under a brief row lock, then clear or count."""
    async with session_scope() as session:
        order = await session.get(Order, order_id, with_for_update=True)
        if order is None:
            return False
        meta = dict(order.metadata_ or {})
        if not meta.get(LATE_NOTIFY_PENDING_KEY):
            # Delivered concurrently (webhook/button) while we were sending.
            return False
        if order.status != "paid":
            mark_notified(order)
            logger.info(
                "Credit-notify drops order %s: status is %s, not paid",
                order.id,
                order.status,
            )
            return False
        if not owner_ok:
            return _note_failed_attempt(order, meta) is not None
        mark_notified(order)
        if referrer_pending and referrer_ok:
            mark_referrer_notified(order)
        logger.info(
            "Credit-notify delivered late push for order %s", order.id
        )
        return True


async def _drop_debt(order_id) -> None:
    """Clear a dead debt under a brief row lock (user gone/blocked/refunded)."""
    async with session_scope() as session:
        order = await session.get(Order, order_id, with_for_update=True)
        if order is None:
            return
        meta = dict(order.metadata_ or {})
        if not meta.get(LATE_NOTIFY_PENDING_KEY):
            return
        mark_notified(order)
        logger.info("Credit-notify drops dead debt for order %s", order.id)


async def _finalize_attempt(order_id) -> None:
    """Bounded attempt accounting for the anomaly path (no credit)."""
    async with session_scope() as session:
        order = await session.get(Order, order_id, with_for_update=True)
        if order is None:
            return
        meta = dict(order.metadata_ or {})
        if not meta.get(LATE_NOTIFY_PENDING_KEY):
            return
        if _note_failed_attempt(order, meta) is None:
            logger.warning(
                "Credit-notify gives up on anomalous order %s", order.id
            )


def _note_failed_attempt(order: Order, meta: dict) -> int | None:
    """Record one failed delivery attempt for a debted order.

    Returns the attempt number, or None when the budget is exhausted (the
    debt is dropped via mark_notified so the order stops being selected).
    """
    attempts = int(meta.get(LATE_NOTIFY_ATTEMPTS_KEY, 0) or 0) + 1
    if attempts >= MAX_NOTIFY_ATTEMPTS:
        mark_notified(order)
        return None
    meta[LATE_NOTIFY_ATTEMPTS_KEY] = attempts
    order.metadata_ = meta
    return attempts
