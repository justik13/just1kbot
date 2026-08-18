import os
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from bot.handlers.amnezia_bridge import amnezia_bridge_handler
from utils.http_rate_limiter import HttpRateLimiter, get_trusted_client_ip


class AmneziaBridgeRateLimitTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.env_patcher = patch.dict(
            os.environ,
            {
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
                "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/db",
            },
        )
        cls.env_patcher.start()
        from config.settings import get_settings
        get_settings.cache_clear()

    @classmethod
    def tearDownClass(cls):
        from config.settings import get_settings
        get_settings.cache_clear()
        cls.env_patcher.stop()

    def test_rate_limiter_burst_and_wait_time(self):
        limiter = HttpRateLimiter(rate_per_minute=30.0, burst=10)
        now = 1000.0

        # Burst of 10 requests allowed
        for _ in range(10):
            allowed, retry_after = limiter.check("client_1", now=now)
            self.assertTrue(allowed)
            self.assertEqual(retry_after, 0)

        # 11th request denied
        allowed, retry_after = limiter.check("client_1", now=now)
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry_after, 1)

        # Different client IP is still allowed
        allowed, retry_after = limiter.check("client_2", now=now)
        self.assertTrue(allowed)

        # Advancing time by 2 seconds refills 1 token
        now += 2.0
        allowed, retry_after = limiter.check("client_1", now=now)
        self.assertTrue(allowed)

    def test_trusted_client_ip_extraction(self):
        # Case 1: Untrusted client directly connecting (no trusted proxy configured)
        req = make_mocked_request(
            "GET",
            "/amnezia/open/1",
            headers={"X-Forwarded-For": "203.0.113.195"},
        )
        # Without trusted proxy configuration, X-Forwarded-For MUST be ignored
        ip = get_trusted_client_ip(req)
        self.assertEqual(ip, req.remote or "127.0.0.1")

        # Case 2: Configured trusted proxy
        app = web.Application()
        app["trusted_proxies"] = {"127.0.0.1"}
        req_with_proxy = make_mocked_request(
            "GET",
            "/amnezia/open/1",
            headers={"X-Forwarded-For": "203.0.113.195, 10.0.0.1"},
            app=app,
        )
        ip = get_trusted_client_ip(req_with_proxy)
        self.assertEqual(ip, "203.0.113.195")

    @patch("bot.handlers.amnezia_bridge.amnezia_bridge_rate_limiter.check", return_value=(False, 15))
    async def test_endpoint_returns_429_with_retry_after(self, mock_check):
        query = {"uid": "1", "exp": "1700000000", "sig": "a" * 64}
        req = make_mocked_request(
            "GET",
            "/amnezia/open/1",
            match_info={"profile_id": "1"},
        )
        req._query = query
        req._rel_url = req._rel_url.with_query(query)
        resp = await amnezia_bridge_handler(req)
        self.assertEqual(resp.status, 429)
        self.assertEqual(resp.headers["Retry-After"], "15")
        self.assertEqual(resp.headers["Content-Type"], "text/html; charset=utf-8")
        self.assertIn("Слишком много запросов", resp.text)


if __name__ == "__main__":
    unittest.main()
