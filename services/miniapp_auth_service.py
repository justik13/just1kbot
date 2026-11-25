"""Authentication, TOTP 2FA, and signed admin session helpers for Telegram Mini App."""

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

from cachetools import TTLCache
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.connection import queue_post_commit_task
from database.repositories.system_settings_repo import get_system_setting, set_system_setting
from utils.admin import is_admin

logger = logging.getLogger(__name__)

DEFAULT_INIT_DATA_MAX_AGE_SECONDS = 86400
MAX_FUTURE_SKEW_SECONDS = 300
ADMIN_SESSION_TTL_SECONDS = 12 * 3600
SETUP_TOKEN_TTL_SECONDS = 600
MAX_2FA_ATTEMPTS = 5
ATTEMPT_WINDOW_SECONDS = 300
LOCKOUT_SECONDS = 300
TOTP_REPLAY_TTL_SECONDS = 90
ADMIN_SESSION_EPOCH_KEY = "miniapp_admin_session_epoch"

_cached_admin_session_epoch: str = "1"


class InitDataExpiredError(Exception):
    """Raised when Telegram WebApp initData has valid signature but expired auth_date."""

# In-memory per-admin TOTP brute-force tracker: admin_id -> (fail_count, window_start, locked_until)
_2fa_failures: dict[int, tuple[int, float, float]] = {}
# Bounded in-memory TOTP replay guard: (admin_id, time_step) -> True (90s TTL covers +-30s window)
_used_totp_steps: TTLCache[tuple[int, int], bool] = TTLCache(
    maxsize=1024,
    ttl=TOTP_REPLAY_TTL_SECONDS,
)


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
    raise_on_expired: bool = False,
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

    if max_age_seconds > 0 and (now_ts - auth_date) > max_age_seconds:
        logger.warning("MiniApp initData expired (age=%ss)", now_ts - auth_date)
        if raise_on_expired:
            raise InitDataExpiredError("Telegram WebApp initData expired")
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


def authenticate_miniapp_request(
    init_data: str | None,
    *,
    raise_on_expired: bool = False,
) -> MiniAppUserContext | None:
    """Validate initData against configured BOT_TOKEN and resolve admin permissions."""
    if not init_data:
        return None

    settings = get_settings()
    user_info = validate_telegram_init_data(
        init_data,
        settings.BOT_TOKEN,
        raise_on_expired=raise_on_expired,
    )
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


async def get_admin_session_epoch(session: AsyncSession) -> str:
    """Fetch current admin session epoch from SystemSetting (default '1') and update cache."""
    global _cached_admin_session_epoch
    raw = await get_system_setting(session, ADMIN_SESSION_EPOCH_KEY)
    epoch = (raw or "1").strip() or "1"
    _cached_admin_session_epoch = epoch
    return epoch


async def revoke_all_admin_sessions(
    session: AsyncSession,
    admin_telegram_id: int | None = None,
) -> str:
    """Increment miniapp_admin_session_epoch so all existing admin session tokens become invalid."""
    current = await get_admin_session_epoch(session)
    try:
        new_epoch = str(int(current) + 1)
    except ValueError:
        new_epoch = "2"
    await set_system_setting(
        session,
        ADMIN_SESSION_EPOCH_KEY,
        new_epoch,
        updated_by=admin_telegram_id,
    )

    async def _update_epoch_after_commit() -> None:
        global _cached_admin_session_epoch
        try:
            if int(new_epoch) > int(_cached_admin_session_epoch):
                _cached_admin_session_epoch = new_epoch
        except ValueError:
            _cached_admin_session_epoch = new_epoch

    queue_post_commit_task(session, _update_epoch_after_commit)
    return new_epoch


def issue_admin_session_token(
    telegram_id: int,
    *,
    ttl_seconds: int = ADMIN_SESSION_TTL_SECONDS,
    epoch: str | None = None,
) -> tuple[str, int]:
    """Issue an HMAC-SHA256 signed short-lived admin session token."""
    exp_ts = int(time.time()) + max(60, ttl_seconds)
    nonce = secrets.token_hex(8)
    epoch_val = (epoch or _cached_admin_session_epoch or "1").strip() or "1"
    payload = f"{telegram_id}:{exp_ts}:{nonce}:{epoch_val}".encode("utf-8")
    sig = hmac.new(_derive_signing_key(b"MiniAppAdminSession"), payload, hashlib.sha256).hexdigest()
    return f"{exp_ts}.{nonce}.{epoch_val}.{sig}", exp_ts


