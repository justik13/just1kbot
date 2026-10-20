"""Support link construction and helpers."""
from __future__ import annotations

import os
from urllib.parse import quote


def _resolve_support_username(username: str | None = None) -> str:
    """Resolve clean Telegram username for support without leading @."""
    if username:
        return username.lstrip("@")
    env_user = os.getenv("SUPPORT_USERNAME") or os.getenv("SUPPORT_BOT_USERNAME")
    if env_user:
        return env_user.lstrip("@")
    try:
        from config.settings import get_settings
        settings_user = getattr(get_settings(), "SUPPORT_USERNAME", None)
        if settings_user:
            return settings_user.lstrip("@")
    except Exception:
        pass
    return "support"


def get_support_url(text: str | None = None, username: str | None = None) -> str:
    """Build a direct Telegram URL to support contact with an optional pre-filled message.

    Format: https://t.me/{support_username}?text={quote(text)}
    """
    clean_username = _resolve_support_username(username)
    if text:
        return f"https://t.me/{clean_username}?text={quote(text)}"
    return f"https://t.me/{clean_username}"
