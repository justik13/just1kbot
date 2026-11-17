"""Callback handlers for notification and sanitization alerts."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot import texts

logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data == "alert:copy_too_long")
async def alert_copy_too_long_callback(callback: CallbackQuery) -> None:
    await callback.answer(texts.ALERT_COPY_TEXT_TOO_LONG, show_alert=True)
