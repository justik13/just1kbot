"""Minimal /start entrypoint opening the Telegram Mini App via single Connect button."""

from __future__ import annotations

import asyncio
import logging
import re

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message
from cachetools import TTLCache

from bot import texts
from bot.keyboards.notifications import get_referral_onboarding_keyboard
from bot.keyboards.start import build_start_inline_keyboard
from config.settings import get_settings
from database.connection import session_scope
from services.subscription import SubscriptionService
from utils.telegram import EFFECT_LIKE, safe, safe_send_message

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
                from database.repositories.users_repo import is_eligible_for_referral_first_discount
                has_discount = await is_eligible_for_referral_first_discount(session, db_user.id)
                from database.repositories.system_settings_repo import get_system_setting
                raw_proxy = await get_system_setting(session, "mtproto_proxy_url", "")
                if raw_proxy and str(raw_proxy).strip():
                    mtproto_url = str(raw_proxy).strip()
    except Exception as exc:
        logger.error("Failed onboarding in /start for user %s: %s", tg_user.id, exc)
        await safe_send_message(message.bot, message.chat.id, texts.START_TECHNICAL_ERROR)
        return

    if db_user is None:
        await safe_send_message(message.bot, message.chat.id, texts.START_TECHNICAL_ERROR)
        return

    if getattr(db_user, "is_newly_referred", False) and db_user.referred_by:
        bot_inst = message.bot
        ref_tg_id = db_user.referred_by

        async def _send_onboarding_push() -> None:
            try:
                await safe_send_message(
                    bot_inst,
                    ref_tg_id,
                    texts.REFERRAL_ONBOARDING_NOTIFICATION,
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

    safe_name = safe(db_user.first_name or tg_user.first_name or texts.USER_HUB_START)
    discount_line = texts.START_REFERRAL_DISCOUNT_LINE if has_discount else ""
    kb = build_start_inline_keyboard(app_url, mtproto_url=mtproto_url)
    msg_text = texts.START_HUB_MESSAGE.format(name=safe_name, discount_line=discount_line)

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
