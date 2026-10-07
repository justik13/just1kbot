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
from decimal import Decimal, InvalidOperation

from aiogram import Bot
from sqlalchemy import func, or_, select

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
    REFERRER_PENDING_KEY,
    mark_notified,
    mark_referrer_notified,
)
from utils.telegram import EFFECT_CONFETTI, EFFECT_FIRE, safe_send_message

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
                    or_(
                        Order.metadata_[LATE_NOTIFY_PENDING_KEY].astext
                        == "true",
                        Order.metadata_.has_key(REFERRER_PENDING_KEY),
                    ),
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
        bonus = int(referrer["bonus"])
        rate_pct = referrer.get("bonus_rate_pct")
        bonus_balance = referrer.get("bonus_balance")

        if rate_pct is not None and bonus_balance is not None:
            text = texts.REFERRAL_BONUS_ACCREDITED_DETAILED.format(
                bonus=bonus,
                rate_pct=rate_pct,
                bonus_balance=bonus_balance,
            )
        else:
            text = texts.REFERRAL_BONUS_ACCREDITED.format(
                bonus=bonus
            )

        sent_id = await safe_send_message(
            bot,
            referrer["telegram_id"],
            text,
            reply_markup=get_referral_bonus_keyboard(),
            message_effect_id=EFFECT_FIRE,
        )

        if referrer.get("tier_upgraded") and referrer.get("new_tier_name"):
            upgrade_text = texts.REFERRAL_TIER_UPGRADE_NOTIFICATION.format(
                tier_name=referrer["new_tier_name"],
                rate_pct=referrer.get("new_rate_pct", 20),
            )
            await safe_send_message(
                bot,
                referrer["telegram_id"],
                upgrade_text,
                message_effect_id=EFFECT_FIRE,
            )

        return sent_id is not None
    except Exception as exc:
        logger.warning("Credit-notify referrer send failed: %s", exc)
        return False


async def _deliver_one(bot: Bot, order_id) -> bool:
    """Deliver outstanding pushes for one order. True if anything delivered.

    Owner and referrer debts are fully independent: the webhook/button may
    clear the owner debt first without touching the referrer one.

    Three phases so the row lock is never held during Telegram I/O:

    1. Snapshot (short read-only session): eligibility + plain scalars.
    2. Send (no session at all): owner push and/or referrer push.
    3. Finalize (brief locked session): re-check each debt is still open
       (a concurrent webhook/button may have delivered first), then clear
       markers or record the failed attempt.
    """
    snap = await _snapshot(order_id)
    if snap is None:
        return False
    owner, ref = snap["owner"], snap["referrer"]
    if owner is None and ref is None:
        return False
    if owner is not None and owner["kind"] == "wait":
        return False
    if owner is not None and owner["kind"] == "anomaly":
        await _finalize_attempt(order_id)
        return False
    if owner is not None and owner["kind"] == "drop":
        await _drop_debts(order_id, owner=True, referrer=False)
        owner = None
        if ref is None:
            return False
    if ref is not None and ref["kind"] == "drop":
        await _drop_debts(order_id, owner=False, referrer=True)
        ref = None
        if owner is None:
            return False

    owner_ok = (
        await _send_late_push(
            bot,
            owner["service_type"],
            owner["fields"],
            owner["telegram_id"],
            owner["real_available"],
            owner["bonus_available"],
        )
        if owner is not None
        else None
    )
    ref_ok = (
        await _send_referrer_push(bot, ref) if ref is not None else None
    )
    return await _finalize_delivery(order_id, owner_ok, ref_ok)


