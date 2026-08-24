"""Durable, retryable user notifications for credited top-ups."""

import asyncio
import logging

from aiogram import Bot
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from bot import texts
from bot.constants import WORKER_ERROR_SLEEP_INTERVAL
from bot.keyboards import get_topup_credit_keyboard, get_topup_payment_keyboard
from config.settings import get_settings  # noqa: F401
from database.connection import session_scope
from database.models import Payment, TariffQuote, TariffVersion, User
from database.repositories.account_ledger_repo import get_account_balance
from utils.rate_limiter import global_send_limiter

logger = logging.getLogger("AccountBalanceNotifications")
BALANCE_NOTIFICATION_INTERVAL = 10.0
BALANCE_NOTIFICATION_BATCH = 50


async def _render_account_purchase(bot: Bot, chat_id: int, payload: dict) -> int | None:
    await global_send_limiter.acquire()
    operation_type = payload.get("operation_type")
    resulting_paid_hours = payload.get("resulting_paid_hours", 0)
    resulting_bonus_hours = payload.get("resulting_bonus_hours", 0)
    duration_hours = payload.get("duration_hours", 0)
    device_limit = payload.get("device_limit", 0)

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

    msg = await bot.send_message(
        chat_id,
        texts.UI_SERVICES_WORKERS_ACCOUNT_BALANCE_L232_1.format(
            value_0=title,
            value_1=duration,
            value_2=device_limit,
        ),
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    return msg.message_id


async def process_balance_purchase_notifications(bot: Bot) -> int:
    from services.notification_coordinator import (
        claim_notification,
        ensure_payment_notification,
        execute_notification_presentation,
        safe_begin_nested,
    )

    # 1. Backfill outbox for any consumed quotes that have not been notified
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
        for (
            quote_id,
            telegram_id,
            op_type,
            paid_h,
            bonus_h,
            dur_h,
            dev_lim,
        ) in rows:
            try:
                async with safe_begin_nested(session):
                    await ensure_payment_notification(
                        session,
                        quote_id=quote_id,
                        kind="account_purchase",
                        chat_id=telegram_id,
                        payload_snapshot={
                            "quote_id": quote_id,
                            "operation_type": op_type,
                            "resulting_paid_hours": paid_h,
                            "resulting_bonus_hours": bonus_h,
                            "duration_hours": dur_h,
                            "device_limit": dev_lim,
                        },
                    )
            except Exception as row_exc:
                logger.error("Failed to backfill purchase notification for quote %s: %s", quote_id, row_exc)

    delivered = 0
    for _ in range(BALANCE_NOTIFICATION_BATCH):
        async with session_scope() as session:
            claim = await claim_notification(
                session, worker_id="quote_purchase_worker", kind="account_purchase"
            )
        if claim is None:
            break
        ok = await execute_notification_presentation(
            bot, claim, render_func=_render_account_purchase
        )
        if ok:
            delivered += 1
    return delivered


async def _render_topup_payment_url(bot: Bot, chat_id: int, payload: dict) -> int | None:
    from utils.telegram import render_hub

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

    auto_status = topup_ctx.get("auto_fulfill_status")
    auto_action = topup_ctx.get("auto_fulfill_action")

    if auto_status == "succeeded":
        builder = InlineKeyboardBuilder()
        builder.button(text="📱 Мои подключения", callback_data="menu_connections")
        builder.button(text="📋 Подписка", callback_data="menu_subscription")
        builder.adjust(1)
        if auto_action == "tariff_change":
            message = (
                "🎉 <b>Оплата получена и тариф успешно обновлен!</b>\n\n"
                "Ваш новый тариф активирован. Настройки подписки и подключений обновлены."
            )
        else:
            message = (
                "🎉 <b>Оплата получена и подписка успешно оформлена!</b>\n\n"
                "Ваши VPN-ключи и настройки подключений доступны в меню «Мои подключения»."
            )
        from utils.telegram import render_hub
        return await render_hub(
            bot,
            chat_id,
            message,
            builder.as_markup(),
            message_effect_id="5046509860389126442",
        )

    real_avail = 0
    bonus_avail = 0
    try:
        from database.connection import session_scope
        async with session_scope() as session:
            balance = await get_account_balance(session, user_id=user_id) if user_id else None
            if balance:
                real_avail = int(balance.real_available)
                bonus_avail = int(balance.bonus_available)
    except Exception:
        pass
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
    from utils.telegram import render_hub
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
    from utils.telegram import render_hub
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
        safe_begin_nested,
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
            try:
                async with safe_begin_nested(session):
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
                            "trigger_message_id": ctx.get("message_id"),
                        },
                    )
            except Exception as purl_exc:
                logger.error("Failed to backfill payment_url notification for payment %s: %s", pid, purl_exc)

    # 2. Claim and present via unified coordinator
    presented = 0
    for _ in range(BALANCE_NOTIFICATION_BATCH):
        async with session_scope() as session:
            claim = await claim_notification(
                session, worker_id="topup_url_worker", kind="payment_url"
            )
        if claim is None:
            break
        ok = await execute_notification_presentation(
            bot, claim, render_func=_render_topup_payment_url
        )
        if ok:
            presented += 1

    return presented


