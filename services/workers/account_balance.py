"""Durable, retryable user notifications for credited top-ups."""

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from bot import texts
from bot.constants import WORKER_ERROR_SLEEP_INTERVAL
from bot.keyboards import get_topup_credit_keyboard, get_topup_payment_keyboard
from config.settings import get_settings
from database.connection import session_scope
from database.models import Payment, TariffQuote, TariffVersion, User
from database.repositories.account_ledger_repo import get_account_balance
from database.repositories.users_repo import mark_user_bot_blocked
from utils.datetime_helpers import now_utc
from utils.rate_limiter import global_send_limiter
from utils.telegram import render_hub

logger = logging.getLogger("AccountBalanceNotifications")
BALANCE_NOTIFICATION_INTERVAL = 10.0
BALANCE_NOTIFICATION_BATCH = 50


async def process_balance_purchase_notifications(bot: Bot) -> int:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(
                    TariffQuote.id,
                    User.telegram_id,
                    TariffQuote.operation_type,
                    TariffQuote.resulting_paid_hours,
                    TariffQuote.resulting_bonus_hours,
                    TariffVersion.duration_hours,
                    TariffVersion.device_limit,
                )
                .join(User, User.id == TariffQuote.user_id)
                .join(
                    TariffVersion,
                    TariffVersion.id == TariffQuote.target_tariff_version_id,
                )
                .where(
                    TariffQuote.status == "consumed",
                    TariffQuote.operation_type.in_(
                        ("purchase", "renew", "change")
                    ),
                    TariffQuote.purchase_notified_at.is_(None),
                )
                .order_by(TariffQuote.id)
                .limit(BALANCE_NOTIFICATION_BATCH)
            )
        ).all()

    delivered = 0
    for (
        quote_id,
        telegram_id,
        operation_type,
        resulting_paid_hours,
        resulting_bonus_hours,
        duration_hours,
        device_limit,
    ) in rows:
        try:
            await global_send_limiter.acquire()
            hours = (
                resulting_paid_hours + resulting_bonus_hours
                if operation_type == "change"
                else duration_hours
            )
            days, remainder = divmod(hours, 24)
            duration = texts.RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L223_1.format(
                value_0=days
            ) + (
                texts.RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L224_1.format(
                    value_0=remainder
                )
                if remainder
                else ""
            )
            title = (
                texts.RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L227_1
                if operation_type == "change"
                else texts.RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L229_1
            )
            builder = InlineKeyboardBuilder()
            builder.button(
                text="🏠 Главное меню",
                callback_data="back_to_main_menu",
            )
            builder.button(
                text="✅ Прочитано",
                callback_data="dismiss_notification",
            )
            builder.adjust(2)

            await bot.send_message(
                telegram_id,
                texts.UI_SERVICES_WORKERS_ACCOUNT_BALANCE_L232_1.format(
                    value_0=title,
                    value_1=duration,
                    value_2=device_limit,
                ),
                reply_markup=builder.as_markup(),
                parse_mode="HTML",
            )
        except TelegramForbiddenError:
            async with session_scope() as session:
                await mark_user_bot_blocked(session, telegram_id)
        except Exception:
            logger.exception(
                "Failed to notify balance purchase quote=%s", quote_id
            )
            continue
        async with session_scope() as session:
            quote = await session.scalar(
                select(TariffQuote)
                .where(TariffQuote.id == quote_id)
                .with_for_update()
            )
            if quote and quote.purchase_notified_at is None:
                quote.purchase_notified_at = now_utc()
        delivered += 1
    return delivered


