"""Minimal /start entrypoint opening the Telegram Mini App via single Connect button."""

from __future__ import annotations

import asyncio
import logging
import re

from urllib.parse import quote

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message
from cachetools import TTLCache

from bot import texts
from bot.keyboards.notifications import get_referral_onboarding_keyboard
from bot.keyboards.start import build_start_inline_keyboard
from config.constants import DEFAULT_FALLBACK_BOT_USERNAME, REFERRAL_WELCOME_DISCOUNT_PERCENT
from config.enums import WhiteInternetStatus
from config.settings import get_settings
from database.connection import session_scope
from database.repositories.account_ledger_repo import get_account_balance
from database.repositories.system_settings_repo import get_system_setting
from database.repositories.users_repo import (
    get_user_active_referrals_count,
    is_eligible_for_referral_first_discount,
)
from database.repositories.vless_subscription_repo import (
    get_subscription_by_user_id as get_vless_subscription_by_user_id,
)
from database.repositories.white_internet_repo import get_subscription_by_user_id
from services.referral_bonus import get_referral_tier
from services.subscription import SubscriptionService
from utils.telegram import EFFECT_LIKE, resolve_bot_username, safe, safe_send_message
from utils.datetime_helpers import now_utc

router = Router()
logger = logging.getLogger(__name__)
_background_tasks: set[asyncio.Task] = set()


