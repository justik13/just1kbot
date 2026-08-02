"""Durable, retryable user notifications for credited top-ups."""
from bot import texts

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from bot.constants import WORKER_ERROR_SLEEP_INTERVAL
from bot.keyboards import get_topup_credit_keyboard, get_topup_payment_keyboard
from config.settings import get_settings
from database.connection import session_scope
from database.models import (
    EntitlementEntry,
    Payment,
    ReferralEligibility,
    ReferralReward,
    TariffQuote,
    TariffVersion,
    User,
)
from database.repositories.account_ledger_repo import get_account_balance
from database.repositories.users_repo import mark_user_bot_blocked
from utils.datetime_helpers import now_utc
from utils.rate_limiter import global_send_limiter
from utils.telegram import render_hub
from services.subscription import SubscriptionService


logger = logging.getLogger("AccountBalanceNotifications")
BALANCE_NOTIFICATION_INTERVAL = 10.0
BALANCE_NOTIFICATION_BATCH = 50


async def _referral_entitlement(
    session,
    *,
    user_id: int,
    quote_id: int,
    entry_type: str,
    days: int,
) -> bool:
    entry_id = await session.scalar(
        insert(EntitlementEntry)
        .values(
            beneficiary_user_id=user_id,
            source_type="balance_referral",
            source_id=str(quote_id),
            entry_type=entry_type,
            days_delta=days,
            hours_delta=days * 24,
            metadata={"source_quote_id": quote_id},
        )
        .on_conflict_do_nothing(constraint="uq_entitlement_entries_source")
        .returning(EntitlementEntry.id)
    )
    return entry_id is not None


async def process_balance_purchase_referrals() -> int:
    processed = 0
    async with session_scope() as session:
        quote_ids = list(
            (
                await session.scalars(
                    select(TariffQuote.id)
                    .where(
                        TariffQuote.status == "consumed",
                        TariffQuote.operation_type.in_(("purchase", "renew")),
                        TariffQuote.referral_processed_at.is_(None),
                    )
                    .order_by(TariffQuote.id)
                    .limit(BALANCE_NOTIFICATION_BATCH)
                )
            ).all()
        )
    for quote_id in quote_ids:
        async with session_scope() as session:
            quote = await session.scalar(
                select(TariffQuote)
                .where(TariffQuote.id == quote_id)
                .with_for_update()
            )
            if quote is None or quote.referral_processed_at is not None:
                continue
            user = await session.scalar(
                select(User).where(User.id == quote.user_id).with_for_update()
            )
            version = await session.get(
                TariffVersion, quote.target_tariff_version_id
            )
            if (
                user is None
                or version is None
                or not user.referred_by
                or version.duration_hours < 30 * 24
            ):
                quote.referral_processed_at = now_utc()
                processed += 1
                continue
            referrer = await session.scalar(
                select(User)
                .where(User.telegram_id == user.referred_by)
                .with_for_update()
            )
            eligibility = await session.scalar(
                select(ReferralEligibility)
                .where(ReferralEligibility.referred_user_id == user.id)
                .with_for_update()
            )
            if (
                referrer is None
                or referrer.is_deleted
                or referrer.is_banned
                or (eligibility and eligibility.status == "blocked")
            ):
                quote.referral_processed_at = now_utc()
                processed += 1
                continue
            reward = await session.scalar(
                select(ReferralReward).where(
                    ReferralReward.source_quote_id == quote.id
                )
            )
            if reward is None:
                first = eligibility is None
                if first:
                    eligibility = ReferralEligibility(
                        referred_user_id=user.id,
                        status="claimed",
                        source_payment_id=None,
                        source_quote_id=quote.id,
                        reason="first_balance_purchase_reward",
                    )
                    session.add(eligibility)
                reward = ReferralReward(
                    referred_user_id=user.id,
                    source_payment_id=None,
                    source_quote_id=quote.id,
                    referrer_user_id=referrer.id,
                    is_first=first,
                )
                session.add(reward)
                await session.flush()
            user_days = 5 if reward.is_first else 0
            referrer_days = 3 if reward.is_first else 1
            if user_days and await _referral_entitlement(
                session,
                user_id=user.id,
                quote_id=quote.id,
                entry_type="referral_user_bonus",
                days=user_days,
            ):
                await SubscriptionService.extend_subscription(
                    session, user.telegram_id, user_days
                )
            if await _referral_entitlement(
                session,
                user_id=referrer.id,
                quote_id=quote.id,
                entry_type="referral_referrer_bonus",
                days=referrer_days,
            ):
                await SubscriptionService.extend_subscription(
                    session, referrer.telegram_id, referrer_days
                )
                referrer.referral_days = (
                    referrer.referral_days or 0
                ) + referrer_days
            quote.referral_processed_at = now_utc()
            processed += 1
    return processed


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
                    TariffQuote.operation_type.in_(("purchase", "renew", "change")),
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
            duration = texts.RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L223_1.format(value_0=days) + (
                texts.RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L224_1.format(value_0=remainder) if remainder else ""
            )
            title = (
                texts.RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L227_1
                if operation_type == "change"
                else texts.RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L229_1
            )
            await bot.send_message(
                telegram_id,
                texts.UI_SERVICES_WORKERS_ACCOUNT_BALANCE_L232_1.format(value_0=title, value_1=duration, value_2=device_limit),
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
            chat_id = int(context.get("chat_id") or user.telegram_id)
            try:
                await render_hub(
                    bot,
                    chat_id,
                    texts.UI_SERVICES_WORKERS_ACCOUNT_BALANCE_L295_1.format(value_0=int(payment.amount)),
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
                texts.RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L340_1
                if resume
                else ""
            )
            message = (
                texts.RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L345_1.format(value_0=int(payment.amount), value_1=int(balance.available), value_2=suffix)
            )
            try:
                await global_send_limiter.acquire()
                await render_hub(
                    bot,
                    telegram_id,
                    message,
                    get_topup_credit_keyboard(payment.topup_context or {}),
                )
                if (
                    balance.accounting_position
                    > get_settings().BALANCE_MAX_AVAILABLE_RUB
                ):
                    diagnostic = (
                        texts.RUNTIME_SERVICES_WORKERS_ACCOUNT_BALANCE_L361_1.format(value_0=payment.id, value_1=telegram_id, value_2=int(balance.accounting_position))
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
            await process_balance_purchase_referrals()
            await process_balance_purchase_notifications(bot)
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
