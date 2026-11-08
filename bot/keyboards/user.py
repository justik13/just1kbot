"""Keyboards for referral and user screens in Just1k 2.0."""

from __future__ import annotations

import urllib.parse
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts


def get_referral_keyboard(
    invite_url: str,
    share_text: str | None = None,
    count: int = 0,
    **kwargs,
) -> InlineKeyboardMarkup:
    """Build referral screen keyboard strictly matching just1kbotv2.md:

    - Share button calling native Telegram share
    - My referrals | Top 5
    - Back button
    """
    builder = InlineKeyboardBuilder()

    # Кнопка 'Поделиться' через нативный share в Telegram
    default_text = "Присоединяйся к Just1k!"
    text_to_share = share_text or default_text
    encoded_url = urllib.parse.quote(invite_url)
    encoded_text = urllib.parse.quote(text_to_share)
    share_url = f"https://t.me/share/url?url={encoded_url}&text={encoded_text}"

    builder.button(
        text=texts.BTN_INVITE_FRIEND,
        url=share_url,
    )

    # Кнопки: Мои рефералы | Топ 5
    ref_btn_text = f"👥 Мои рефералы ({count})" if count > 0 else "👥 Мои рефералы"
    builder.button(
        text=ref_btn_text,
        callback_data="user_referrals_list:0",
    )
    builder.button(
        text="🏆 Топ 5",
        callback_data="user_referrals_top",
    )

    # Кнопка: Назад в главное меню
    builder.button(
        text=texts.BTN_MAIN_MENU_NAV,
        callback_data="back_to_main_menu",
    )

    builder.adjust(1, 2, 1)
    return builder.as_markup()


def get_referral_leaderboard_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_BACK,
        callback_data="menu_referral",
    )
    return builder.as_markup()


def get_referrals_list_keyboard(
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    nav_buttons = []
    if page > 0:
        nav_buttons.append(
            ("◀️", f"user_referrals_list:{page - 1}")
        )
    if page < total_pages - 1:
        nav_buttons.append(
            ("▶️", f"user_referrals_list:{page + 1}")
        )

    for btn_text, cb in nav_buttons:
        builder.button(text=btn_text, callback_data=cb)

    builder.button(
        text=texts.BTN_BACK,
        callback_data="menu_referral",
    )

    if nav_buttons:
        builder.adjust(len(nav_buttons), 1)
    else:
        builder.adjust(1)

    return builder.as_markup()


def get_history_keyboard(
    page: int = 0,
    total_pages: int = 1,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_BACK,
        callback_data="menu_balance",
    )
    return builder.as_markup()
