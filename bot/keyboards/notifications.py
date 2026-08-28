"""Canonical keyboards for runtime notifications and background alerts."""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.texts.common.buttons import (
    BTN_BUY_ACCESS,
    BTN_BUY_SUBSCRIPTION,
    BTN_DISMISS,
    BTN_DISMISS_ALERT,
    BTN_DISMISS_NOTIFICATION,
    BTN_ENABLE_SERVER,
    BTN_HIDE,
    BTN_MAIN_MENU,
    BTN_MY_BALANCE,
    BTN_OPEN_USER_CARD,
    BTN_RENEW_ACCESS,
    BTN_SERVERS_LIST,
    BTN_SUPPORT,
    BTN_TO_SERVER,
)


def get_pre_expiry_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=BTN_RENEW_ACCESS, callback_data="menu_subscription")
    builder.button(text=BTN_DISMISS_NOTIFICATION, callback_data="dismiss_notification")
    builder.adjust(1)
    return builder.as_markup()


def get_post_expiry_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=BTN_BUY_ACCESS, callback_data="menu_buy")
    builder.button(text=BTN_SUPPORT, callback_data="menu_support")
    builder.button(text=BTN_DISMISS_NOTIFICATION, callback_data="dismiss_notification")
    builder.adjust(1)
    return builder.as_markup()


def get_devices_deleted_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=BTN_BUY_SUBSCRIPTION, callback_data="menu_buy")
    builder.button(text=BTN_DISMISS, callback_data="dismiss_notification")
    builder.adjust(1)
    return builder.as_markup()


def get_purchase_completed_notification_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=BTN_MAIN_MENU, callback_data="back_to_main_menu")
    builder.button(text=BTN_DISMISS, callback_data="dismiss_notification")
    builder.adjust(2)
    return builder.as_markup()


def get_referral_bonus_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=BTN_MY_BALANCE, callback_data="menu_balance")
    builder.adjust(1)
    return builder.as_markup()


def get_node_monitor_alert_keyboard(
    server_id: int,
    include_enable_button: bool = False,
) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    if include_enable_button:
        builder.button(
            text=BTN_ENABLE_SERVER,
            callback_data=f"admin_server_toggle_apply:{server_id}",
        )
    builder.button(
        text=BTN_TO_SERVER,
        callback_data=f"admin_server_card:{server_id}",
    )
    builder.button(
        text=BTN_SERVERS_LIST,
        callback_data="admin_servers",
    )
    builder.button(
        text=BTN_DISMISS_ALERT,
        callback_data=f"admin_dismiss_alert:{server_id}",
    )
    builder.adjust(1)
    return builder


def get_traffic_alert_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=BTN_OPEN_USER_CARD,
        callback_data=f"admin_user_card:{telegram_id}",
    )
    builder.adjust(1)
    return builder.as_markup()


def get_dismiss_alert_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=BTN_HIDE, callback_data="dismiss_notification")
    builder.adjust(1)
    return builder.as_markup()