def verify_admin_session_token(
    token: str | None,
    telegram_id: int,
    *,
    expected_epoch: str | None = None,
) -> bool:
    """Verify HMAC-SHA256 signature, epoch, and expiration of an admin session token."""
    if not token or not is_admin(telegram_id):
        return False
    parts = token.strip().split(".")
    current_epoch = (expected_epoch or _cached_admin_session_epoch or "1").strip() or "1"
    if len(parts) == 3:
        if current_epoch != "1":
            return False
        exp_raw, nonce, sig = parts
        token_epoch: str | None = None
    elif len(parts) == 4:
        exp_raw, nonce, token_epoch, sig = parts
        if token_epoch != current_epoch:
            return False
    else:
        return False

    if not exp_raw.isdigit() or not nonce or not sig:
        return False
    exp_ts = int(exp_raw)
    if exp_ts < int(time.time()):
        return False
    if token_epoch is None:
        payload = f"{telegram_id}:{exp_ts}:{nonce}".encode("utf-8")
    else:
        payload = f"{telegram_id}:{exp_ts}:{nonce}:{token_epoch}".encode("utf-8")
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


def _match_totp_step(
    secret: str,
    code: str,
    *,
    window: int = 1,
    now: int | None = None,
) -> int | None:
    """Return matched 30s TOTP time step within +-window, or None if invalid."""
    clean_code = (code or "").strip().replace(" ", "")
    if not clean_code.isdigit() or len(clean_code) != 6:
        return None
    ts = int(time.time()) if now is None else int(now)
    step = ts // 30
    try:
        for delta in range(-window, window + 1):
            candidate_step = step + delta
            if hmac.compare_digest(_compute_hotp(secret, candidate_step), clean_code):
                return candidate_step
    except Exception:
        return None
    return None


def verify_totp_code(
    secret: str,
    code: str,
    *,
    window: int = 1,
    now: int | None = None,
) -> bool:
    """Verify 6-digit RFC 6238 TOTP code within +-window 30s steps."""
    return _match_totp_step(secret, code, window=window, now=now) is not None


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


def _check_2fa_lockout(telegram_id: int) -> int:
    """Return remaining lockout seconds for admin, or 0 if not locked out."""
    entry = _2fa_failures.get(telegram_id)
    if not entry:
        return 0
    _count, window_start, locked_until = entry
    now = time.monotonic()
    if locked_until > now:
        return max(1, int(locked_until - now))
    if now - window_start > ATTEMPT_WINDOW_SECONDS:
        _2fa_failures.pop(telegram_id, None)
    return 0


def _record_2fa_failure(telegram_id: int) -> int:
    """Record a failed 2FA attempt; return lockout seconds if now locked, else 0."""
    now = time.monotonic()
    entry = _2fa_failures.get(telegram_id)
    if not entry or (now - entry[1] > ATTEMPT_WINDOW_SECONDS and entry[2] <= now):
        count, window_start = 1, now
    else:
        count = entry[0] + 1
        window_start = entry[1]

    if count >= MAX_2FA_ATTEMPTS:
        locked_until = now + LOCKOUT_SECONDS
        _2fa_failures[telegram_id] = (count, window_start, locked_until)
        logger.warning("Admin %s temporarily locked out after %d failed 2FA attempts", telegram_id, count)
        return LOCKOUT_SECONDS

    _2fa_failures[telegram_id] = (count, window_start, 0.0)
    return 0


_TOTP_ENC_PREFIX = "enc:"


def _encrypt_totp_secret(secret: str) -> str:
    """Encrypt TOTP secret with DB_ENCRYPTION_KEY before persisting to system_settings."""
    from utils.encryption import _get_active_keys, _get_fernet_engine

    keys_str = _get_active_keys()
    if not keys_str:
        raise RuntimeError("DB_ENCRYPTION_KEY is required to store TOTP secret")
    token = _get_fernet_engine(keys_str).encrypt(secret.encode("utf-8")).decode("utf-8")
    return f"{_TOTP_ENC_PREFIX}{token}"


def _decrypt_totp_secret(stored: str) -> str | None:
    """Decrypt enc:-prefixed TOTP secret, or return legacy plaintext Base32 secret.

    Returns None when decryption fails (invalid token or missing/mismatched key)
    so callers degrade gracefully instead of crashing with HTTP 500.
    """
    clean = stored.strip()
    if not clean.startswith(_TOTP_ENC_PREFIX):
        return clean
    from cryptography.fernet import InvalidToken

    from utils.encryption import _get_active_keys, _get_fernet_engine

    try:
        keys_str = _get_active_keys()
        if not keys_str:
            raise RuntimeError("DB_ENCRYPTION_KEY is required to decrypt TOTP secret")
        raw_token = clean[len(_TOTP_ENC_PREFIX):]
        return _get_fernet_engine(keys_str).decrypt(raw_token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, RuntimeError) as exc:
        logger.error("Failed to decrypt admin TOTP secret: %s", exc)
        return None


