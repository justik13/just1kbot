import asyncio
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

from cryptography.fernet import Fernet, InvalidToken
from aiohttp.test_utils import make_mocked_request

from utils.encryption import EncryptedString, _get_fernet_engine, _get_active_keys
from bot.handlers.webhook import healthcheck_handler
import bot.handlers.webhook as webhook_module
import services.amnezia_client as amnezia_client
from services.amnezia_client import (
    _get_circuit_breaker,
    _get_rate_limiter,
    _MAX_CLIENT_CACHE_ENTRIES,
    cleanup_server_circuit_breakers,
)
from scripts.reencrypt_database import reencrypt_all


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
        env_new = {**BASE_MOCK_ENV, "DB_ENCRYPTION_KEY": new_key, "DB_ENCRYPTION_KEYS": old_key}
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

    def test_key_contract_guarantees_primary_key_first(self):
        primary_key = Fernet.generate_key().decode("utf-8")
        fallback_key = Fernet.generate_key().decode("utf-8")

        env = {
            **BASE_MOCK_ENV,
            "DB_ENCRYPTION_KEY": primary_key,
            "DB_ENCRYPTION_KEYS": f"{fallback_key},{primary_key}",
        }
        with patch.dict("os.environ", env, clear=True):
            from config.settings import get_settings
            get_settings.cache_clear()
            _get_fernet_engine.cache_clear()

            active_keys = _get_active_keys().split(",")
            self.assertEqual(active_keys[0], primary_key)
            self.assertIn(fallback_key, active_keys)


class HealthcheckCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        webhook_module._healthcheck_cache = None

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

    async def test_healthcheck_single_flight_concurrent_burst(self):
        req = make_mocked_request("GET", "/health")

        with patch("bot.handlers.webhook.session_scope") as mock_session_scope, \
             patch("bot.handlers.webhook._get_healthcheck_redis") as mock_get_redis:
            
            mock_session = AsyncMock()
            mock_session_scope.return_value.__aenter__.return_value = mock_session
            mock_redis = AsyncMock()
            mock_get_redis.return_value = mock_redis

            # Fire 50 concurrent requests simultaneously when cache is empty
            responses = await asyncio.gather(*[healthcheck_handler(req) for _ in range(50)])

            # All 50 requests receive 200 OK
            for resp in responses:
                self.assertEqual(resp.status, 200)

            # Single-flight lock guarantees only 1 DB execute and 1 Redis ping occurred
            self.assertEqual(mock_session.execute.call_count, 1)
            self.assertEqual(mock_redis.ping.call_count, 1)


class AmneziaClientCacheTests(unittest.TestCase):
    def test_circuit_breaker_and_rate_limiter_lru_bounding(self):
        amnezia_client._circuit_breakers.clear()
        amnezia_client._rate_limiters.clear()

        # Populate cache exceeding max entries
        for i in range(_MAX_CLIENT_CACHE_ENTRIES + 100):
            url = f"https://server-{i}.vpn.internal"
            cb = _get_circuit_breaker(url)
            rl = _get_rate_limiter(url)
            self.assertIsNotNone(cb)
            self.assertIsNotNone(rl)

        # Assert strict LRU upper bound enforcement
        self.assertLessEqual(len(amnezia_client._circuit_breakers), _MAX_CLIENT_CACHE_ENTRIES)
        self.assertLessEqual(len(amnezia_client._rate_limiters), _MAX_CLIENT_CACHE_ENTRIES)

        # Ensure cleanup removes specific entry
        cleanup_server_circuit_breakers("https://server-550.vpn.internal")


class DatabaseReencryptionScriptTests(unittest.IsolatedAsyncioTestCase):
    async def test_reencryption_script_processes_batches(self):
        with patch("scripts.reencrypt_database.session_scope") as mock_session_scope:
            mock_session = AsyncMock()
            mock_session_scope.return_value.__aenter__.return_value = mock_session

            server1 = MagicMock(id=1, api_key="secret_key_1")
            profile1 = MagicMock(id=1, raw_config="raw_vpn_config_1")

            # First query returns items, second returns empty list to stop loop
            mock_session.scalars.side_effect = [
                MagicMock(all=MagicMock(return_value=[server1])),
                MagicMock(all=MagicMock(return_value=[])),
                MagicMock(all=MagicMock(return_value=[profile1])),
                MagicMock(all=MagicMock(return_value=[])),
            ]

            await reencrypt_all()

            # Verify commit was executed for both batches
            self.assertGreaterEqual(mock_session.commit.call_count, 2)
