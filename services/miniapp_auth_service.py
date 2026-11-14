"""Authentication, TOTP/PIN 2FA, and signed admin session helpers for Telegram Mini App."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import logging
import os
import secrets
import struct
import time
from typing import Any
from urllib.parse import parse_qsl, quote

from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.repositories.system_settings_repo import get_system_setting, set_system_setting
from utils.admin import is_admin

logger = logging.getLogger(__name__)

DEFAULT_INIT_DATA_MAX_AGE_SECONDS = 86400
MAX_FUTURE_SKEW_SECONDS = 300
ADMIN_SESSION_TTL_SECONDS = 12 * 3600
SETUP_TOKEN_TTL_SECONDS = 600


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


def _derive_signing_key(purpose: bytes) -> bytes:
    settings = get_settings()
    return hmac.new(purpose, settings.BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()


def issue_admin_session_token(
    telegram_id: int,
    *,
    ttl_seconds: int = ADMIN_SESSION_TTL_SECONDS,
) -> tuple[str, int]:
    """Issue an HMAC-SHA256 signed short-lived admin session token."""
    exp_ts = int(time.time()) + max(60, ttl_seconds)
    nonce = secrets.token_hex(8)
    payload = f"{telegram_id}:{exp_ts}:{nonce}".encode("utf-8")
    sig = hmac.new(_derive_signing_key(b"MiniAppAdminSession"), payload, hashlib.sha256).hexdigest()
    return f"{exp_ts}.{nonce}.{sig}", exp_ts


def verify_admin_session_token(token: str | None, telegram_id: int) -> bool:
    """Verify HMAC-SHA256 signature and expiration of an admin session token."""
    if not token or not is_admin(telegram_id):
        return False
    parts = token.strip().split(".")
    if len(parts) != 3:
        return False
    exp_raw, nonce, sig = parts
    if not exp_raw.isdigit() or not nonce or not sig:
        return False
    exp_ts = int(exp_raw)
    if exp_ts < int(time.time()):
        return False
    payload = f"{telegram_id}:{exp_ts}:{nonce}".encode("utf-8")
    expected_sig = hmac.new(
        _derive_signing_key(b"MiniAppAdminSession"),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(sig, expected_sig)


def generate_totp_secret() -> str:
    """Generate a 160-bit Base32 TOTP secret without padding."""
    raw = secrets.token_bytes(20)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def build_totp_uri(secret: str, account_name: str, issuer: str = "Just1k Admin") -> str:
    """Build standard otpauth://totp URI for authenticator apps."""
    label = quote(f"{issuer}:{account_name}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&digits=6&period=30"


def _compute_hotp(secret: str, counter: int) -> str:
    clean = secret.strip().replace(" ", "").upper()
    pad_len = (-len(clean)) % 8
    key = base64.b32decode(clean + ("=" * pad_len), casefold=True)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code_int:06d}"


def verify_totp_code(
    secret: str,
    code: str,
    *,
    window: int = 1,
    now: int | None = None,
) -> bool:
    """Verify 6-digit RFC 6238 TOTP code within +-window 30s steps."""
    clean_code = (code or "").strip().replace(" ", "")
    if not clean_code.isdigit() or len(clean_code) != 6:
        return False
    ts = int(time.time()) if now is None else int(now)
    step = ts // 30
    try:
        for delta in range(-window, window + 1):
            if hmac.compare_digest(_compute_hotp(secret, step + delta), clean_code):
                return True
    except Exception:
        return False
    return False


