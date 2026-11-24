"""Keyboards for Mini App entry point."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)

from bot import texts


def build_start_inline_keyboard(
    miniapp_url: str,
    mtproto_url: str | None = None,
    referral_share_url: str | None = None,
    connect_btn_text: str = texts.BTN_MINIAPP_CONNECT,
) -> InlineKeyboardMarkup:
    """Inline keyboard with Mini App entry button, optional referral share, and MTProto proxy button."""
    rows = [
        [
            InlineKeyboardButton(
                text=connect_btn_text,
                web_app=WebAppInfo(url=miniapp_url),
            )
        ]
    ]
    if referral_share_url and str(referral_share_url).strip():
        rows.append(
            [
                InlineKeyboardButton(
                    text="Пригласить друга",
                    url=str(referral_share_url).strip(),
                )
            ]
        )
    if mtproto_url and str(mtproto_url).strip():
        rows.append(
            [
                InlineKeyboardButton(
                    text="Прокси для Telegram",
                    url=str(mtproto_url).strip(),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
