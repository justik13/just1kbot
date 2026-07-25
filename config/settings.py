from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    REDIS_KEY_PREFIX: str = "projectx_bot:"

    # ── YooKassa ──
    YOOKASSA_SHOP_ID: str = ""
    YOOKASSA_SECRET_KEY: str = ""
    YOOKASSA_RETURN_URL: str = "https://t.me/{bot_username}"
    YOOKASSA_WEBHOOK_PORT: int = 8080

    # Включать ли автоматические чеки YooKassa.
    # По умолчанию выключено, чтобы не отправлять фиктивные receipt email.
    YOOKASSA_RECEIPTS_ENABLED: bool = False

    # ── Security ──
    ALLOW_LOCAL_HTTP: bool = False
    ALLOW_LOCAL_HTTPS: bool = False


@lru_cache()
def get_settings() -> Settings:
    return Settings()