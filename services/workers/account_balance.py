"""Durable, retryable user notifications for credited top-ups."""

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import select

from bot.constants import WORKER_ERROR_SLEEP_INTERVAL
from bot.keyboards import get_topup_payment_keyboard
from config.settings import get_settings
from database.connection import session_scope
from database.models import Payment, User
from database.repositories.account_ledger_repo import get_account_balance
from database.repositories.users_repo import mark_user_bot_blocked
from utils.datetime_helpers import now_utc
from utils.rate_limiter import global_send_limiter
from utils.telegram import render_hub


logger = logging.getLogger("AccountBalanceNotifications")
BALANCE_NOTIFICATION_INTERVAL = 10.0
BALANCE_NOTIFICATION_BATCH = 50


async def process_topup_link_presentations(bot: Bot) -> int:
    async with session_scope() as session:
        payment_ids = list(
            (
                await session.scalars(
                    select(Payment.id)
                    .where(
                        Payment.payment_kind == "balance_topup",
                        Payment.payment_url.is_not(None),
                        Payment.payment_url_notified_at.is_(None),
                        Payment.ui_visible.is_(True),
                    )
                    .order_by(Payment.id)
                    .limit(BALANCE_NOTIFICATION_BATCH)
                )
            ).all()
        )
    presented = 0
    for payment_id in payment_ids:
        async with session_scope() as session:
            payment = await session.scalar(
                select(Payment)
                .where(Payment.id == payment_id)
                .with_for_update()
            )
            context = payment.topup_context or {} if payment else {}
            if (
                payment is None
                or payment.payment_url_notified_at is not None
                or not payment.ui_visible
                or not context.get("auto_show")
            ):
                continue
            user = await session.get(User, payment.user_id)
            chat_id = int(context.get("chat_id") or user.telegram_id)
            try:
                await render_hub(
                    bot,
                    chat_id,
                    "💳 <b>Ссылка на пополнение готова</b>\n\n"
                    f"Сумма: <b>{int(payment.amount)} ₽</b>\n\n"
                    "Перейдите на защищённую страницу ЮKassa.",
                    get_topup_payment_keyboard(payment.payment_url, payment.id),
                )
            except TelegramForbiddenError:
                await mark_user_bot_blocked(session, user.telegram_id)
            except Exception:
                logger.exception(
                    "Failed to present top-up URL payment=%s", payment.id
                )
                continue
            payment.payment_url_notified_at = now_utc()
            payment.topup_context = {**context, "auto_show": False}
            presented += 1
    return presented


async def process_balance_notifications(bot: Bot) -> int:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Payment.id, User.telegram_id)
                .join(User, User.id == Payment.user_id)
                .where(
                    Payment.payment_kind == "balance_topup",
                    Payment.credited_at.is_not(None),
                    Payment.credit_notified_at.is_(None),
                )
                .order_by(Payment.credited_at, Payment.id)
                .limit(BALANCE_NOTIFICATION_BATCH)
            )
        ).all()

    delivered = 0
    for payment_id, telegram_id in rows:
        async with session_scope() as session:
            payment = await session.scalar(
                select(Payment)
                .where(Payment.id == payment_id)
                .with_for_update()
            )
            if payment is None or payment.credit_notified_at is not None:
                continue
            balance = await get_account_balance(
                session, user_id=payment.user_id
            )
            resume = bool((payment.topup_context or {}).get("operation"))
            suffix = (
                "\n\nТариф готов к покупке. Подтвердите покупку с баланса."
                if resume
                else ""
            )
            message = (
                f"✅ <b>Баланс пополнен на {int(payment.amount)} ₽</b>\n"
                f"Доступно: <b>{int(balance.available)} ₽</b>{suffix}"
            )
            try:
                await global_send_limiter.acquire()
                await bot.send_message(telegram_id, message, parse_mode="HTML")
                if (
                    balance.accounting_position
                    > get_settings().BALANCE_MAX_AVAILABLE_RUB
                ):
                    diagnostic = (
                        "⚠️ Поздняя оплата превысила лимит баланса\n"
                        f"Payment: <code>{payment.id}</code>\n"
                        f"User: <code>{telegram_id}</code>\n"
                        f"Баланс: <b>{int(balance.accounting_position)} ₽</b>"
                    )
                    for admin_id in get_settings().ADMIN_IDS:
                        await global_send_limiter.acquire()
                        await bot.send_message(
                            admin_id, diagnostic, parse_mode="HTML"
                        )
            except TelegramForbiddenError:
                await mark_user_bot_blocked(session, telegram_id)
            except Exception:
                logger.exception(
                    "Failed to notify top-up credit payment=%s", payment.id
                )
                continue
            payment.credit_notified_at = now_utc()
            delivered += 1
    return delivered


async def account_balance_notifications_loop(
    bot: Bot, shutdown_event: asyncio.Event
):
    while not shutdown_event.is_set():
        try:
            await process_topup_link_presentations(bot)
            await process_balance_notifications(bot)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Account balance notification loop failed")
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(), timeout=WORKER_ERROR_SLEEP_INTERVAL
                )
                break
            except asyncio.TimeoutError:
                continue
        try:
            await asyncio.wait_for(
                shutdown_event.wait(), timeout=BALANCE_NOTIFICATION_INTERVAL
            )
            break
        except asyncio.TimeoutError:
            continue
