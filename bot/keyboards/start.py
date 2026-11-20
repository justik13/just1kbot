"""Keyboards for Mini App entry point."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)

from bot import texts


def build_start_inline_keyboard(miniapp_url: str, mtproto_url: str | None = None) -> InlineKeyboardMarkup:
    """Inline keyboard with Mini App Connect button and optional MTProto proxy button."""
    rows = [
        [
            InlineKeyboardButton(
                text=texts.BTN_MINIAPP_CONNECT,
                web_app=WebAppInfo(url=miniapp_url),
            )
        ]
    ]
    if mtproto_url and str(mtproto_url).strip():
        rows.append(
            [
                InlineKeyboardButton(
                    text="🚀 Прокси для Telegram",
                    url=str(mtproto_url).strip(),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