async def process_topup_link_presentations(bot: Bot) -> int:
    async with session_scope() as session:
        candidates = (
            await session.execute(
                select(
                    Payment.id,
                    Payment.user_id,
                    Payment.amount,
                    Payment.payment_url,
                    Payment.topup_context,
                    User.telegram_id,
                )
                .join(User, User.id == Payment.user_id)
                .where(
                    Payment.payment_url.is_not(None),
                    Payment.payment_url_notified_at.is_(None),
                    Payment.checkout_status == "active",
                    Payment.ui_visible.is_(True),
                )
                .order_by(Payment.id)
                .limit(BALANCE_NOTIFICATION_BATCH)
            )
        ).all()

    presented = 0
    for pid, uid, amount, purl, topup_ctx, user_tg_id in candidates:
        ctx = dict(topup_ctx or {})
        if not ctx.get("auto_show") or not purl:
            continue

        chat_id = int(ctx.get("chat_id") or user_tg_id)
        text = texts.UI_SERVICES_WORKERS_ACCOUNT_BALANCE_L295_1.format(
            value_0=int(amount)
        )
        keyboard = get_topup_payment_keyboard(purl, pid)

        # Lock-Free External Telegram I/O
        new_msg_id = None
        bot_blocked = False
        try:
            new_msg_id = await render_hub(bot, chat_id, text, keyboard)
        except TelegramForbiddenError:
            bot_blocked = True
        except Exception:
            logger.exception("Failed to present top-up URL payment=%s", pid)
            continue

        # Short Post-Send Verification & Acknowledge Transaction
        needs_compensation = False
        async with session_scope() as session:
            from database.repositories.tariff_quotes_repo import lock_checkout_user
            await lock_checkout_user(session, uid)
            payment = await session.get(Payment, pid)
            if payment is None:
                needs_compensation = True
            elif bot_blocked:
                await mark_user_bot_blocked(session, user_tg_id)
                payment.payment_url_notified_at = now_utc()
            elif payment.checkout_status != "active" or not payment.ui_visible:
                needs_compensation = True
                payment.payment_url_notified_at = now_utc()
            else:
                payment.payment_url_notified_at = now_utc()
                payment.topup_context = {
                    **(payment.topup_context or {}),
                    "message_id": new_msg_id,
                    "auto_show": False,
                }
                presented += 1

        # Lock-Free Compensation if checkout was abandoned concurrently
        if needs_compensation and new_msg_id:
            from utils.telegram import _delete_hub_messages
            try:
                await _delete_hub_messages(bot, chat_id, [new_msg_id])
            except Exception as exc:
                logger.warning("Failed to compensate orphan topup message %s: %s", new_msg_id, exc)

    return presented


present_topup_urls = process_topup_link_presentations


