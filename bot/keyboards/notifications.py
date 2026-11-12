"""Canonical keyboards for runtime notifications and background alerts."""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts


def get_dismiss_notification_keyboard() -> InlineKeyboardMarkup:
    """Single dismiss button for user-facing push notifications."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_DISMISS_NOTIFICATION,
        callback_data="dismiss_notification",
    )
    builder.adjust(1)
    return builder.as_markup()


def get_dismiss_alert_keyboard(target: str = "general") -> InlineKeyboardMarkup:
    """Single close button for admin alert notifications."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_DISMISS_ALERT,
        callback_data=f"admin_dismiss_alert:{target}",
    )
    builder.adjust(1)
    return builder.as_markup()


def get_pre_expiry_keyboard() -> InlineKeyboardMarkup:
    return get_dismiss_notification_keyboard()


def get_post_expiry_keyboard() -> InlineKeyboardMarkup:
    return get_dismiss_notification_keyboard()


def get_devices_deleted_keyboard() -> InlineKeyboardMarkup:
    return get_dismiss_notification_keyboard()


def get_referral_bonus_keyboard() -> InlineKeyboardMarkup:
    return get_dismiss_notification_keyboard()


def get_node_monitor_alert_keyboard(
    server_id: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_DISMISS_ALERT,
        callback_data=f"admin_dismiss_alert:{server_id}",
    )
    builder.adjust(1)
    return builder.as_markup()


def get_traffic_alert_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    return get_dismiss_alert_keyboard("general")
