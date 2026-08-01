import os
import pwd
from functools import lru_cache
from typing import List

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
    ADMIN_IDS: List[int] = []
    SUPPORT_USERNAME: str = "support"

    # ── Database ──
    DATABASE_URL: str
    DB_ENCRYPTION_KEY: str

    # ── Redis ──
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_PASSWORD: str = ""
    REDIS_KEY_PREFIX: str = "just1kbot_bot:"

    # ── YooKassa ──
    YOOKASSA_SHOP_ID: str = ""
    YOOKASSA_SECRET_KEY: str = ""
    YOOKASSA_RETURN_URL: str = "https://t.me/{bot_username}"
    YOOKASSA_WEBHOOK_PORT: int = 8080

    # ── Security ──
    ALLOW_LOCAL_HTTP: bool = False
    ALLOW_LOCAL_HTTPS: bool = False


@lru_cache()
def get_settings() -> Settings:
    return Settings()