async def process_balance_notifications(bot: Bot) -> int:
    async with session_scope() as session:
        from sqlalchemy import or_, and_
        rows = (
            await session.execute(
                select(
                    Payment.id,
                    Payment.user_id,
                    Payment.amount,
                    Payment.topup_context,
                    Payment.credit_notified_at,
                    User.telegram_id,
                )
                .join(User, User.id == Payment.user_id)
                .where(
                    Payment.credited_at.is_not(None),
                    or_(
                        Payment.credit_notified_at.is_(None),
                        and_(
                            Payment.topup_context["referrer_telegram_id"].as_string().is_not(None),
                            Payment.topup_context["referrer_notified_at"].as_string().is_(None),
                        ),
                    ),
                )
                .order_by(Payment.credited_at, Payment.id)
                .limit(BALANCE_NOTIFICATION_BATCH)
            )
        ).all()

    delivered = 0
    for row in rows:
        if isinstance(row, (tuple, list)) and len(row) >= 6:
            pid, uid, amount, topup_ctx, credit_notified, telegram_id = row[:6]
        elif isinstance(row, (tuple, list)) and len(row) >= 2:
            pid, telegram_id = row[0], row[1]
            uid = None
            amount = None
            topup_ctx = None
            credit_notified = None
        else:
            continue

        async with session_scope() as session:
            p = await session.scalar(select(Payment).where(Payment.id == pid))
            if p is None:
                p = await session.get(Payment, pid)
            if p is None:
                continue
            if uid is None:
                uid = p.user_id
            if amount is None:
                amount = p.amount
            if topup_ctx is None:
                topup_ctx = p.topup_context
            if credit_notified is None:
                credit_notified = p.credit_notified_at

        ctx = dict(topup_ctx or {})

        # 1. Handle pending referrer push lock-free
        ref_id_raw = ctx.get("referrer_telegram_id")
        ref_bonus_raw = ctx.get("referrer_bonus", 0)
        ref_id: int | None = None
        ref_bonus: int = 0
        try:
            if ref_id_raw is not None:
                parsed_id = int(ref_id_raw)
                if parsed_id > 0:
                    ref_id = parsed_id
            if ref_bonus_raw is not None:
                parsed_bonus = int(ref_bonus_raw)
                if parsed_bonus > 0:
                    ref_bonus = parsed_bonus
        except (ValueError, TypeError):
            ref_id = None
            ref_bonus = 0

        if ref_id and ref_bonus > 0 and ctx.get("referrer_notified_at") is None:
            ref_blocked = False
            ref_sent = False
            try:
                await global_send_limiter.acquire()
                from aiogram.utils.keyboard import InlineKeyboardBuilder
                b_builder = InlineKeyboardBuilder()
                b_builder.button(text="🎁 Мой баланс", callback_data="menu_balance")
                ref_text = f"🎉 <b>Ваш реферал пополнил баланс!</b>\n\nВам зачислено <b>+{ref_bonus} ₽</b> бонусов на баланс."
                await render_hub(bot, ref_id, ref_text, b_builder.as_markup())
                ref_sent = True
            except TelegramForbiddenError:
                ref_blocked = True
            except Exception as exc:
                logger.warning("Failed to deliver durable referrer push to %s: %s", ref_id, exc)

            if ref_sent or ref_blocked:
                async with session_scope() as session:
                    p = await session.scalar(select(Payment).where(Payment.id == pid)) or await session.get(Payment, pid)
                    if p:
                        cur_ctx = dict(p.topup_context or {})
                        p.topup_context = {
                            **cur_ctx,
                            "referrer_notified_at": now_utc().isoformat(),
                            "referrer_bot_blocked": ref_blocked,
                        }

        # 2. Handle main credit push if pending
        if credit_notified is not None:
            continue

        if ctx.get("auto_fulfill_status") == "succeeded":
            quote_raw = ctx.get("quote_public_id")
            if quote_raw:
                import uuid
                from database.models import TariffQuote
                try:
                    quote_uuid = uuid.UUID(str(quote_raw))
                    async with session_scope() as session:
                        quote = await session.scalar(
                            select(TariffQuote).where(TariffQuote.public_id == quote_uuid)
                        )
                        if quote is None or quote.purchase_notified_at is None:
                            continue
                except Exception as exc:
                    logger.warning("Error checking quote for auto-fulfilled payment %s: %s", pid, exc)
                    continue

            p.credit_notified_at = now_utc()
            continue

        # Calculate message text in short read session
        async with session_scope() as session:
            balance = await get_account_balance(session, user_id=uid)

        resume = bool(ctx.get("operation"))
        suffix = texts.RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L340_1 if resume else ""
        message = texts.RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L345_1.format(
            value_0=int(amount),
            value_1=int(balance.real_available),
            value_2=int(balance.bonus_available),
            value_3=suffix,
        )
        if ctx.get("purchaser_welcome_bonus", 0) > 0:
            wb = ctx["purchaser_welcome_bonus"]
            message += (
                f"\n\n🎁 <b>Вам начислен приветственный бонус +{wb} ₽ "
                f"за первое пополнение по приглашению!</b>"
            )

        credit_keyboard = get_topup_credit_keyboard(ctx)

        # Lock-Free External Telegram I/O
        user_blocked = False
        user_sent = False
        try:
            await global_send_limiter.acquire()
            await render_hub(bot, telegram_id, message, credit_keyboard)
            user_sent = True
        except TelegramForbiddenError:
            user_blocked = True
        except Exception as exc:
            logger.warning("Failed to send balance credit push to %s: %s", telegram_id, exc)

        if user_sent or user_blocked:
            async with session_scope() as session:
                p = await session.scalar(select(Payment).where(Payment.id == pid)) or await session.get(Payment, pid)
                if p:
                    p.credit_notified_at = now_utc()
                    delivered += 1
                if user_blocked:
                    await mark_user_bot_blocked(session, telegram_id)

            if user_sent and balance.real_position > get_settings().BALANCE_MAX_AVAILABLE_RUB:
                diagnostic = texts.RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L361_1.format(
                    value_0=pid,
                    value_1=telegram_id,
                    value_2=int(balance.real_position),
                )
                diag_builder = InlineKeyboardBuilder()
                diag_builder.button(text="🔍 Пользователь", callback_data=f"admin_user_card:{telegram_id}")
                for admin_id in get_settings().ADMIN_IDS:
                    try:
                        await global_send_limiter.acquire()
                        await bot.send_message(
                            admin_id,
                            diagnostic,
                            reply_markup=diag_builder.as_markup(),
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
    return delivered


async def account_balance_notifications_loop(
    bot: Bot, shutdown_event: asyncio.Event
):
    while not shutdown_event.is_set():
        try:
            await process_balance_purchase_notifications(bot)
            await process_topup_link_presentations(bot)
            await process_balance_notifications(bot)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Account balance notification loop failed")
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(),
                    timeout=WORKER_ERROR_SLEEP_INTERVAL,
                )
                break
            except asyncio.TimeoutError:
                continue
        try:
            await asyncio.wait_for(
                shutdown_event.wait(),
                timeout=BALANCE_NOTIFICATION_INTERVAL,
            )
            break
        except asyncio.TimeoutError:
            continue