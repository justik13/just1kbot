import os
import unittest
from unittest.mock import patch

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

    def test_rate_limiter_bounded_lru_and_cleanup(self):
        limiter = HttpRateLimiter(rate_per_minute=30.0, burst=10, max_entries=3)
        now = 100.0

        limiter.check("ip_1", now=now)
        limiter.check("ip_2", now=now)
        limiter.check("ip_3", now=now)
        self.assertEqual(len(limiter.buckets), 3)

        # Adding 4th entry evicts least recently used (ip_1)
        limiter.check("ip_4", now=now)
        self.assertEqual(len(limiter.buckets), 3)
        self.assertNotIn("ip_1", limiter.buckets)
        self.assertIn("ip_4", limiter.buckets)

        # Cleanup of stale entries after 130s
        now += 130.0
        limiter._cleanup(now)
        self.assertEqual(len(limiter.buckets), 0)

    def test_trusted_client_ip_extraction_from_caddy_and_docker(self):
        # Scenario 1: Request from Caddy via Docker private network (172.18.0.3)
        req_caddy = make_mocked_request(
            "GET",
            "/amnezia/open/1",
            headers={"X-Real-IP": "203.0.113.50"},
        )
        req_caddy._transport_peername = ("172.18.0.3", 12345)
        ip = get_trusted_client_ip(req_caddy)
        self.assertEqual(ip, "203.0.113.50")

        # Scenario 2: Request from Caddy via loopback (127.0.0.1) with X-Forwarded-For
        req_loopback = make_mocked_request(
            "GET",
            "/amnezia/open/1",
            headers={"X-Forwarded-For": "198.51.100.25, 127.0.0.1"},
        )
        req_loopback._transport_peername = ("127.0.0.1", 12345)
        ip = get_trusted_client_ip(req_loopback)
        self.assertEqual(ip, "198.51.100.25")

        # Scenario 3: Untrusted client directly connecting from public IP (93.184.216.34)
        # Attempting to spoof X-Real-IP
        req_untrusted = make_mocked_request(
            "GET",
            "/amnezia/open/1",
            headers={"X-Real-IP": "1.1.1.1", "X-Forwarded-For": "2.2.2.2"},
        )
        req_untrusted._transport_peername = ("93.184.216.34", 12345)
        ip = get_trusted_client_ip(req_untrusted)
        # Header spoof MUST be ignored; uses direct remote
        self.assertEqual(ip, "93.184.216.34")

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
