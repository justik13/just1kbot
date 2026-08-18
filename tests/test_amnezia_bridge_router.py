import os
import unittest
from unittest.mock import patch

from aiohttp import web

from bot.handlers.amnezia_web_templates import AMNEZIA_SECURITY_HEADERS
from bot.handlers.webhook import setup_webhook_routes


class AmneziaBridgeRouterTests(unittest.TestCase):
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

    def test_routes_registered_correctly(self):
        app = web.Application()
        setup_webhook_routes(app)

        routes = [r.resource.canonical for r in app.router.routes() if r.method == "GET"]
        self.assertIn("/amnezia/open/{profile_id}", routes)
        self.assertIn("/sub/open/{token}", routes)
        self.assertIn("/sub/{token}", routes)
        self.assertIn("/health", routes)

    def test_security_headers_content(self):
        self.assertEqual(AMNEZIA_SECURITY_HEADERS["Content-Type"], "text/html; charset=utf-8")
        self.assertEqual(AMNEZIA_SECURITY_HEADERS["X-Frame-Options"], "DENY")
        self.assertEqual(AMNEZIA_SECURITY_HEADERS["X-Content-Type-Options"], "nosniff")
        self.assertIn("default-src 'none'", AMNEZIA_SECURITY_HEADERS["Content-Security-Policy"])
        self.assertIn("connect-src 'none'", AMNEZIA_SECURITY_HEADERS["Content-Security-Policy"])
        self.assertIn("img-src 'none'", AMNEZIA_SECURITY_HEADERS["Content-Security-Policy"])


if __name__ == "__main__":
    unittest.main()
