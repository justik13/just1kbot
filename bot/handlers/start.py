"""Minimal /start entrypoint opening the Telegram Mini App via single Connect button."""

from __future__ import annotations

import logging
import re

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import MenuButtonWebApp, Message, WebAppInfo

from bot import texts
from bot.keyboards.start import build_start_inline_keyboard, build_start_rich_message
from config.settings import get_settings
from database.connection import session_scope
from services.subscription import SubscriptionService
from utils.telegram import safe, safe_send_message

router = Router()
logger = logging.getLogger(__name__)


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

    tg_user = message.from_user
    command_args = ""
    if message.text:
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            command_args = parts[1]
    ref_id = parse_referral_id(command_args)

    try:
        async with session_scope() as session:
            db_user = await SubscriptionService.process_onboarding(
                session,
                tg_user.id,
                tg_user.username,
                tg_user.first_name,
                ref_id=ref_id,
            )
    except Exception as exc:
        logger.error("Failed onboarding in /start for user %s: %s", tg_user.id, exc)
        await safe_send_message(message.bot, message.chat.id, texts.START_TECHNICAL_ERROR)
        return

    if db_user is None:
        await safe_send_message(message.bot, message.chat.id, texts.START_TECHNICAL_ERROR)
        return

    settings = get_settings()
    app_url = f"https://{settings.DOMAIN}/app"

    try:
        await message.bot.set_chat_menu_button(
            chat_id=message.chat.id,
            menu_button=MenuButtonWebApp(
                text=texts.BTN_MINIAPP_CONNECT,
                web_app=WebAppInfo(url=app_url),
            ),
        )
    except Exception as exc:
        logger.debug("Failed to update chat menu button for %s: %s", tg_user.id, exc)

    safe_name = safe(db_user.first_name or tg_user.first_name or texts.USER_HUB_START)
    rich_msg = build_start_rich_message(safe_name, app_url)
    fallback_kb = build_start_inline_keyboard(app_url)
    fallback_text = texts.START_HUB_MESSAGE.format(name=safe_name)

    try:
        await message.bot.send_rich_message(
            chat_id=message.chat.id,
            rich_message=rich_msg,
        )
    except TelegramBadRequest as exc:
        logger.warning("send_rich_message fallback to standard message for chat %s: %s", message.chat.id, exc)
        await safe_send_message(
            message.bot,
            message.chat.id,
            fallback_text,
            reply_markup=fallback_kb,
        )
