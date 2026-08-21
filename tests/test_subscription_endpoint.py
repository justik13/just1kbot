from datetime import timedelta
import os
import unittest
from unittest.mock import AsyncMock, patch
from aiohttp.test_utils import make_mocked_request

from bot.handlers.subscription_feed import (
    subscription_feed_handler,
    subscription_open_handler,
)
from database.models import User
from utils.datetime_helpers import now_utc
from utils.http_rate_limiter import subscription_feed_rate_limiter


class SubscriptionEndpointTests(unittest.IsolatedAsyncioTestCase):
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

    def setUp(self):
        subscription_feed_rate_limiter.reset()

    @patch("bot.handlers.subscription_feed.SubscriptionTokenService.get_user_by_token")
    @patch("bot.handlers.subscription_feed.SubscriptionFeedService.build_feed")
    @patch("bot.handlers.subscription_feed.session_scope")
    async def test_endpoint_valid_token(
        self, mock_session_scope, mock_build_feed, mock_get_user
    ):
        mock_session = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = session
        session.begin_nested.return_value.__aexit__.return_value = None
        mock_session_scope.return_value.__aenter__.return_value = mock_session

        user = User(id=77, telegram_id=888, subscription_token="valid_token_xyz")
        mock_get_user.return_value = user
        mock_build_feed.return_value = (
            200,
            {
                "Content-Type": "text/plain; charset=utf-8",
                "Cache-Control": "no-store",
                "profile-title": "JUST1K VPN",
            },
            "b64_feed_payload",
        )

        req = make_mocked_request("GET", "/sub/valid_token_xyz", match_info={"token": "valid_token_xyz"})
        response = await subscription_feed_handler(req)

        self.assertEqual(response.status, 200)
        self.assertEqual(response.text, "b64_feed_payload")
        self.assertEqual(response.headers["Content-Type"], "text/plain; charset=utf-8")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        mock_get_user.assert_awaited_once_with(mock_session, "valid_token_xyz")
        mock_build_feed.assert_awaited_once_with(mock_session, user)

    @patch("bot.handlers.subscription_feed.SubscriptionTokenService.get_user_by_token")
    @patch("bot.handlers.subscription_feed.session_scope")
    async def test_endpoint_invalid_or_unknown_token(
        self, mock_session_scope, mock_get_user
    ):
        mock_session = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = session
        session.begin_nested.return_value.__aexit__.return_value = None
        mock_session_scope.return_value.__aenter__.return_value = mock_session
        mock_get_user.return_value = None

        req = make_mocked_request("GET", "/sub/non_existent_token", match_info={"token": "non_existent_token"})
        response = await subscription_feed_handler(req)

        self.assertEqual(response.status, 404)
        self.assertEqual(response.text, "Not Found")

    async def test_endpoint_empty_or_excessive_token(self):
        req_empty = make_mocked_request("GET", "/sub/", match_info={"token": ""})
        resp_empty = await subscription_feed_handler(req_empty)
        self.assertEqual(resp_empty.status, 404)

        req_65 = make_mocked_request("GET", "/sub/" + "a" * 65, match_info={"token": "a" * 65})
        resp_65 = await subscription_feed_handler(req_65)
        self.assertEqual(resp_65.status, 404)

        req_huge = make_mocked_request("GET", "/sub/" + "a" * 200, match_info={"token": "a" * 200})
        resp_huge = await subscription_feed_handler(req_huge)
        self.assertEqual(resp_huge.status, 404)

    @patch("bot.handlers.subscription_feed.SubscriptionTokenService.get_user_by_token")
    @patch("bot.handlers.subscription_feed.session_scope")
    async def test_open_endpoint_valid_token(
        self, mock_session_scope, mock_get_user
    ):
        mock_session = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = session
        session.begin_nested.return_value.__aexit__.return_value = None
        mock_session_scope.return_value.__aenter__.return_value = mock_session

        user = User(
            id=77,
            telegram_id=888,
            subscription_token="valid_token_xyz",
            subscription_end=now_utc() + timedelta(days=30),
        )
        mock_get_user.return_value = user

        req = make_mocked_request("GET", "/sub/open/valid_token_xyz", match_info={"token": "valid_token_xyz"})
        response = await subscription_open_handler(req)

        self.assertEqual(response.status, 200)
        self.assertIn("text/html", response.headers["Content-Type"])
        self.assertIn("incy://import/https://", response.text)
        self.assertIn("valid_token_xyz", response.text)
        self.assertIn("Подключение к INCY", response.text)
        mock_get_user.assert_awaited_once_with(mock_session, "valid_token_xyz")

    @patch("bot.handlers.subscription_feed.SubscriptionTokenService.get_user_by_token")
    @patch("bot.handlers.subscription_feed.session_scope")
    async def test_open_endpoint_invalid_or_unknown_token(
        self, mock_session_scope, mock_get_user
    ):
        mock_session = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = session
        session.begin_nested.return_value.__aexit__.return_value = None
        mock_session_scope.return_value.__aenter__.return_value = mock_session
        mock_get_user.return_value = None

        req = make_mocked_request("GET", "/sub/open/bad_token", match_info={"token": "bad_token"})
        response = await subscription_open_handler(req)

        self.assertEqual(response.status, 404)
        self.assertIn("text/html", response.headers["Content-Type"])
        self.assertIn("404", response.text)

    async def test_open_endpoint_empty_or_excessive_token(self):
        req_empty = make_mocked_request("GET", "/sub/open/", match_info={"token": ""})
        resp_empty = await subscription_open_handler(req_empty)
        self.assertEqual(resp_empty.status, 404)

        req_65 = make_mocked_request("GET", "/sub/open/" + "a" * 65, match_info={"token": "a" * 65})
        resp_65 = await subscription_open_handler(req_65)
        self.assertEqual(resp_65.status, 404)

    @patch("bot.handlers.subscription_feed.subscription_feed_rate_limiter.check")
    async def test_open_endpoint_rate_limit_uses_correct_429_page(self, mock_check):
        mock_check.return_value = (False, 12)

        req = make_mocked_request(
            "GET",
            "/sub/open/valid_token_xyz",
            match_info={"token": "valid_token_xyz"},
        )
        response = await subscription_open_handler(req)

        self.assertEqual(response.status, 429)
        self.assertEqual(response.headers["Retry-After"], "12")
        self.assertIn("Слишком много запросов", response.text)
        self.assertNotIn("Подписка не активна", response.text)

    @patch("bot.handlers.subscription_feed.SubscriptionTokenService.get_user_by_token")
    @patch("bot.handlers.subscription_feed.session_scope")
    async def test_open_endpoint_security_headers(
        self, mock_session_scope, mock_get_user
    ):
        mock_session = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = session
        session.begin_nested.return_value.__aexit__.return_value = None
        mock_session_scope.return_value.__aenter__.return_value = mock_session

        user = User(
            id=77,
            telegram_id=888,
            subscription_token="valid_token_xyz",
            subscription_end=now_utc() + timedelta(days=30),
        )
        mock_get_user.return_value = user

        req = make_mocked_request("GET", "/sub/open/valid_token_xyz", match_info={"token": "valid_token_xyz"})
        response = await subscription_open_handler(req)

        self.assertEqual(response.headers["Cache-Control"], "no-store, no-cache, must-revalidate, max-age=0")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow, noarchive")
        self.assertIn("default-src 'none'", response.headers["Content-Security-Policy"])

    @patch("bot.handlers.subscription_feed.SubscriptionService.check_vpn_access")
    @patch("bot.handlers.subscription_feed.SubscriptionTokenService.get_user_by_token")
    @patch("bot.handlers.subscription_feed.session_scope")
    async def test_open_endpoint_inactive_subscription(
        self, mock_session_scope, mock_get_user, mock_check_access
    ):
        mock_session = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = session
        session.begin_nested.return_value.__aexit__.return_value = None
        mock_session_scope.return_value.__aenter__.return_value = mock_session

        user = User(id=77, telegram_id=888, subscription_token="valid_token_xyz")
        mock_get_user.return_value = user
        mock_check_access.return_value = False

        req = make_mocked_request("GET", "/sub/open/valid_token_xyz", match_info={"token": "valid_token_xyz"})
        response = await subscription_open_handler(req)

        self.assertEqual(response.status, 200)
        self.assertIn("Подписка не активна", response.text)
        self.assertIn("Связаться с поддержкой", response.text)
        self.assertIn("https://t.me/test_support", response.text)
        self.assertNotIn("incy://import/", response.text)

    @patch("bot.handlers.subscription_feed.SubscriptionFeedService.build_feed")
    @patch("bot.handlers.subscription_feed.SubscriptionTokenService.get_user_by_token")
    @patch("bot.handlers.subscription_feed.session_scope")
    async def test_feed_and_open_endpoints_share_rate_limit_budget(
        self, mock_session_scope, mock_get_user, mock_build_feed
    ):
        mock_session = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = session
        session.begin_nested.return_value.__aexit__.return_value = None
        mock_session_scope.return_value.__aenter__.return_value = mock_session
        user = User(
            id=77,
            telegram_id=888,
            subscription_token="valid_token_xyz",
            subscription_end=now_utc() + timedelta(days=30),
        )
        mock_get_user.return_value = user
        mock_build_feed.return_value = (200, {"Content-Type": "text/plain"}, "test-feed")

        # Burst is 10. Send 6 requests to feed endpoint
        for _ in range(6):
            req = make_mocked_request(
                "GET",
                "/sub/valid_token_xyz",
                match_info={"token": "valid_token_xyz"},
            )
            req._transport_peername = ("198.51.100.10", 12345)
            resp = await subscription_feed_handler(req)
            self.assertEqual(resp.status, 200)

        # Send 4 requests to open endpoint (total 10 requests consuming the entire burst)
        for _ in range(4):
            req = make_mocked_request(
                "GET",
                "/sub/open/valid_token_xyz",
                match_info={"token": "valid_token_xyz"},
            )
            req._transport_peername = ("198.51.100.10", 12345)
            resp = await subscription_open_handler(req)
            self.assertEqual(resp.status, 200)

        # 11th request to feed endpoint MUST be throttled (429)
        req_11 = make_mocked_request(
            "GET",
            "/sub/valid_token_xyz",
            match_info={"token": "valid_token_xyz"},
        )
        req_11._transport_peername = ("198.51.100.10", 12345)
        resp_11 = await subscription_feed_handler(req_11)
        self.assertEqual(resp_11.status, 429)
        self.assertEqual(resp_11.text, "Too Many Requests")

        # 12th request to open endpoint MUST also be throttled (429 with HTML)
        req_12 = make_mocked_request(
            "GET",
            "/sub/open/valid_token_xyz",
            match_info={"token": "valid_token_xyz"},
        )
        req_12._transport_peername = ("198.51.100.10", 12345)
        resp_12 = await subscription_open_handler(req_12)
        self.assertEqual(resp_12.status, 429)
        self.assertIn("Слишком много запросов", resp_12.text)

        # A different IP address still has its full independent budget
        req_other = make_mocked_request(
            "GET",
            "/sub/valid_token_xyz",
            match_info={"token": "valid_token_xyz"},
        )
        req_other._transport_peername = ("198.51.100.20", 12345)
        resp_other = await subscription_feed_handler(req_other)
        self.assertEqual(resp_other.status, 200)

    def test_subscription_rate_limit_time_progression_and_refill(self):
        # Direct temporal verification of subscription_feed_rate_limiter token dynamics
        ip = "198.51.100.99"
        t0 = 1000.0

        # Burst capacity is 10
        for _ in range(10):
            allowed, retry = subscription_feed_rate_limiter.check(ip, now=t0)
            self.assertTrue(allowed)
            self.assertEqual(retry, 0)

        # 11th request at t0 is denied; retry_after indicates 2 seconds needed for 1 full token (30 req/min = 0.5 tok/s)
        allowed, retry = subscription_feed_rate_limiter.check(ip, now=t0)
        self.assertFalse(allowed)
        self.assertEqual(retry, 2)

        # After 1.0s, only 0.5 token refilled (< 1.0), still denied
        allowed, retry = subscription_feed_rate_limiter.check(ip, now=t0 + 1.0)
        self.assertFalse(allowed)
        self.assertEqual(retry, 1)

        # After 2.0s, 1 full token refilled -> allowed
        allowed, retry = subscription_feed_rate_limiter.check(ip, now=t0 + 2.0)
        self.assertTrue(allowed)
        self.assertEqual(retry, 0)

        # Immediate follow-up is denied again
        allowed, retry = subscription_feed_rate_limiter.check(ip, now=t0 + 2.0)
        self.assertFalse(allowed)
        self.assertEqual(retry, 2)

        # After 60.0s of idle time, bucket refills up to burst ceiling (10)
        allowed, _ = subscription_feed_rate_limiter.check(ip, now=t0 + 62.0)
        self.assertTrue(allowed)
        # 9 more allowed immediately
        for _ in range(9):
            allowed, _ = subscription_feed_rate_limiter.check(ip, now=t0 + 62.0)
            self.assertTrue(allowed)
        # 11th is denied
        allowed, _ = subscription_feed_rate_limiter.check(ip, now=t0 + 62.0)
        self.assertFalse(allowed)


if __name__ == "__main__":
    unittest.main()
