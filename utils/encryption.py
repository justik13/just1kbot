import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import Text, TypeDecorator

from config.settings import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=10)
def _get_fernet(key: str) -> Fernet:
    return Fernet(key.encode("utf-8"))


class EncryptedString(TypeDecorator):
    impl = Text
    cache_ok = True

    def __init__(self, critical: bool = False, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.critical = critical

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        settings = get_settings()
        key = settings.DB_ENCRYPTION_KEY
        if not key:
            raise RuntimeError(
                "CRITICAL: DB_ENCRYPTION_KEY is empty! "
                "Cannot write sensitive data in plaintext. "
                "Fix .env immediately."
            )
        try:
            f = _get_fernet(key)
            encrypted = f.encrypt(value.encode("utf-8"))
            return encrypted.decode("utf-8")
        except Exception as e:
            logger.error("Encryption failed: %s", type(e).__name__)
            raise RuntimeError("Encryption failed") from e

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        settings = get_settings()
        key = settings.DB_ENCRYPTION_KEY
        if not key:
            if self.critical:
                # ── ИСПРАВЛЕНО: raise вместо return None ──
                raise RuntimeError(
                    "CRITICAL: DB_ENCRYPTION_KEY is empty during "
                    "decryption of a critical field. "
                    "This is a security incident. Fix .env immediately."
                )
            else:
                logger.error(
                    "DB_ENCRYPTION_KEY is empty during decryption. Returning None."
                )
            return None
        try:
            f = _get_fernet(key)
            decrypted = f.decrypt(value.encode("utf-8"))
            return decrypted.decode("utf-8")
        except InvalidToken:
            if self.critical:
                # ── ИСПРАВЛЕНО: raise вместо return None ──
                logger.critical(
                    "Critical encrypted field decryption failed: "
                    "invalid token. Possible causes: "
                    "DB_ENCRYPTION_KEY changed, data corrupted, "
                    "or value stored in plaintext."
                )
                raise RuntimeError(
                    "CRITICAL: Failed to decrypt critical field. "
                    "DB_ENCRYPTION_KEY may have changed or data "
                    "is corrupted. Server cannot operate safely."
                ) from None
            else:
                logger.warning(
                    "Encrypted field decryption failed: invalid token. Returning None."
                )
            return None
        except Exception as e:
            if self.critical:
                logger.critical(
                    "Critical encrypted field decryption failed: %s",
                    type(e).__name__,
                )
                raise RuntimeError(
                    f"CRITICAL: Decryption error: {type(e).__name__}"
                ) from e
            else:
                logger.error(
                    "Encrypted field decryption failed: %s",
                    type(e).__name__,
                )
            return None
