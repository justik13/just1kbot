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
    @patch("bot.handlers.subscription_feed.SubscriptionTokenService.get_user_by_token")
    @patch("bot.handlers.subscription_feed.SubscriptionFeedService.build_feed")
    @patch("bot.handlers.subscription_feed.session_scope")
    async def test_endpoint_valid_token(
        self, mock_session_scope, mock_build_feed, mock_get_user
    ):
        mock_session = AsyncMock()
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

    @patch("bot.handlers.subscription_feed.SubscriptionTokenService.get_user_by_token")
    @patch("bot.handlers.subscription_feed.session_scope")
    async def test_open_endpoint_security_headers(
        self, mock_session_scope, mock_get_user
    ):
        mock_session = AsyncMock()
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


if __name__ == "__main__":
    unittest.main()
