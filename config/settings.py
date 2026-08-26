import os
import re

try:
    import pwd
except ImportError:
    pwd = None
from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SERVICE_USER = "just1kbot"
_SERVICE_RUNTIME_HOME = "/run/just1kbot"


def _configure_database_client_home() -> None:
    """Keep asyncpg/libpq client-file discovery inside the systemd sandbox."""

    if pwd is None or not hasattr(os, "geteuid"):
        return

    try:
        username = pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        return

    if username == _SERVICE_USER:
        # The hardened unit intentionally uses ProtectHome=true. asyncpg still
        # checks standard PostgreSQL client paths below HOME while parsing a
        # DSN, even when no client certificate is configured. Point HOME at the
        # service runtime directory so those checks stay accessible and private.
        os.environ["HOME"] = _SERVICE_RUNTIME_HOME


_configure_database_client_home()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Telegram ──
    BOT_TOKEN: str = Field(repr=False)
    ADMIN_IDS: list[int]
    SUPPORT_USERNAME: str

    # ── Database ──
    DATABASE_URL: str
    DB_ENCRYPTION_KEY: str = Field(repr=False)
    DB_ENCRYPTION_KEYS: str = Field(default="", repr=False)

    # ── Redis ──
    REDIS_URL: str
    REDIS_PASSWORD: str
    REDIS_KEY_PREFIX: str = "just1kbot_bot:"

    # ── YooKassa ──
    YOOKASSA_SHOP_ID: str
    YOOKASSA_SECRET_KEY: str = Field(repr=False)
    YOOKASSA_RETURN_URL: str
    YOOKASSA_WEBHOOK_PORT: int
    DOMAIN: str
    SSL_EMAIL: str

    # ── Amnezia Bridge ──
    AMNEZIA_BRIDGE_HMAC_SECRET: str | None = Field(default=None, repr=False)

    # ── INCY / Subscription Feed ──
    INCY_SUBSCRIPTION_ENABLED: bool = Field(default=True)

    # Removed greenfield settings are declared only so stale .env files fail.
    AMNEZIA_API_URL: str | None = None
    AMNEZIA_API_KEY: str | None = None
    WEBHOOK_URL: str | None = None

    # ── Account balance product limits ──
    BALANCE_MIN_TOPUP_RUB: int = 10
    BALANCE_MAX_CUSTOM_TOPUP_RUB: int = 5000
    BALANCE_MAX_AVAILABLE_RUB: int = 10000
    BALANCE_MAX_PRESET_RUB: int = 1000
    BALANCE_MAX_UNFINISHED_TOPUPS: int = 3
    BALANCE_MAX_TOPUP_CREATIONS_24H: int = 10
    BALANCE_MAX_PRESET_OPTIONS: int = 6

    # ── Security ──
    ALLOW_LOCAL_HTTP: bool = False
    ALLOW_LOCAL_HTTPS: bool = False
    TRUSTED_PROXIES: str = "127.0.0.1,::1,172.16.0.0/12"

    @model_validator(mode="after")
    def reject_removed_settings(self):
        removed = {
            "AMNEZIA_API_URL": self.AMNEZIA_API_URL,
            "AMNEZIA_API_KEY": self.AMNEZIA_API_KEY,
            "WEBHOOK_URL": self.WEBHOOK_URL,
        }
        configured = [name for name, value in removed.items() if value]
        if configured:
            names = ", ".join(sorted(configured))
            raise ValueError(f"removed settings are not supported: {names}")
        return self

    @field_validator("BOT_TOKEN", mode="before")
    @classmethod
    def validate_bot_token(cls, value: str) -> str:
        if isinstance(value, str):
            value = value.strip().strip("'").strip('"')
        token = value.strip()
        if re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token) is None:
            raise ValueError("BOT_TOKEN has invalid format")
        return token

    @field_validator("ADMIN_IDS", mode="before")
    @classmethod
    def validate_admin_ids(cls, value: Any) -> list[int]:
        if isinstance(value, str):
            value = value.strip().strip("'").strip('"')
            import json
            try:
                value = json.loads(value)
            except Exception:
                pass
        if not isinstance(value, list) or not value or any(not isinstance(item, int) or item <= 0 for item in value):
            raise ValueError("ADMIN_IDS must contain positive Telegram IDs")
        return value

    @field_validator("YOOKASSA_RETURN_URL", mode="before")
    @classmethod
    def validate_yookassa_return_url(cls, value: str) -> str:
        if isinstance(value, str):
            value = value.strip().strip("'").strip('"')
        if "{bot_username}" not in value:
            raise ValueError(
                "YOOKASSA_RETURN_URL must contain '{bot_username}' placeholder"
            )
        return value

    @field_validator(
        "DATABASE_URL",
        "REDIS_URL",
        "REDIS_PASSWORD",
        "YOOKASSA_SHOP_ID",
        "YOOKASSA_SECRET_KEY",
        mode="before",
    )
    @classmethod
    def validate_required_value(cls, value: str) -> str:
        if isinstance(value, str):
            value = value.strip().strip("'").strip('"')
        normalized = value.strip()
        if not normalized or "change_me" in normalized.lower():
            raise ValueError(
                "required production setting is empty or placeholder"
            )
        return normalized

    @field_validator("DB_ENCRYPTION_KEY", mode="before")
    @classmethod
    def validate_db_encryption_key(cls, value: str) -> str:
        if isinstance(value, str):
            value = value.strip().strip("'").strip('"')
        normalized = value.strip()
        if not normalized or "change_me" in normalized.lower():
            raise ValueError(
                "DB_ENCRYPTION_KEY is required and must not be empty or placeholder"
            )
        from cryptography.fernet import Fernet
        try:
            Fernet(normalized.encode("utf-8"))
        except Exception as exc:
            raise ValueError(f"DB_ENCRYPTION_KEY must be a valid 32-byte base64 Fernet key: {exc}") from exc
        return normalized

    @field_validator("DB_ENCRYPTION_KEYS", mode="before")
    @classmethod
    def validate_db_encryption_keys(cls, value: Any) -> str:
        if not value or not isinstance(value, str):
            return ""
        normalized = value.strip().strip("'").strip('"')
        if not normalized:
            return ""
        from cryptography.fernet import Fernet
        raw_keys = [
            k.strip().strip("'").strip('"')
            for k in normalized.split(",")
            if k.strip().strip("'").strip('"')
        ]
        valid_keys: list[str] = []
        for k in raw_keys:
            try:
                Fernet(k.encode("utf-8"))
                valid_keys.append(k)
            except Exception as exc:
                raise ValueError(f"Invalid Fernet key in DB_ENCRYPTION_KEYS: {exc}") from exc
        return ",".join(valid_keys)

    @field_validator("DOMAIN", mode="before")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        if isinstance(value, str):
            value = value.strip().strip("'").strip('"')
        domain = value.strip().lower().rstrip(".")
        labels = domain.split(".")
        label_pattern = re.compile(
            r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
        )
        if (
            not domain
            or len(domain) > 253
            or len(labels) < 2
            or any(
                label_pattern.fullmatch(label) is None
                for label in labels
            )
        ):
            raise ValueError("DOMAIN must be a valid public hostname")
        return domain

    @field_validator("SSL_EMAIL", mode="before")
    @classmethod
    def validate_ssl_email(cls, value: str) -> str:
        if isinstance(value, str):
            value = value.strip().strip("'").strip('"')
        email = value.strip().lower()
        if (
            not email
            or email in {"admin@example.com", "owner@example.com", "change_me@example.com"}
            or re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
            is None
        ):
            raise ValueError("SSL_EMAIL must be a real certificate email")
        return email

    @field_validator("YOOKASSA_WEBHOOK_PORT", mode="before")
    @classmethod
    def validate_webhook_port(cls, value: Any) -> int:
        if isinstance(value, str):
            value = value.strip().strip("'").strip('"')
        int_val = int(value)
        if int_val < 1 or int_val > 65535:
            raise ValueError(
                "YOOKASSA_WEBHOOK_PORT must be between 1 and 65535"
            )
        return int_val

    @field_validator("SUPPORT_USERNAME", mode="before")
    @classmethod
    def validate_support_username(cls, value: str) -> str:
        if isinstance(value, str):
            value = value.strip().strip("'").strip('"')
        username = value.strip().lstrip("@")
        if (
            not username
            or username.lower() in {"support", "change_me_support_username"}
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", username) is None
        ):
            raise ValueError("SUPPORT_USERNAME must be a real Telegram username")
        return username

    @field_validator("AMNEZIA_BRIDGE_HMAC_SECRET", mode="before")
    @classmethod
    def validate_amnezia_bridge_hmac_secret(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("AMNEZIA_BRIDGE_HMAC_SECRET must be a string")
        if not value:
            return None
        if value != value.strip():
            raise ValueError(
                "AMNEZIA_BRIDGE_HMAC_SECRET must not contain leading or trailing whitespace"
            )
        if re.fullmatch(r"^[0-9a-fA-F]{64}$", value) is None:
            raise ValueError(
                "AMNEZIA_BRIDGE_HMAC_SECRET must be exactly 64 hexadecimal characters"
            )
        if value.lower() in {"0" * 64, "a" * 64, "f" * 64, "1" * 64}:
            raise ValueError(
                "AMNEZIA_BRIDGE_HMAC_SECRET cannot be a trivial repeating placeholder"
            )
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
