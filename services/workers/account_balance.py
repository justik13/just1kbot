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
from config.settings import get_settings  # noqa: F401
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


async def _render_topup_payment_url(bot: Bot, chat_id: int, payload: dict) -> int | None:
    purl = payload.get("payment_url")
    pid = payload.get("payment_id")
    amount = payload.get("amount", 0)
    msg_id = payload.get("message_id")
    text = texts.UI_SERVICES_WORKERS_ACCOUNT_BALANCE_L295_1.format(value_0=int(amount))
    keyboard = get_topup_payment_keyboard(purl, pid)
    return await render_hub(bot, chat_id, text, keyboard, trigger_message_id=msg_id)


async def _render_balance_credit(bot: Bot, chat_id: int, payload: dict) -> int | None:
    await global_send_limiter.acquire()
    amount = payload.get("amount", 0)
    user_id = payload.get("user_id")
    topup_ctx = payload.get("topup_context") or {}
    async with session_scope() as session:
        balance = await get_account_balance(session, user_id=user_id) if user_id else None

    real_avail = int(balance.real_available) if balance else 0
    bonus_avail = int(balance.bonus_available) if balance else 0
    resume = bool(topup_ctx.get("operation"))
    suffix = texts.RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L340_1 if resume else ""
    message = texts.RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L345_1.format(
        value_0=int(amount),
        value_1=real_avail,
        value_2=bonus_avail,
        value_3=suffix,
    )
    if topup_ctx.get("purchaser_welcome_bonus", 0) > 0:
        wb = topup_ctx["purchaser_welcome_bonus"]
        message += (
            f"\n\n🎁 <b>Вам начислен приветственный бонус +{wb} ₽ "
            f"за первое пополнение по приглашению!</b>"
        )
    credit_keyboard = get_topup_credit_keyboard(topup_ctx)
    return await render_hub(
        bot,
        chat_id,
        message,
        credit_keyboard,
        message_effect_id="5046509860389126442",
    )


async def _render_referral_bonus(bot: Bot, chat_id: int, payload: dict) -> int | None:
    await global_send_limiter.acquire()
    bonus = payload.get("bonus") or payload.get("referrer_bonus", 0)
    builder = InlineKeyboardBuilder()
    builder.button(text="🎁 Мой баланс", callback_data="menu_balance")
    ref_text = f"🎉 <b>Ваш реферал пополнил баланс!</b>\n\nВам зачислено <b>+{int(bonus)} ₽</b> бонусов на баланс."
    return await render_hub(
        bot,
        chat_id,
        ref_text,
        builder.as_markup(),
        message_effect_id="5104841245755180586",
    )


async def process_topup_link_presentations(bot: Bot) -> int:
    from services.notification_coordinator import (
        claim_notification,
        ensure_payment_notification,
        execute_notification_presentation,
    )

    # 1. Backfill outbox for any active unnotified payments
    async with session_scope() as session:
        candidates = (
            await session.execute(
                select(
                    Payment.id,
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
        for pid, amount, purl, topup_ctx, user_tg_id in candidates:
            ctx = dict(topup_ctx or {})
            if not ctx.get("auto_show", True) or not purl:
                continue
            chat_id = int(ctx.get("chat_id") or user_tg_id)
            await ensure_payment_notification(
                session,
                payment_id=pid,
                kind="payment_url",
                chat_id=chat_id,
                payload_snapshot={
                    "payment_url": purl,
                    "payment_id": pid,
                    "amount": int(amount),
                    "message_id": ctx.get("message_id"),
                },
            )

    # 2. Claim and present via unified coordinator
    presented = 0
    for _ in range(BALANCE_NOTIFICATION_BATCH):
        async with session_scope() as session:
            claim = await claim_notification(session, worker_id="topup_url_worker")
        if claim is None:
            break
        if claim.kind == "payment_url":
            ok = await execute_notification_presentation(bot, claim, render_func=_render_topup_payment_url)
            if ok:
                presented += 1
        elif claim.kind in ("balance_credit", "referral_bonus"):
            render_f = _render_balance_credit if claim.kind == "balance_credit" else _render_referral_bonus
            await execute_notification_presentation(bot, claim, render_func=render_f)

    return presented


present_topup_urls = process_topup_link_presentations


async def process_balance_notifications(bot: Bot) -> int:
    from sqlalchemy import and_, or_

    async with session_scope() as session:
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
                await render_hub(
                    bot,
                    ref_id,
                    ref_text,
                    b_builder.as_markup(),
                    message_effect_id="5104841245755180586",
                )
                ref_sent = True
            except TelegramForbiddenError:
                ref_blocked = True
            except Exception as exc:
                logger.warning("Failed to deliver durable referrer push to %s: %s", ref_id, exc)

            if ref_sent or ref_blocked:
                now_str = now_utc().isoformat()
                async with session_scope() as session:
                    p_db = await session.scalar(select(Payment).where(Payment.id == pid)) or await session.get(Payment, pid)
                    if p_db:
                        cur_ctx = dict(p_db.topup_context or {})
                        p_db.topup_context = {
                            **cur_ctx,
                            "referrer_notified_at": now_str,
                            "referrer_bot_blocked": ref_blocked,
                        }
                if p:
                    cur_ctx = dict(p.topup_context or {})
                    p.topup_context = {
                        **cur_ctx,
                        "referrer_notified_at": now_str,
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

            async with session_scope() as session:
                p_db = await session.scalar(select(Payment).where(Payment.id == pid)) or await session.get(Payment, pid)
                if p_db and p_db.credit_notified_at is None:
                    p_db.credit_notified_at = now_utc()
            if p:
                p.credit_notified_at = now_utc()
            continue

        # Calculate message text in short read session
        async with session_scope() as session:
            balance = await get_account_balance(session, user_id=uid)

        resume = bool(ctx.get("operation"))
        suffix = texts.RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L340_1 if resume else ""
        real_avail = int(balance.real_available) if balance else 0
        bonus_avail = int(balance.bonus_available) if balance else 0
        message = texts.RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L345_1.format(
            value_0=int(amount or 0),
            value_1=real_avail,
            value_2=bonus_avail,
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
            await render_hub(
                bot,
                telegram_id,
                message,
                credit_keyboard,
                message_effect_id="5046509860389126442",
            )
            user_sent = True
        except TelegramForbiddenError:
            user_blocked = True
        except Exception as exc:
            logger.warning("Failed to send balance credit push to %s: %s", telegram_id, exc)

        if user_sent or user_blocked:
            async with session_scope() as session:
                p_db = await session.scalar(select(Payment).where(Payment.id == pid)) or await session.get(Payment, pid)
                if p_db:
                    p_db.credit_notified_at = now_utc()
            if p:
                p.credit_notified_at = now_utc()
            delivered += 1
            if user_blocked:
                async with session_scope() as session:
                    await mark_user_bot_blocked(session, telegram_id)

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