async def cancel_start_background_tasks() -> None:
    """Cancel and drain in-flight start onboarding tasks."""
    tasks = list(_background_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
_LAST_START_MESSAGES: TTLCache[int, int] = TTLCache(maxsize=10000, ttl=172800)


async def _safe_delete_message(bot, chat_id: int, message_id: int) -> None:
    if bot is None:
        return
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass


def parse_referral_id(command_args: str) -> int | None:
    if not command_args:
        return None
    match = re.match(r"^(?:ref_)?(\d{1,19})$", command_args.strip())
    if not match:
        return None
    try:
        val = int(match.group(1))
        if 0 < val <= 9_223_372_036_854_775_807:
            return val
    except (ValueError, OverflowError):
        pass
    return None


async def _build_start_profile_context(session, db_user) -> dict:
    """Build concise user profile summary and real referral tier values for /start."""
    now = now_utc()
    bal = await get_account_balance(session, user_id=db_user.id)
    vless_sub = await get_vless_subscription_by_user_id(session, db_user.id)
    wi_sub = await get_subscription_by_user_id(session, db_user.id)

    std_active = SubscriptionService.check_vpn_access(db_user, vless_sub=vless_sub)
    if std_active and vless_sub and vless_sub.expires_at:
        std_line = f"Стандарт: до <b>{vless_sub.expires_at.strftime('%d.%m.%Y')}</b> ({vless_sub.device_limit or 2} устр.)"
    else:
        std_line = "Стандарт: <b>не подключён</b>"

    wi_has_period = bool(
        wi_sub
        and not db_user.financial_hold
        and wi_sub.status in (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.PENDING, WhiteInternetStatus.EXHAUSTED)
        and wi_sub.expires_at
        and wi_sub.expires_at > now
    )
    if wi_has_period and wi_sub:
        total_bytes = int(wi_sub.base_traffic_bytes or 0) + int(wi_sub.extra_traffic_bytes or 0)
        used_bytes = int(wi_sub.traffic_used_bytes or 0)
        rem_gb = max(0.0, (total_bytes - used_bytes) / (1024 ** 3))
        wi_line = f"Белый интернет: до <b>{wi_sub.expires_at.strftime('%d.%m.%Y')}</b> (ост. <b>{rem_gb:.1f} ГБ</b>)"
    else:
        wi_line = "Белый интернет: <b>не подключён</b>"

    total_rub = int(bal.available)
    real_rub = int(bal.real_available)
    bonus_rub = int(bal.bonus_available)
    profile_block = (
        "<b>Ваш профиль:</b>\n"
        f"• Баланс: <b>{total_rub} ₽</b> (осн. {real_rub} ₽ · бонусы {bonus_rub} ₽)\n"
        f"• {std_line}\n"
        f"• {wi_line}"
    )

    active_count = await get_user_active_referrals_count(session, db_user.telegram_id)
    tier = get_referral_tier(active_count)
    ref_rate_pct = int(tier.rate * 100)
    ref_next_line = ""
    if tier.needed_for_next and tier.next_rate is not None:
        next_pct = int(tier.next_rate * 100)
        ref_next_line = f" До ставки <b>{next_pct}%</b> осталось <b>{tier.needed_for_next}</b> акт. реф."

    return {
        "profile_block": profile_block,
        "ref_rate_pct": ref_rate_pct,
        "ref_tier_name": safe(tier.name),
        "ref_next_line": ref_next_line,
        "welcome_discount_pct": int(REFERRAL_WELCOME_DISCOUNT_PERCENT * 100),
    }


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Handle /start: onboard user, bind WebApp menu button, and show single Connect card."""
    if not message.from_user or not message.chat:
        return

    await _safe_delete_message(message.bot, message.chat.id, message.message_id)
    prev_msg_id = _LAST_START_MESSAGES.pop(message.chat.id, None)
    if prev_msg_id:
        await _safe_delete_message(message.bot, message.chat.id, prev_msg_id)

    tg_user = message.from_user
    command_args = ""
    if message.text:
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            command_args = parts[1]
    ref_id = parse_referral_id(command_args)

    has_discount = False
    mtproto_url = None
    profile_ctx: dict | None = None
    referrer_rate_pct: int | None = None
    try:
        async with session_scope() as session:
            db_user = await SubscriptionService.process_onboarding(
                session,
                tg_user.id,
                tg_user.username,
                tg_user.first_name,
                ref_id=ref_id,
            )
            if db_user is not None:
                has_discount = await is_eligible_for_referral_first_discount(session, db_user.id)
                raw_proxy = await get_system_setting(session, "mtproto_proxy_url", "")
                if raw_proxy and str(raw_proxy).strip():
                    mtproto_url = str(raw_proxy).strip()
                profile_ctx = await _build_start_profile_context(session, db_user)
                if getattr(db_user, "is_newly_referred", False) and db_user.referred_by:
                    ref_active = await get_user_active_referrals_count(session, db_user.referred_by)
                    referrer_rate_pct = int(get_referral_tier(ref_active).rate * 100)
    except Exception as exc:
        logger.error("Failed onboarding in /start for user %s: %s", tg_user.id, exc)
        await safe_send_message(message.bot, message.chat.id, texts.START_TECHNICAL_ERROR)
        return

    if db_user is None or profile_ctx is None:
        await safe_send_message(message.bot, message.chat.id, texts.START_TECHNICAL_ERROR)
        return

    if getattr(db_user, "is_newly_referred", False) and db_user.referred_by:
        bot_inst = message.bot
        ref_tg_id = db_user.referred_by
        referred_label = (
            f"@{safe(db_user.username)}"
            if db_user.username
            else safe(db_user.first_name or f"ID {db_user.telegram_id}")
        )
        notify_text = texts.REFERRAL_ONBOARDING_NOTIFICATION.format(
            referred_user=referred_label,
            rate_pct=referrer_rate_pct if referrer_rate_pct is not None else 15,
        )

        async def _send_onboarding_push() -> None:
            try:
                await safe_send_message(
                    bot_inst,
                    ref_tg_id,
                    notify_text,
                    reply_markup=get_referral_onboarding_keyboard(),
                    message_effect_id=EFFECT_LIKE,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to send referral onboarding push to %s: %s",
                    ref_tg_id,
                    exc,
                )

        task = asyncio.create_task(_send_onboarding_push())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    settings = get_settings()
    app_url = f"https://{settings.DOMAIN}/app"
    bot_username = (await resolve_bot_username(message.bot) or DEFAULT_FALLBACK_BOT_USERNAME).lstrip("@")
    ref_link = f"https://t.me/{bot_username}?start=ref_{db_user.telegram_id}"
    share_text = f"Держи приглашение: скидка {profile_ctx['welcome_discount_pct']}% на первый заказ"
    referral_share_url = (
        f"https://t.me/share/url?url={quote(ref_link, safe='')}"
        f"&text={quote(share_text, safe='')}"
    )

    safe_name = safe(db_user.first_name or tg_user.first_name or texts.USER_HUB_START)
    discount_line = (
        texts.START_REFERRAL_DISCOUNT_LINE.format(
            discount_pct=profile_ctx["welcome_discount_pct"]
        )
        if has_discount
        else ""
    )
    kb = build_start_inline_keyboard(
        app_url,
        mtproto_url=mtproto_url,
        referral_share_url=referral_share_url,
    )
    msg_text = texts.START_HUB_MESSAGE.format(
        name=safe_name,
        discount_line=discount_line,
        **profile_ctx,
    )

    sent = await safe_send_message(
        message.bot,
        message.chat.id,
        msg_text,
        reply_markup=kb,
    )
    if isinstance(sent, int):
        _LAST_START_MESSAGES[message.chat.id] = sent
    elif sent and getattr(sent, "message_id", None):
        _LAST_START_MESSAGES[message.chat.id] = sent.message_id


@router.message()
async def cmd_fallback(message: Message) -> None:
    """Fallback handler for unhandled messages; points user to the Mini App control panel."""
    if not message.from_user or not message.chat:
        return

    settings = get_settings()
    app_url = f"https://{settings.DOMAIN}/app"
    kb = build_start_inline_keyboard(app_url)
    await safe_send_message(
        message.bot,
        message.chat.id,
        texts.START_FALLBACK_MESSAGE,
        reply_markup=kb,
    )


@router.callback_query()
async def cb_legacy_fallback(callback: CallbackQuery) -> None:
    """Handle legacy inline buttons from older bot versions gracefully."""
    data = (callback.data or "").strip()
    if data == "ignore":
        try:
            await callback.answer()
        except Exception as exc:
            logger.debug("Ignored legacy callback.answer error: %s", exc)
        return
    settings = get_settings()
    app_url = f"https://{settings.DOMAIN}/app"
    kb = build_start_inline_keyboard(app_url)
    try:
        await callback.answer("Меню обновлено. Откройте кабинет.", show_alert=False)
    except Exception as exc:
        logger.debug("Legacy fallback callback.answer error: %s", exc)
    if callback.message:
        await safe_send_message(
            callback.bot,
            callback.message.chat.id,
            texts.START_FALLBACK_MESSAGE,
            reply_markup=kb,
        )
