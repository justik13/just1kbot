import os
import re

try:
    import pwd
except ImportError:
    pwd = None
from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator
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
    BOT_USERNAME: str = ""
    STATUS_PAGE_URL: str = "https://status.just1k.pro/status/just1kbot"
    CHANNEL_URL: str = "https://t.me/just1k_news"
    PROXY_URL: str = ""

    # ── Mini App & Web ──
    MINIAPP_URL: str = ""
    DOMAIN: str = "just1k.pro"
    CDN_DOMAIN: str = "cdn.just1k.pro"

    # ── Database ──
    DATABASE_URL: str = Field(repr=False)
    DB_ENCRYPTION_KEY: str = Field(repr=False)
    DB_ENCRYPTION_KEYS: str = Field(default="", repr=False)

    # ── Redis ──
    REDIS_URL: str = Field(repr=False)
    REDIS_PASSWORD: str = Field(default="", repr=False)

    # ── YooKassa ──
    YOOKASSA_SHOP_ID: str = ""
    YOOKASSA_SECRET_KEY: str = Field(default="", repr=False)
    YOOKASSA_RETURN_URL: str = "https://just1k.pro"
    YOOKASSA_WEBHOOK_PORT: int = 8080
    SSL_EMAIL: str = "admin@just1k.pro"
    YOOKASSA_EXTRA_IPS: str = ""

    # ── Account balance limits ──
    BALANCE_MIN_TOPUP_RUB: int = 10
    BALANCE_MAX_CUSTOM_TOPUP_RUB: int = 5000
    BALANCE_MAX_PRESET_RUB: int = 1000
    BALANCE_MAX_UNFINISHED_TOPUPS: int = 3
    BALANCE_MAX_PRESET_OPTIONS: int = 6

    # ── Security ──
    ALLOW_LOCAL_HTTP: bool = False
    ALLOW_LOCAL_HTTPS: bool = False
    TRUSTED_PROXIES: str = "127.0.0.1,::1,172.16.0.0/12"
    LOG_LEVEL: str = "INFO"

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in allowed:
            raise ValueError(
                f"LOG_LEVEL must be one of {sorted(allowed)}, got {value!r}"
            )
        return normalized

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
        if isinstance(value, int):
            return [value]
        if isinstance(value, str):
            value = value.strip().strip("'").strip('"')
            import json
            try:
                parsed = json.loads(value)
                if isinstance(parsed, int):
                    return [parsed]
                if isinstance(parsed, list):
                    return [int(x) for x in parsed]
            except json.JSONDecodeError:
                pass
            return [int(x.strip()) for x in value.split(",") if x.strip()]
        if isinstance(value, list):
            return [int(x) for x in value]
        return []

    @field_validator("SUPPORT_USERNAME", mode="before")
    @classmethod
    def validate_support_username(cls, value: str) -> str:
        if isinstance(value, str):
            value = value.strip().strip("'").strip('"')
        username = value.strip().lstrip("@")
        return username

    @property
    def yookassa_allowed_ip_ranges(self) -> tuple[str, ...]:
        from config.constants import YOOKASSA_IP_RANGES

        if not self.YOOKASSA_EXTRA_IPS:
            return YOOKASSA_IP_RANGES
        extra = [
            item.strip()
            for item in self.YOOKASSA_EXTRA_IPS.split()
            if item.strip()
        ]
        return YOOKASSA_IP_RANGES + tuple(extra)


@lru_cache
def get_settings() -> Settings:
    return Settings()
