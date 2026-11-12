"""Authentication and authorization helpers for Telegram Mini App."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl

from config.settings import get_settings
from utils.admin import is_admin

logger = logging.getLogger(__name__)

DEFAULT_INIT_DATA_MAX_AGE_SECONDS = 86400
MAX_FUTURE_SKEW_SECONDS = 300


@dataclass(frozen=True)
class MiniAppUserContext:
    """Verified Telegram WebApp user context."""

    telegram_id: int
    first_name: str
    last_name: str
    username: str
    is_admin: bool
    auth_date: int


def validate_telegram_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = DEFAULT_INIT_DATA_MAX_AGE_SECONDS,
) -> dict[str, Any] | None:
    """Validate Telegram WebApp initData HMAC-SHA256 signature and auth_date freshness."""
    if not init_data or not bot_token:
        return None

    try:
        parsed_data = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None

    received_hash = parsed_data.pop("hash", None)
    if not received_hash:
        return None

    auth_date_raw = parsed_data.get("auth_date")
    if not auth_date_raw or not str(auth_date_raw).isdigit():
        logger.warning("MiniApp initData missing valid auth_date")
        return None

    auth_date = int(auth_date_raw)
    now_ts = int(time.time())
    if auth_date > now_ts + MAX_FUTURE_SKEW_SECONDS:
        logger.warning("MiniApp initData auth_date is too far in the future")
        return None
    if max_age_seconds > 0 and (now_ts - auth_date) > max_age_seconds:
        logger.warning("MiniApp initData expired (age=%ss)", now_ts - auth_date)
        return None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(received_hash, calculated_hash):
        logger.warning("Telegram WebApp initData signature mismatch")
        return None

    user_raw = parsed_data.get("user")
    if not user_raw:
        return None

    try:
        user_obj = json.loads(user_raw)
    except Exception:
        return None

    if not isinstance(user_obj, dict) or not isinstance(user_obj.get("id"), int):
        return None

    user_obj["_auth_date"] = auth_date
    return user_obj


def authenticate_miniapp_request(init_data: str | None) -> MiniAppUserContext | None:
    """Validate initData against configured BOT_TOKEN and resolve admin permissions."""
    if not init_data:
        return None

    settings = get_settings()
    user_info = validate_telegram_init_data(init_data, settings.BOT_TOKEN)
    if not user_info:
        return None

    tg_id = int(user_info["id"])
    return MiniAppUserContext(
        telegram_id=tg_id,
        first_name=str(user_info.get("first_name") or ""),
        last_name=str(user_info.get("last_name") or ""),
        username=str(user_info.get("username") or ""),
        is_admin=is_admin(tg_id),
        auth_date=int(user_info.get("_auth_date") or 0),
    )


def verify_admin_2fa_stub(telegram_id: int, code: str | None = None) -> dict[str, Any]:
    """Temporary 2FA verification stub for admin access.

    TODO: Replace this stub with real TOTP verification (pyotp) and encrypted
    per-admin 2FA secret storage + short-lived session token validation.
    """
    if not is_admin(telegram_id):
        return {
            "verified": False,
            "stub": True,
            "mode": "denied",
            "todo": "TOTP 2FA verification pending",
        }

    _ = (code or "").strip()
    return {
        "verified": True,
        "stub": True,
        "mode": "stub_bypassed",
        "todo": "TOTP 2FA verification pending",
    }