async def get_admin_2fa_config(
    session: AsyncSession,
    telegram_id: int,
) -> dict[str, Any]:
    """Resolve active TOTP 2FA configuration for an admin (env overrides DB settings)."""
    env_totp = os.getenv("MINIAPP_ADMIN_TOTP_SECRET", "").strip()
    if env_totp:
        return {"configured": True, "mode": "totp", "secret": env_totp, "source": "env"}

    db_totp = await get_system_setting(session, f"miniapp_admin_totp_{telegram_id}")
    if db_totp:
        decrypted = _decrypt_totp_secret(db_totp)
        if decrypted is None:
            return {
                "configured": True,
                "mode": "error",
                "secret": None,
                "source": "db",
            }
        return {
            "configured": True,
            "mode": "totp",
            "secret": decrypted,
            "source": "db",
            "is_plaintext": not db_totp.strip().startswith(_TOTP_ENC_PREFIX),
        }

    return {"configured": False, "mode": "setup_required", "source": "none"}


async def verify_admin_2fa(
    session: AsyncSession,
    telegram_id: int,
    code: str,
    *,
    setup_token: str | None = None,
) -> dict[str, Any]:
    """Verify admin TOTP 2FA code or finalize initial TOTP setup."""
    if not is_admin(telegram_id):
        return {"verified": False, "error": "Access denied"}

    retry_after = _check_2fa_lockout(telegram_id)
    if retry_after > 0:
        return {
            "verified": False,
            "locked": True,
            "newly_locked": False,
            "retry_after": retry_after,
            "error": f"Слишком много неверных попыток. Повторите через {retry_after} сек.",
        }

    clean_code = (code or "").strip().replace(" ", "")
    cfg = await get_admin_2fa_config(session, telegram_id)

    if setup_token:
        if cfg["configured"]:
            return {"verified": False, "error": "2FA уже настроена"}
        pending_secret = verify_totp_setup_token(setup_token, telegram_id)
        if not pending_secret:
            return {"verified": False, "error": "Срок настройки истёк, сгенерируйте ключ заново"}
        matched_step = _match_totp_step(pending_secret, clean_code)
        if matched_step is None:
            lockout = _record_2fa_failure(telegram_id)
            if lockout > 0:
                return {
                    "verified": False,
                    "locked": True,
                    "newly_locked": True,
                    "retry_after": lockout,
                    "error": f"Слишком много неверных попыток. Повторите через {lockout} сек.",
                }
            return {"verified": False, "error": "Неверный 6-значный код подтверждения"}
        if _used_totp_steps.get((telegram_id, matched_step)):
            return {
                "verified": False,
                "error": "Этот код уже использован. Дождитесь следующего кода.",
            }
        try:
            encrypted_secret = _encrypt_totp_secret(pending_secret)
        except RuntimeError:
            return {
                "verified": False,
                "error": "Ключ шифрования БД (DB_ENCRYPTION_KEY) не настроен на сервере.",
            }
        _used_totp_steps[(telegram_id, matched_step)] = True
        await set_system_setting(
            session,
            f"miniapp_admin_totp_{telegram_id}",
            encrypted_secret,
            updated_by=telegram_id,
        )
        _2fa_failures.pop(telegram_id, None)
        epoch = await get_admin_session_epoch(session)
        token, exp_ts = issue_admin_session_token(telegram_id, epoch=epoch)
        return {
            "verified": True,
            "mode": "totp",
            "session_token": token,
            "expires_at": exp_ts,
        }

    if cfg["mode"] != "totp" or not cfg.get("secret"):
        if cfg["mode"] == "error":
            return {"verified": False, "error": "Ошибка конфигурации 2FA. Обратитесь к администратору."}
        return {"verified": False, "error": "2FA не настроена. Настройте TOTP."}

    matched_step = _match_totp_step(cfg["secret"], clean_code)
    if matched_step is None:
        lockout = _record_2fa_failure(telegram_id)
        if lockout > 0:
            return {
                "verified": False,
                "locked": True,
                "newly_locked": True,
                "retry_after": lockout,
                "error": f"Слишком много неверных попыток. Повторите через {lockout} сек.",
            }
        return {"verified": False, "error": "Неверный код подтверждения"}

    if (telegram_id, matched_step) in _used_totp_steps:
        return {
            "verified": False,
            "error": "Этот код уже использован. Дождитесь следующего кода.",
        }

    if cfg.get("source") == "db" and cfg.get("is_plaintext"):
        try:
            await set_system_setting(
                session,
                f"miniapp_admin_totp_{telegram_id}",
                _encrypt_totp_secret(cfg["secret"]),
                updated_by=telegram_id,
            )
            logger.info("Lazily encrypted legacy plaintext admin TOTP secret for %s", telegram_id)
        except Exception as exc:
            logger.warning(
                "Failed to lazily encrypt legacy admin TOTP secret for %s: %s",
                telegram_id,
                exc,
            )

    _used_totp_steps[(telegram_id, matched_step)] = True
    _2fa_failures.pop(telegram_id, None)
    epoch = await get_admin_session_epoch(session)
    token, exp_ts = issue_admin_session_token(telegram_id, epoch=epoch)
    return {
        "verified": True,
        "mode": "totp",
        "session_token": token,
        "expires_at": exp_ts,
    }
