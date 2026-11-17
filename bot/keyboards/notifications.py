"""Canonical keyboards for runtime notifications and background alerts."""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts
from config.settings import get_settings


def get_action_notification_keyboard(
    text: str,
    tab: str = "connect",
    miniapp_url: str | None = None,
) -> InlineKeyboardMarkup:
    """Single WebApp button opening Mini App at specified tab."""
    if not miniapp_url:
        settings = get_settings()
        domain = getattr(settings, "DOMAIN", "localhost")
        miniapp_url = f"https://{domain}/app"

    target_url = miniapp_url
    if tab and "?" not in target_url:
        target_url = f"{miniapp_url}?tab={tab}"
    elif tab:
        target_url = f"{miniapp_url}&tab={tab}"

    builder = InlineKeyboardBuilder()
    builder.button(
        text=text,
        web_app=WebAppInfo(url=target_url),
    )
    builder.adjust(1)
    return builder.as_markup()


def get_dismiss_notification_keyboard() -> InlineKeyboardMarkup:
    """Contextual action button fallback for user-facing push notifications."""
    return get_action_notification_keyboard(texts.BTN_OPEN_CABINET, tab="connect")


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
    return get_action_notification_keyboard(texts.BTN_RENEW_ACCESS, tab="tariffs")


def get_post_expiry_keyboard() -> InlineKeyboardMarkup:
    return get_action_notification_keyboard(texts.BTN_RESUME_ACCESS, tab="tariffs")


def get_autorenew_failed_keyboard() -> InlineKeyboardMarkup:
    return get_action_notification_keyboard(texts.BTN_TOPUP_BALANCE, tab="profile")


def get_referral_bonus_keyboard() -> InlineKeyboardMarkup:
    return get_action_notification_keyboard(texts.BTN_OPEN_PROFILE, tab="profile")


def get_referral_onboarding_keyboard() -> InlineKeyboardMarkup:
    return get_action_notification_keyboard(texts.BTN_OPEN_CABINET, tab="connect")


def get_broadcast_action_keyboard(
    text: str = texts.BTN_OPEN_APP,
    tab: str = "connect",
) -> InlineKeyboardMarkup:
    return get_action_notification_keyboard(text, tab=tab)


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