def issue_totp_setup_token(telegram_id: int, secret: str) -> str:
    """Sign a temporary TOTP setup secret so the client can confirm it with a valid code."""
    exp_ts = int(time.time()) + SETUP_TOKEN_TTL_SECONDS
    payload_str = f"{telegram_id}:{exp_ts}:{secret}"
    sig = hmac.new(
        _derive_signing_key(b"MiniAppTotpSetup"),
        payload_str.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    raw = f"{exp_ts}.{secret}.{sig}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def verify_totp_setup_token(token: str | None, telegram_id: int) -> str | None:
    """Validate temporary TOTP setup token and return embedded Base32 secret."""
    if not token:
        return None
    try:
        padded = token.strip() + ("=" * ((-len(token.strip())) % 4))
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        exp_raw, secret, sig = decoded.split(".", 2)
        if not exp_raw.isdigit() or not secret or not sig:
            return None
        if int(exp_raw) < int(time.time()):
            return None
        expected = hmac.new(
            _derive_signing_key(b"MiniAppTotpSetup"),
            f"{telegram_id}:{exp_raw}:{secret}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        return secret
    except Exception:
        return None


def _hash_pin(pin: str, telegram_id: int) -> str:
    return hmac.new(
        _derive_signing_key(b"MiniAppAdminPin"),
        f"{telegram_id}:{pin}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


async def get_admin_2fa_config(
    session: AsyncSession,
    telegram_id: int,
) -> dict[str, Any]:
    """Resolve active 2FA mode for an admin (env overrides DB settings)."""
    env_totp = os.getenv("MINIAPP_ADMIN_TOTP_SECRET", "").strip()
    if env_totp:
        return {"configured": True, "mode": "totp", "secret": env_totp, "source": "env"}

    env_pin = os.getenv("MINIAPP_ADMIN_PIN", "").strip()
    if env_pin:
        return {"configured": True, "mode": "pin", "pin_plain": env_pin, "source": "env"}

    db_totp = await get_system_setting(session, f"miniapp_admin_totp_{telegram_id}")
    if db_totp:
        return {"configured": True, "mode": "totp", "secret": db_totp.strip(), "source": "db"}

    db_pin_hash = await get_system_setting(session, f"miniapp_admin_pin_{telegram_id}")
    if db_pin_hash:
        return {"configured": True, "mode": "pin", "pin_hash": db_pin_hash.strip(), "source": "db"}

    return {"configured": False, "mode": "setup_required", "source": "none"}


async def verify_admin_2fa(
    session: AsyncSession,
    telegram_id: int,
    code: str,
    *,
    setup_token: str | None = None,
) -> dict[str, Any]:
    """Verify admin 2FA code (TOTP or PIN) or finalize initial TOTP setup."""
    if not is_admin(telegram_id):
        return {"verified": False, "error": "Access denied"}

    clean_code = (code or "").strip()
    if setup_token:
        pending_secret = verify_totp_setup_token(setup_token, telegram_id)
        if not pending_secret:
            return {"verified": False, "error": "Срок настройки истёк, сгенерируйте ключ заново"}
        if not verify_totp_code(pending_secret, clean_code):
            return {"verified": False, "error": "Неверный 6-значный код подтверждения"}
        await set_system_setting(
            session,
            f"miniapp_admin_totp_{telegram_id}",
            pending_secret,
            updated_by=telegram_id,
        )
        token, exp_ts = issue_admin_session_token(telegram_id)
        return {
            "verified": True,
            "mode": "totp",
            "session_token": token,
            "expires_at": exp_ts,
        }

    cfg = await get_admin_2fa_config(session, telegram_id)
    mode = cfg["mode"]
    if mode == "setup_required":
        return {"verified": False, "error": "2FA не настроена. Настройте TOTP или PIN."}

    ok = False
    if mode == "totp":
        ok = verify_totp_code(cfg["secret"], clean_code)
    elif mode == "pin":
        if "pin_plain" in cfg:
            ok = hmac.compare_digest(clean_code, cfg["pin_plain"])
        elif "pin_hash" in cfg:
            ok = hmac.compare_digest(_hash_pin(clean_code, telegram_id), cfg["pin_hash"])

    if not ok:
        return {"verified": False, "error": "Неверный код подтверждения"}

    token, exp_ts = issue_admin_session_token(telegram_id)
    return {
        "verified": True,
        "mode": mode,
        "session_token": token,
        "expires_at": exp_ts,
    }


async def setup_admin_pin(
    session: AsyncSession,
    telegram_id: int,
    pin: str,
) -> dict[str, Any]:
    """Set initial admin PIN when 2FA is not yet configured and issue session token."""
    if not is_admin(telegram_id):
        return {"verified": False, "error": "Access denied"}

    cfg = await get_admin_2fa_config(session, telegram_id)
    if cfg["configured"]:
        return {"verified": False, "error": "2FA уже настроена"}

    clean_pin = (pin or "").strip()
    if len(clean_pin) < 4 or len(clean_pin) > 32:
        return {"verified": False, "error": "PIN должен быть от 4 до 32 символов"}

    await set_system_setting(
        session,
        f"miniapp_admin_pin_{telegram_id}",
        _hash_pin(clean_pin, telegram_id),
        updated_by=telegram_id,
    )
    token, exp_ts = issue_admin_session_token(telegram_id)
    return {
        "verified": True,
        "mode": "pin",
        "session_token": token,
        "expires_at": exp_ts,
    }
