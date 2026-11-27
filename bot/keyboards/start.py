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
    admin_url: str | None = None,
) -> InlineKeyboardMarkup:
    """Inline keyboard with Mini App entry button, optional referral share, MTProto proxy, and admin panel."""
    rows = [
        [
            InlineKeyboardButton(
                text=connect_btn_text,
                web_app=WebAppInfo(url=miniapp_url),
            )
        ]
    ]
    if admin_url and str(admin_url).strip():
        rows.append(
            [
                InlineKeyboardButton(
                    text="Админ-панель",
                    web_app=WebAppInfo(url=str(admin_url).strip()),
                )
            ]
        )
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
                    text="Доступ к Telegram",
                    url=str(mtproto_url).strip(),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
