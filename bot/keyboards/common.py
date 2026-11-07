from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts


def get_hub_keyboard(
    is_admin: bool = False,
    is_active: bool = False,
    mtproto_url: str | None = None,
    miniapp_url: str | None = None,
    real_balance: int = 0,
    bonus_balance: int = 0,
    channel_url: str | None = None,
    support_username: str | None = None,
    telegram_id: int | None = None,
) -> InlineKeyboardMarkup:
    """Build single rich hub keyboard matching Just1k v2 specification."""
    builder = InlineKeyboardBuilder()

    # 1. Row: Main Action - WebApp Mini App
    target_miniapp_url = miniapp_url or "https://just1k.pro/app"
    builder.button(
        text=texts.BTN_HUB_CONNECT,
        web_app=WebAppInfo(url=target_miniapp_url),
        style="success",
    )

    # 2. Row: Рефералы | Баланс
    bal_label = texts.BTN_HUB_BALANCE.format(real=real_balance, bonus=bonus_balance)
    builder.button(
        text=texts.BTN_HUB_REFERRALS,
        callback_data="menu_referral",
    )
    builder.button(
        text=bal_label,
        callback_data="menu_balance",
    )

    # 3. Row: Прокси TG | TG КАНАЛ
    has_proxy = bool(mtproto_url)
    has_channel = bool(channel_url)
    if has_proxy:
        builder.button(
            text=texts.BTN_HUB_PROXY,
            url=mtproto_url,
        )
    if has_channel:
        builder.button(
            text=texts.BTN_HUB_CHANNEL,
            url=channel_url,
        )

    # 4. Row: Информация | Поддержка
    builder.button(
        text=texts.BTN_HUB_INFO,
        callback_data="menu_support",
    )

    from utils.support import get_support_url

    greeting = (
        texts.SUPPORT_GREETING_TEMPLATE.format(telegram_id=telegram_id)
        if telegram_id
        else None
    )
    support_url = get_support_url(text=greeting, username=support_username)
    builder.button(
        text=texts.BTN_SUPPORT,
        url=support_url,
    )

    # 5. Row: Админка
    if is_admin:
        builder.button(
            text=texts.BTN_ADMIN,
            callback_data="menu_admin",
        )

    sizes = [1, 2]
    row3_count = (1 if has_proxy else 0) + (1 if has_channel else 0)
    if row3_count > 0:
        sizes.append(row3_count)
    sizes.append(2)
    if is_admin:
        sizes.append(1)

    builder.adjust(*sizes)
    return builder.as_markup()


def get_back_button(
    callback_data: str = "back_to_main_menu",
    text: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if text is not None:
        button_text = text
    elif callback_data == "back_to_main_menu":
        button_text = texts.BTN_MAIN_MENU_NAV
    else:
        button_text = texts.BTN_BACK

    builder.button(text=button_text, callback_data=callback_data)

    return builder.as_markup()
