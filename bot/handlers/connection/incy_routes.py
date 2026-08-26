"""Backward-compatibility shim for INCY connection routes."""

from bot import texts
from bot.keyboards import get_back_button
from config.settings import get_settings
from database.models import User
from integrations.incy.bot_routes import (
    _build_incy_keyboard,
    _build_incy_text,
    rotate_incy_subscription,
    router,
    show_incy_subscription,
)
from integrations.incy.token_service import SubscriptionTokenService
from services.subscription import SubscriptionService
from utils.telegram import render_hub

__all__ = [
    "router",
    "show_incy_subscription",
    "rotate_incy_subscription",
    "_build_incy_keyboard",
    "_build_incy_text",
    "SubscriptionService",
    "SubscriptionTokenService",
    "get_settings",
    "render_hub",
    "texts",
    "get_back_button",
    "User",
]
