import os
import re
import pwd
from functools import lru_cache
from typing import List

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_SERVICE_USER = "just1kbot"
_SERVICE_RUNTIME_HOME = "/run/just1kbot"


def _configure_database_client_home() -> None:
    """Keep asyncpg/libpq client-file discovery inside the systemd sandbox."""

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
    BOT_TOKEN: str
    ADMIN_IDS: List[int]
    SUPPORT_USERNAME: str

    # ── Database ──
    DATABASE_URL: str
    DB_ENCRYPTION_KEY: str

    # ── Redis ──
    REDIS_URL: str
    REDIS_PASSWORD: str
    REDIS_KEY_PREFIX: str = "just1kbot_bot:"

    # ── YooKassa ──
    YOOKASSA_SHOP_ID: str
    YOOKASSA_SECRET_KEY: str
    YOOKASSA_RETURN_URL: str
    YOOKASSA_WEBHOOK_PORT: int
    DOMAIN: str
    SSL_EMAIL: str

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

    @field_validator("BOT_TOKEN")
    @classmethod
    def validate_bot_token(cls, value: str) -> str:
        token = value.strip()
        if re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token) is None:
            raise ValueError("BOT_TOKEN has invalid format")
        return token

    @field_validator("ADMIN_IDS")
    @classmethod
    def validate_admin_ids(cls, value: List[int]) -> List[int]:
        if not value or any(item <= 0 for item in value):
            raise ValueError("ADMIN_IDS must contain positive Telegram IDs")
        return value

    @field_validator(
        "DATABASE_URL",
        "DB_ENCRYPTION_KEY",
        "REDIS_URL",
        "REDIS_PASSWORD",
        "YOOKASSA_SHOP_ID",
        "YOOKASSA_SECRET_KEY",
        "YOOKASSA_RETURN_URL",
    )
    @field_validator("YOOKASSA_RETURN_URL")
    @classmethod
    def validate_yookassa_return_url(cls, value: str) -> str:
        # P1-6: Валидация формата URL
        if "{bot_username}" not in value:
            raise ValueError("YOOKASSA_RETURN_URL must contain '{bot_username}' placeholder")
        return value

    @field_validator(
        "DB_ENCRYPTION_KEY",
        "BOT_TOKEN",
        "ADMIN_IDS",
        "DATABASE_URL",
        "YOOKASSA_SHOP_ID",
        "YOOKASSA_SECRET_KEY",
        "YOOKASSA_RETURN_URL",
    )
    @classmethod
    def validate_required_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or "change_me" in normalized.lower():
            raise ValueError(
                "required production setting is empty or placeholder"
            )
        return normalized

    @field_validator("DOMAIN")
    @classmethod
    def validate_domain(cls, value: str) -> str:
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

    @field_validator("SSL_EMAIL")
    @classmethod
    def validate_ssl_email(cls, value: str) -> str:
        email = value.strip().lower()
        if (
            not email
            or email in {"admin@example.com", "change_me@example.com"}
            or re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
            is None
        ):
            raise ValueError("SSL_EMAIL must be a real certificate email")
        return email

    @field_validator("YOOKASSA_WEBHOOK_PORT")
    @classmethod
    def validate_webhook_port(cls, value: int) -> int:
        if value < 1 or value > 65535:
            raise ValueError(
                "YOOKASSA_WEBHOOK_PORT must be between 1 and 65535"
            )
        return value

    @field_validator("SUPPORT_USERNAME")
    @classmethod
    def validate_support_username(cls, value: str) -> str:
        username = value.strip().lstrip("@")
        if (
            not username
            or username.lower() in {"support", "change_me_support_username"}
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", username) is None
        ):
            raise ValueError("SUPPORT_USERNAME must be a real Telegram username")
        return username


@lru_cache()
def get_settings() -> Settings:
    return Settings()