async def _snapshot(order_id) -> dict | None:
    """Read-only snapshot of both debts. None = nothing to do (no writes)."""
    async with session_scope() as session:
        order = await session.get(Order, order_id)
        if order is None:
            return None
        meta = dict(order.metadata_ or {})
        owner = None
        if meta.get(LATE_NOTIFY_PENDING_KEY):
            # Status is re-checked under lock at finalize time as well; an
            # early drop here avoids wasting a send on a refunded order.
            if order.status != "paid":
                owner = {"kind": "drop"}
            elif meta.get("settlement_held"):
                # Still held (no release yet): money has not moved, stay
                # silent and do not consume attempts — the release re-arms.
                owner = {"kind": "wait"}
            else:
                # Never notify without an actual credit in the ledger.
                has_credit = await session.scalar(
                    select(func.count(AccountLedgerEntry.id)).where(
                        AccountLedgerEntry.order_id == order.id,
                        AccountLedgerEntry.entry_type == "payment_credit",
                    )
                )
                if not has_credit:
                    logger.warning(
                        "Credit-notify skips order %s: paid but no "
                        "payment_credit (pre-flight 3B owns this anomaly)",
                        order.id,
                    )
                    owner = {"kind": "anomaly"}
                else:
                    user = await session.get(User, order.user_id)
                    # Already synced as blocked by another path (broadcast,
                    # WI traffic, expiry notifications): do not burn attempts
                    # on a walled user.
                    if (
                        user is None
                        or not user.telegram_id
                        or user.is_bot_blocked
                    ):
                        owner = {"kind": "drop"}
                    else:
                        balance = await get_account_balance(
                            session, user_id=user.id
                        )
                        owner = {
                            "kind": "send",
                            "service_type": order.service_type,
                            "fields": {
                                "id": order.id,
                                "amount_rub": order.amount_rub,
                                "duration_days": order.duration_days,
                                "device_limit": order.device_limit,
                                "context": (order.metadata_ or {}).get(
                                    "context"
                                ),
                                "is_tariff_change": bool(
                                    (order.metadata_ or {}).get(
                                        "is_tariff_change"
                                    )
                                ),
                            },
                            "telegram_id": user.telegram_id,
                            "real_available": int(balance.real_available),
                            "bonus_available": int(balance.bonus_available),
                        }

        referrer = None
        debt = meta.get(REFERRER_PENDING_KEY)
        if isinstance(debt, dict) and debt.get("user_id") is not None:
            ref_user = await session.get(User, debt["user_id"])
            try:
                bonus = int(Decimal(str(debt.get("bonus", "0"))))
            except (InvalidOperation, TypeError, ValueError):
                bonus = 0
            if (
                ref_user is None
                or not ref_user.telegram_id
                or ref_user.is_bot_blocked
                or bonus <= 0
            ):
                referrer = {"kind": "drop"}
            else:
                ref_balance = await get_account_balance(session, user_id=ref_user.id)
                referrer = {
                    "kind": "send",
                    "telegram_id": ref_user.telegram_id,
                    "bonus": bonus,
                    "bonus_balance": int(ref_balance.bonus_available),
                    "bonus_rate_pct": debt.get("bonus_rate_pct"),
                    "tier_upgraded": bool(debt.get("tier_upgraded")),
                    "new_tier_name": debt.get("new_tier_name"),
                    "new_rate_pct": debt.get("new_rate_pct"),
                }

        if owner is None and referrer is None:
            return None
        return {"owner": owner, "referrer": referrer}


async def _finalize_delivery(
    order_id, owner_ok: bool | None, ref_ok: bool | None
) -> bool:
    """Re-check each debt under a brief row lock, then clear or count."""
    delivered = False
    async with session_scope() as session:
        order = await session.get(Order, order_id, with_for_update=True)
        if order is None:
            return False
        meta = dict(order.metadata_ or {})

        if owner_ok is not None and meta.get(LATE_NOTIFY_PENDING_KEY):
            if order.status != "paid":
                mark_notified(order)
                logger.info(
                    "Credit-notify drops order %s: status is %s, not paid",
                    order.id,
                    order.status,
                )
            elif owner_ok:
                mark_notified(order)
                logger.info(
                    "Credit-notify delivered late push for order %s", order.id
                )
                delivered = True
            elif _note_failed_attempt(order, meta) is None:
                mark_referrer_notified(order)
                logger.warning(
                    "Credit-notify gives up on order %s", order.id
                )

        if ref_ok is not None and meta.get(REFERRER_PENDING_KEY):
            if order.status != "paid":
                mark_referrer_notified(order)
            elif ref_ok:
                mark_referrer_notified(order)
                logger.info(
                    "Credit-notify delivered referrer push for order %s",
                    order.id,
                )
                delivered = True
            elif _note_failed_attempt(order, meta) is None:
                mark_notified(order)
                mark_referrer_notified(order)
                logger.warning(
                    "Credit-notify gives up on referrer push for order %s",
                    order.id,
                )
    return delivered


async def _drop_debts(order_id, *, owner: bool, referrer: bool) -> None:
    """Clear dead debts under a brief row lock."""
    async with session_scope() as session:
        order = await session.get(Order, order_id, with_for_update=True)
        if order is None:
            return
        if owner:
            mark_notified(order)
        if referrer:
            mark_referrer_notified(order)
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
