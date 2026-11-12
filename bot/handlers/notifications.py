"""Callback handlers for notification dismissal and sanitization alerts."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from bot import texts
from config.settings import get_settings

logger = logging.getLogger(__name__)
router = Router()


async def _delete_or_clear_markup(callback: CallbackQuery) -> None:
    """Delete notification message, or strip its inline markup if too old."""
    if not callback.message or not callback.message.chat:
        return
    chat_id = callback.message.chat.id
    msg_id = callback.message.message_id
    try:
        await callback.bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except TelegramBadRequest as exc:
        err = str(exc).lower()
        if "can't be deleted" in err or "cannot be deleted" in err:
            try:
                await callback.bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=msg_id,
                    reply_markup=None,
                )
            except Exception:
                pass
        elif "not found" not in err:
            logger.debug("Notification message delete failed: %s", exc)
    except Exception as exc:
        logger.debug("Notification message delete failed: %s", exc)


@router.callback_query(F.data == "dismiss_notification")
async def dismiss_notification(callback: CallbackQuery) -> None:
    try:
        await callback.answer(show_alert=False)
    except Exception:
        pass
    await _delete_or_clear_markup(callback)


@router.callback_query(F.data.startswith("admin_dismiss_alert"))
async def dismiss_admin_alert(callback: CallbackQuery) -> None:
    if callback.from_user.id not in get_settings().ADMIN_IDS:
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    try:
        await callback.answer(show_alert=False)
    except Exception:
        pass
    await _delete_or_clear_markup(callback)


@router.callback_query(F.data == "alert:copy_too_long")
async def alert_copy_too_long_callback(callback: CallbackQuery) -> None:
    await callback.answer(texts.ALERT_COPY_TEXT_TOO_LONG, show_alert=True)