present_topup_urls = process_topup_link_presentations


async def process_balance_notifications(bot: Bot) -> int:
    from sqlalchemy import and_, or_

    from services.notification_coordinator import (
        claim_notification,
        ensure_payment_notification,
        execute_notification_presentation,
        safe_begin_nested,
    )

    # 1. Backfill outbox for any pending credit / referral notifications
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

            ctx = dict(topup_ctx or {})

            # Backfill referral bonus outbox
            ref_id_raw = ctx.get("referrer_telegram_id")
            ref_bonus_raw = ctx.get("referrer_bonus", 0)
            if ref_id_raw and ref_bonus_raw and ctx.get("referrer_notified_at") is None:
                try:
                    ref_id = int(ref_id_raw)
                    ref_bonus = int(ref_bonus_raw)
                    if ref_id > 0 and ref_bonus > 0:
                        async with safe_begin_nested(session):
                            await ensure_payment_notification(
                                session,
                                payment_id=pid,
                                kind="referral_bonus",
                                chat_id=ref_id,
                                payload_snapshot={
                                    "bonus": ref_bonus,
                                    "referrer_bonus": ref_bonus,
                                    "payment_id": pid,
                                },
                            )
                except (ValueError, TypeError):
                    pass

            # Backfill balance credit outbox
            if credit_notified is None and telegram_id:
                # If auto_fulfill was triggered, verify quote was consumed and notified
                if ctx.get("auto_fulfill_status") == "succeeded":
                    quote_raw = ctx.get("quote_public_id")
                    if quote_raw:
                        import uuid
                        try:
                            quote_uuid = uuid.UUID(str(quote_raw))
                            quote = await session.scalar(
                                select(TariffQuote).where(TariffQuote.public_id == quote_uuid)
                            )
                            if quote is None or quote.purchase_notified_at is None:
                                continue
                        except Exception as exc:
                            logger.warning("Error checking quote for auto-fulfilled payment %s: %s", pid, exc)
                            continue

                try:
                    async with safe_begin_nested(session):
                        await ensure_payment_notification(
                            session,
                            payment_id=pid,
                            kind="balance_credit",
                            chat_id=telegram_id,
                            payload_snapshot={
                                "amount": int(amount or 0),
                                "user_id": uid,
                                "topup_context": ctx,
                            },
                        )
                except Exception as credit_err:
                    logger.error("Failed to backfill credit notification for payment %s: %s", pid, credit_err)

    # 2. Claim and execute via unified coordinator
    delivered = 0
    for _ in range(BALANCE_NOTIFICATION_BATCH):
        async with session_scope() as session:
            claim = await claim_notification(session, worker_id="balance_worker")
        if claim is None:
            break
        if claim.kind == "balance_credit":
            ok = await execute_notification_presentation(
                bot, claim, render_func=_render_balance_credit
            )
            if ok:
                delivered += 1
        elif claim.kind == "referral_bonus":
            ok = await execute_notification_presentation(
                bot, claim, render_func=_render_referral_bonus
            )
            if ok:
                delivered += 1
        elif claim.kind == "payment_url":
            ok = await execute_notification_presentation(
                bot, claim, render_func=_render_topup_payment_url
            )
            if ok:
                delivered += 1
        elif claim.kind == "account_purchase":
            ok = await execute_notification_presentation(
                bot, claim, render_func=_render_account_purchase
            )
            if ok:
                delivered += 1

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
