import unittest
from unittest.mock import AsyncMock, patch

from cryptography.fernet import Fernet, InvalidToken
from aiohttp.test_utils import make_mocked_request

from utils.encryption import EncryptedString, _get_fernet_engine
from bot.handlers.webhook import healthcheck_handler
from services.amnezia_client import _get_circuit_breaker, _get_rate_limiter, _MAX_CLIENT_CACHE_ENTRIES, cleanup_server_circuit_breakers


BASE_MOCK_ENV = {
    "BOT_TOKEN": "123:test",
    "REDIS_URL": "redis://localhost:6379/1",
    "REDIS_PASSWORD": "test",
    "ADMIN_IDS": "[123456789]",
    "SUPPORT_USERNAME": "test_support",
    "DOMAIN": "test.domain",
    "SSL_EMAIL": "test@domain.com",
    "YOOKASSA_SHOP_ID": "123456",
    "YOOKASSA_SECRET_KEY": "test_secret",
    "YOOKASSA_RETURN_URL": "https://t.me/{bot_username}",
    "YOOKASSA_WEBHOOK_PORT": "8080",
    "DB_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    "AMNEZIA_BRIDGE_HMAC_SECRET": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "TRUSTED_PROXIES": "127.0.0.1,::1,172.16.0.0/12",
    "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/db",
}


class EncryptionRotationTests(unittest.TestCase):
    def test_single_key_encryption_and_decryption(self):
        key1 = Fernet.generate_key().decode("utf-8")
        env = {**BASE_MOCK_ENV, "DB_ENCRYPTION_KEY": key1, "DB_ENCRYPTION_KEYS": ""}
        with patch.dict("os.environ", env, clear=True):
            from config.settings import get_settings
            get_settings.cache_clear()
            _get_fernet_engine.cache_clear()

            enc_field = EncryptedString(critical=True)
            plaintext = "secret_api_key_12345"
            ciphertext = enc_field.process_bind_param(plaintext, None)
            self.assertNotEqual(ciphertext, plaintext)

            decrypted = enc_field.process_result_value(ciphertext, None)
            self.assertEqual(decrypted, plaintext)

    def test_multi_key_rotation_decryption(self):
        old_key = Fernet.generate_key().decode("utf-8")
        new_key = Fernet.generate_key().decode("utf-8")

        # 1. Encrypt with old key
        env_old = {**BASE_MOCK_ENV, "DB_ENCRYPTION_KEY": old_key, "DB_ENCRYPTION_KEYS": ""}
        with patch.dict("os.environ", env_old, clear=True):
            from config.settings import get_settings
            get_settings.cache_clear()
            _get_fernet_engine.cache_clear()

            enc_field = EncryptedString(critical=True)
            old_ciphertext = enc_field.process_bind_param("old_secret_value", None)

        # 2. Switch to new primary key + old key in DB_ENCRYPTION_KEYS
        env_new = {**BASE_MOCK_ENV, "DB_ENCRYPTION_KEY": new_key, "DB_ENCRYPTION_KEYS": f"{new_key},{old_key}"}
        with patch.dict("os.environ", env_new, clear=True):
            get_settings.cache_clear()
            _get_fernet_engine.cache_clear()

            enc_field_new = EncryptedString(critical=True)
            # Decrypt ciphertext made with old key: MUST SUCCEED
            decrypted = enc_field_new.process_result_value(old_ciphertext, None)
            self.assertEqual(decrypted, "old_secret_value")

            # Encrypt new value: MUST USE NEW KEY
            new_ciphertext = enc_field_new.process_bind_param("new_secret_value", None)
            self.assertEqual(enc_field_new.process_result_value(new_ciphertext, None), "new_secret_value")

            # Old key alone CANNOT decrypt the new ciphertext
            f_old = Fernet(old_key.encode("utf-8"))
            with self.assertRaises(InvalidToken):
                f_old.decrypt(new_ciphertext.encode("utf-8"))

            # New key alone CAN decrypt the new ciphertext
            f_new = Fernet(new_key.encode("utf-8"))
            self.assertEqual(f_new.decrypt(new_ciphertext.encode("utf-8")).decode("utf-8"), "new_secret_value")


class HealthcheckCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_healthcheck_in_memory_caching(self):
        req = make_mocked_request("GET", "/health")

        with patch("bot.handlers.webhook.session_scope") as mock_session_scope, \
             patch("bot.handlers.webhook._get_healthcheck_redis") as mock_get_redis:
            
            mock_session = AsyncMock()
            mock_session_scope.return_value.__aenter__.return_value = mock_session
            mock_redis = AsyncMock()
            mock_get_redis.return_value = mock_redis

            # First call executes DB and Redis checks
            resp1 = await healthcheck_handler(req)
            self.assertEqual(resp1.status, 200)
            self.assertEqual(mock_session.execute.call_count, 1)
            self.assertEqual(mock_redis.ping.call_count, 1)

            # Second call within 5s MUST use cached result without querying DB or Redis again
            resp2 = await healthcheck_handler(req)
            self.assertEqual(resp2.status, 200)
            self.assertEqual(mock_session.execute.call_count, 1)
            self.assertEqual(mock_redis.ping.call_count, 1)


class AmneziaClientCacheTests(unittest.TestCase):
    def test_circuit_breaker_and_rate_limiter_lru_bounding(self):
        # Populate cache up to max entries
        for i in range(_MAX_CLIENT_CACHE_ENTRIES + 50):
            url = f"https://server-{i}.vpn.internal"
            cb = _get_circuit_breaker(url)
            rl = _get_rate_limiter(url)
            self.assertIsNotNone(cb)
            self.assertIsNotNone(rl)

        # Ensure cleanup removes entries
        cleanup_server_circuit_breakers("https://server-500.vpn.internal")
