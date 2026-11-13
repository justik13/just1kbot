"""Keyboards for Mini App entry point."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)

from bot import texts


def build_start_inline_keyboard(miniapp_url: str) -> InlineKeyboardMarkup:
    """Fallback inline keyboard with Mini App Connect button."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_MINIAPP_CONNECT,
                    web_app=WebAppInfo(url=miniapp_url),
                )
            ]
        ]
    )
