"""Canonical keyboards for runtime notifications and background alerts."""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts


def get_pre_expiry_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_RENEW_ACCESS, callback_data="menu_subscription")
    builder.button(text=texts.BTN_DISMISS_NOTIFICATION, callback_data="dismiss_notification")
    builder.adjust(1)
    return builder.as_markup()


def get_post_expiry_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_BUY_ACCESS, callback_data="menu_buy")
    builder.button(text=texts.BTN_SUPPORT, callback_data="menu_support")
    builder.button(text=texts.BTN_DISMISS_NOTIFICATION, callback_data="dismiss_notification")
    builder.adjust(1)
    return builder.as_markup()


def get_devices_deleted_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_BUY_SUBSCRIPTION, callback_data="menu_buy")
    builder.button(text=texts.BTN_DISMISS, callback_data="dismiss_notification")
    builder.adjust(1)
    return builder.as_markup()


def get_referral_bonus_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_MY_BALANCE, callback_data="menu_balance")
    builder.button(text=texts.BTN_DISMISS_NOTIFICATION, callback_data="dismiss_notification")
    builder.adjust(1)
    return builder.as_markup()


def get_referral_onboarding_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_INVITE_FRIEND, callback_data="menu_referral")
    builder.button(text=texts.BTN_DISMISS_NOTIFICATION, callback_data="dismiss_notification")
    builder.adjust(1)
    return builder.as_markup()



def get_node_monitor_alert_keyboard(
    server_id: int,
    include_enable_button: bool = False,
) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    if include_enable_button:
        builder.button(
            text=texts.BTN_ENABLE_SERVER,
            callback_data=f"admin_server_toggle_apply:{server_id}",
        )
    builder.button(
        text=texts.BTN_TO_SERVER,
        callback_data=f"admin_server_card:{server_id}",
    )
    builder.button(
        text=texts.BTN_SERVERS_LIST,
        callback_data="admin_servers",
    )
    builder.button(
        text=texts.BTN_DISMISS_ALERT,
        callback_data=f"admin_dismiss_alert:{server_id}",
    )
    builder.adjust(1)
    return builder


def get_traffic_alert_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_OPEN_USER_CARD,
        callback_data=f"admin_user_card:{telegram_id}",
    )
    builder.button(
        text=texts.BTN_DISMISS_NOTIFICATION,
        callback_data="dismiss_notification",
    )
    builder.adjust(1)
    return builder.as_markup()
