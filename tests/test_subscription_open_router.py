import os
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from bot.handlers.webhook import setup_webhook_routes
from database.models import User
from utils.datetime_helpers import now_utc


class SubscriptionOpenRouterIntegrationTests(AioHTTPTestCase):
    @classmethod
    def setUpClass(cls):
        cls.env_patcher = patch.dict(
            os.environ,
            {
                "BOT_TOKEN": "123:test",
                "REDIS_URL": "redis://localhost:6379/1",
                "REDIS_PASSWORD": "test",
                "ADMIN_IDS": "[123456789]",
                "SUPPORT_USERNAME": "support_team",
                "DOMAIN": "vpn.example.com",
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

    async def get_application(self):
        app = web.Application()
        setup_webhook_routes(app)
        return app

    @patch("bot.handlers.subscription_feed.SubscriptionTokenService.get_user_by_token")
    @patch("bot.handlers.subscription_feed.session_scope")
    async def test_sub_open_router_e2e_active_user(
        self, mock_session_scope, mock_get_user
    ):
        mock_session = AsyncMock()
        mock_session_scope.return_value.__aenter__.return_value = mock_session

        token = "secure_token_abc_123"
        user = User(
            id=42,
            telegram_id=999,
            subscription_token=token,
            subscription_end=now_utc() + timedelta(days=15),
        )
        mock_get_user.return_value = user

        # 1. Test canonical /sub/open/{token}
        resp = await self.client.request("GET", f"/sub/open/{token}")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers.get("Content-Type"), "text/html; charset=utf-8")
        self.assertEqual(resp.headers.get("Cache-Control"), "no-store, no-cache, must-revalidate, max-age=0")
        self.assertEqual(resp.headers.get("Referrer-Policy"), "no-referrer")
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(resp.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(resp.headers.get("X-Robots-Tag"), "noindex, nofollow, noarchive")
        self.assertIn("default-src 'none'", resp.headers.get("Content-Security-Policy", ""))

        text = await resp.text()
        expected_sub_url = f"https://vpn.example.com/sub/{token}"
        expected_deep_link = f"incy://import/{expected_sub_url}"

        # Ensure deep link is present in href and script
        self.assertIn(f'href="{expected_deep_link}"', text)
        self.assertIn(expected_deep_link, text)
        self.assertIn("📱 Открыть в приложении INCY", text)
        self.assertIn("📋 Скопировать ссылку на подписку", text)
        self.assertIn("App Store", text)
        self.assertIn("Google Play", text)

        # 2. Test synonym route /subscription/open/{token}
        resp_synonym = await self.client.request("GET", f"/subscription/open/{token}")
        self.assertEqual(resp_synonym.status, 200)
        text_synonym = await resp_synonym.text()
        self.assertIn(f'href="{expected_deep_link}"', text_synonym)

    @patch("bot.handlers.subscription_feed.SubscriptionService.check_vpn_access")
    @patch("bot.handlers.subscription_feed.SubscriptionTokenService.get_user_by_token")
    @patch("bot.handlers.subscription_feed.session_scope")
    async def test_sub_open_router_e2e_inactive_user(
        self, mock_session_scope, mock_get_user, mock_check_access
    ):
        mock_session = AsyncMock()
        mock_session_scope.return_value.__aenter__.return_value = mock_session

        token = "expired_token_xyz"
        user = User(
            id=42,
            telegram_id=999,
            subscription_token=token,
            subscription_end=now_utc() - timedelta(days=1),
        )
        mock_get_user.return_value = user
        mock_check_access.return_value = False

        resp = await self.client.request("GET", f"/sub/open/{token}")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers.get("Cache-Control"), "no-store, no-cache, must-revalidate, max-age=0")

        text = await resp.text()
        self.assertIn("Подписка не активна", text)
        self.assertIn("💬 Связаться с поддержкой", text)
        self.assertIn("https://t.me/support_team", text)
        self.assertNotIn("incy://import/", text)

    @patch("bot.handlers.subscription_feed.SubscriptionTokenService.get_user_by_token")
    @patch("bot.handlers.subscription_feed.session_scope")
    async def test_sub_open_router_404_not_found(
        self, mock_session_scope, mock_get_user
    ):
        mock_session = AsyncMock()
        mock_session_scope.return_value.__aenter__.return_value = mock_session
        mock_get_user.return_value = None

        resp = await self.client.request("GET", "/sub/open/nonexistent_token")
        self.assertEqual(resp.status, 404)
        self.assertEqual(resp.headers.get("Cache-Control"), "no-store, no-cache, must-revalidate, max-age=0")
        text = await resp.text()
        self.assertIn("404", text)


if __name__ == "__main__":
    unittest.main()
