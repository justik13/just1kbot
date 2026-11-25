"""Canonical keyboards for runtime notifications and background alerts."""
from __future__ import annotations

from urllib.parse import urlencode

from aiogram.types import InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts
from config.settings import get_settings


def _build_miniapp_url(
    query_params: dict[str, str | int] | None = None,
    miniapp_url: str | None = None,
) -> str:
    """Build Mini App URL with optional query parameters."""
    if not miniapp_url:
        settings = get_settings()
        domain = getattr(settings, "DOMAIN", "localhost")
        miniapp_url = f"https://{domain}/app"
    if not query_params:
        return miniapp_url
    sep = "&" if "?" in miniapp_url else "?"
    return f"{miniapp_url}{sep}{urlencode(query_params)}"


def get_action_notification_keyboard(
    text: str,
    tab: str = "connect",
    miniapp_url: str | None = None,
) -> InlineKeyboardMarkup:
    """Single WebApp button opening Mini App at specified tab."""
    target_url = _build_miniapp_url({"tab": tab} if tab else None, miniapp_url=miniapp_url)

    builder = InlineKeyboardBuilder()
    builder.button(
        text=text,
        web_app=WebAppInfo(url=target_url),
    )
    builder.adjust(1)
    return builder.as_markup()


def get_pre_expiry_keyboard() -> InlineKeyboardMarkup:
    return get_action_notification_keyboard(texts.BTN_RENEW_ACCESS, tab="tariffs")


def get_post_expiry_keyboard() -> InlineKeyboardMarkup:
    return get_action_notification_keyboard(texts.BTN_RESUME_ACCESS, tab="tariffs")


def get_autorenew_failed_keyboard() -> InlineKeyboardMarkup:
    return get_action_notification_keyboard(texts.BTN_TOPUP_BALANCE, tab="profile")


def get_bonus_credited_keyboard() -> InlineKeyboardMarkup:
    return get_action_notification_keyboard(texts.BTN_OPEN_PROFILE, tab="profile")


def get_referral_bonus_keyboard() -> InlineKeyboardMarkup:
    return get_action_notification_keyboard(texts.BTN_OPEN_PROFILE, tab="profile")


def get_referral_onboarding_keyboard() -> InlineKeyboardMarkup:
    return get_action_notification_keyboard(texts.BTN_OPEN_CABINET, tab="connect")


def get_broadcast_action_keyboard(
    text: str = texts.BTN_OPEN_APP,
    tab: str = "connect",
) -> InlineKeyboardMarkup:
    return get_action_notification_keyboard(text, tab=tab)


def _get_admin_deeplink_keyboard(
    text: str,
    query_params: dict[str, str | int],
) -> InlineKeyboardMarkup:
    """Single WebApp button opening Mini App Admin mode at a specific entity/tab."""
    target_url = _build_miniapp_url(query_params)
    builder = InlineKeyboardBuilder()
    builder.button(
        text=text,
        web_app=WebAppInfo(url=target_url),
    )
    builder.adjust(1)
    return builder.as_markup()


def get_admin_node_keyboard(server_id: int) -> InlineKeyboardMarkup:
    """Admin deep-link keyboard to open a specific node in Mini App Admin."""
    return _get_admin_deeplink_keyboard(
        "Открыть ноду",
        {"mode": "admin", "tab": "nodes", "node_id": int(server_id)},
    )


def get_admin_user_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Admin deep-link keyboard to open a specific user in Mini App Admin."""
    return _get_admin_deeplink_keyboard(
        "Открыть пользователя",
        {"mode": "admin", "tab": "users", "user_id": int(user_id)},
    )


def get_admin_finances_keyboard(query: str = "") -> InlineKeyboardMarkup:
    """Admin deep-link keyboard to open Finances tab in Mini App Admin."""
    params: dict[str, str | int] = {"mode": "admin", "tab": "finances"}
    clean_query = str(query).strip() if query else ""
    if clean_query:
        params["q"] = clean_query
    return _get_admin_deeplink_keyboard("Открыть в Финансах", params)
