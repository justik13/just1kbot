"""Durable, retryable user notifications for credited top-ups."""

import asyncio
import logging
import uuid

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import or_, select, text

from bot.keyboards.notifications import (
    get_purchase_completed_notification_keyboard,
    get_referral_bonus_keyboard,
)
from bot.keyboards.payment import (
    get_topup_credit_keyboard,
    get_topup_payment_keyboard,
)
from bot.texts.runtime.alerts import ALERT_BALANCE_LIMIT_EXCEEDED
from bot.texts.runtime.notifications import (
    BALANCE_PURCHASE_SUCCESS_NOTIFICATION,
    BALANCE_TOPUP_CREDITED,
    BALANCE_TOPUP_RESUME_HINT,
    BALANCE_TOPUP_WELCOME_BONUS,
    REFERRAL_BONUS_ACCREDITED,
    TIME_DAYS_FORMAT,
    TIME_DAYS_HOURS_FORMAT,
    TITLE_SUBSCRIPTION_EXTENDED,
    TITLE_TARIFF_CHANGED,
    TOPUP_LINK_CARD,
)
from config.constants import WORKER_ERROR_SLEEP_INTERVAL
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
            duration = (
                TIME_DAYS_HOURS_FORMAT.format(days=days, hours=remainder)
                if remainder
                else TIME_DAYS_FORMAT.format(days=days)
            )
            title = (
                TITLE_TARIFF_CHANGED
                if operation_type == "change"
                else TITLE_SUBSCRIPTION_EXTENDED
            )
            await bot.send_message(
                telegram_id,
                BALANCE_PURCHASE_SUCCESS_NOTIFICATION.format(
                    title=title,
                    duration=duration,
                    device_limit=device_limit,
                ),
                reply_markup=get_purchase_completed_notification_keyboard(),
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
        payment_ids = list(
            (
                await session.scalars(
                    select(Payment.id)
                    .where(
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
            if user is None:
                continue
            chat_id = int(context.get("chat_id") or user.telegram_id)
            try:
                await render_hub(
                    bot,
                    chat_id,
                    TOPUP_LINK_CARD.format(amount=int(payment.amount)),
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
                    Payment.credited_at.is_not(None),
                    or_(
                        Payment.credit_notified_at.is_(None),
                        text(
                            "payments.topup_context->>'referrer_notified_at' IS NULL AND payments.topup_context->>'referrer_telegram_id' IS NOT NULL"
                        ),
                    ),
                )
                .order_by(Payment.credited_at, Payment.id)
                .limit(BALANCE_NOTIFICATION_BATCH)
            )
        ).all()

    delivered = 0
    for payment_id, telegram_id in rows:
        # Phase 1: Read payment details and check work needed within a short DB transaction
        async with session_scope() as session:
            payment = await session.scalar(
                select(Payment).where(Payment.id == payment_id)
            )
            if payment is None:
                continue

            ctx = dict(payment.topup_context or {})
            credit_already_notified = payment.credit_notified_at is not None
            is_auto_fulfilled = ctx.get("auto_fulfill_status") == "succeeded"
            quote_public_id = ctx.get("quote_public_id")
            payment_amount = payment.amount
            payment_user_id = payment.user_id

            # Referrer push analysis
            ref_id: int | None = None
            ref_bonus: int = 0
            ref_needed: bool = False
            ref_id_raw = ctx.get("referrer_telegram_id")
            ref_bonus_raw = ctx.get("referrer_bonus", 0)
            try:
                if ref_id_raw is not None and int(ref_id_raw) > 0:
                    ref_id = int(ref_id_raw)
                if ref_bonus_raw is not None and int(ref_bonus_raw) > 0:
                    ref_bonus = int(ref_bonus_raw)
                if ref_id and ref_bonus > 0 and ctx.get("referrer_notified_at") is None:
                    ref_needed = True
            except (ValueError, TypeError):
                logger.warning("Malformed referrer info in topup_context for payment %s", payment_id)

            ref_attempts = 0
            try:
                ref_attempts = int(ctx.get("referrer_notify_attempts") or 0)
            except (TypeError, ValueError):
                ref_attempts = 0

            # Quote handshake check for auto-fulfilled orders
            if is_auto_fulfilled and not credit_already_notified and quote_public_id:
                try:
                    quote_uuid = uuid.UUID(str(quote_public_id))
                    quote = await session.scalar(
                        select(TariffQuote).where(TariffQuote.public_id == quote_uuid)
                    )
                    if quote is not None and quote.purchase_notified_at is not None:
                        payment.credit_notified_at = now_utc()
                        credit_already_notified = True
                except Exception as exc:
                    logger.warning("Error checking quote for auto-fulfilled payment %s: %s", payment_id, exc)

            balance_snapshot = None
            if not credit_already_notified and not is_auto_fulfilled:
                balance_snapshot = await get_account_balance(session, user_id=payment_user_id)

        # Phase 2: Execute Referrer Push (outside DB transaction)
        if ref_needed and ref_id:
            ref_attempts += 1
            if ref_attempts > 5:
                logger.error(
                    "Referrer push exhausted max attempts (5) for payment %s, ref_id %s",
                    payment_id,
                    ref_id,
                )
                async with session_scope() as session:
                    p = await session.scalar(select(Payment).where(Payment.id == payment_id))
                    if p:
                        p_ctx = dict(p.topup_context or {})
                        p.topup_context = {
                            **p_ctx,
                            "referrer_notified_at": now_utc().isoformat(),
                            "referrer_notify_failed": True,
                            "referrer_notify_attempts": ref_attempts,
                        }
            else:
                ref_sent = False
                ref_blocked = False
                try:
                    await global_send_limiter.acquire()
                    ref_text = REFERRAL_BONUS_ACCREDITED.format(bonus=ref_bonus)
                    await render_hub(bot, ref_id, ref_text, get_referral_bonus_keyboard())
                    ref_sent = True
                except TelegramForbiddenError:
                    ref_blocked = True
                except Exception as exc:
                    logger.warning(
                        "Failed to deliver durable referrer push to %s (attempt %s/5): %s",
                        ref_id,
                        ref_attempts,
                        exc,
                    )

                async with session_scope() as session:
                    p = await session.scalar(select(Payment).where(Payment.id == payment_id))
                    if p:
                        p_ctx = dict(p.topup_context or {})
                        update_dict = {
                            **p_ctx,
                            "referrer_notify_attempts": ref_attempts,
                        }
                        if ref_sent:
                            update_dict["referrer_notified_at"] = now_utc().isoformat()
                        elif ref_blocked:
                            update_dict["referrer_notified_at"] = now_utc().isoformat()
                            update_dict["referrer_bot_blocked"] = True
                        p.topup_context = update_dict

        # Phase 3: Execute User Credit Push (outside DB transaction)
        if credit_already_notified or is_auto_fulfilled:
            continue

        if balance_snapshot is not None:
            resume_hint = BALANCE_TOPUP_RESUME_HINT if bool(ctx.get("operation")) else ""
            welcome_bonus = ""
            if ctx.get("purchaser_welcome_bonus", 0) > 0:
                wb = ctx["purchaser_welcome_bonus"]
                welcome_bonus = BALANCE_TOPUP_WELCOME_BONUS.format(bonus=wb)

            message = BALANCE_TOPUP_CREDITED.format(
                amount=int(payment_amount),
                real_balance=int(balance_snapshot.real_available),
                bonus_balance=int(balance_snapshot.bonus_available),
                resume_hint=resume_hint,
                welcome_bonus=welcome_bonus,
            )

            user_sent = False
            user_blocked = False
            try:
                await global_send_limiter.acquire()
                await render_hub(
                    bot,
                    telegram_id,
                    message,
                    get_topup_credit_keyboard(ctx),
                )
                if balance_snapshot.real_position > get_settings().BALANCE_MAX_AVAILABLE_RUB:
                    diagnostic = ALERT_BALANCE_LIMIT_EXCEEDED.format(
                        payment_id=payment_id,
                        telegram_id=telegram_id,
                        real_position=int(balance_snapshot.real_position),
                    )
                    for admin_id in get_settings().ADMIN_IDS:
                        await global_send_limiter.acquire()
                        await bot.send_message(admin_id, diagnostic, parse_mode="HTML")
                user_sent = True
            except TelegramForbiddenError:
                user_blocked = True
            except Exception:
                logger.exception("Failed to notify top-up credit payment=%s", payment_id)

            if user_sent or user_blocked:
                async with session_scope() as session:
                    if user_blocked:
                        await mark_user_bot_blocked(session, telegram_id)
                    p = await session.scalar(select(Payment).where(Payment.id == payment_id))
                    if p and p.credit_notified_at is None:
                        p.credit_notified_at = now_utc()
                if user_sent:
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