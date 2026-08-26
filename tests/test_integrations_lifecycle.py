"""Unit and integration tests for modular integrations architecture and lifecycle."""

import os
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from config.settings import get_settings
from integrations import (
    ALL_INTEGRATIONS,
    get_all_bot_routers,
    register_all_web_routes,
)
from integrations.amnezia_bridge import (
    AmneziaBridgeIntegration,
    amnezia_bridge_handler,
)
from integrations.base import BaseIntegration
from integrations.incy import (
    IncyIntegration,
    subscription_feed_handler,
    subscription_open_handler,
)


class IntegrationsLifecycleTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_env = {
            "BOT_TOKEN": "123:test",
            "REDIS_URL": "redis://localhost:6379/1",
            "REDIS_PASSWORD": "test",
            "ADMIN_IDS": "[123456789]",
            "SUPPORT_USERNAME": "test_support",
            "DOMAIN": "test.domain.com",
            "SSL_EMAIL": "test@domain.com",
            "YOOKASSA_SHOP_ID": "123456",
            "YOOKASSA_SECRET_KEY": "test_secret",
            "YOOKASSA_RETURN_URL": "https://t.me/{bot_username}",
            "YOOKASSA_WEBHOOK_PORT": "8080",
            "DB_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
            "AMNEZIA_BRIDGE_HMAC_SECRET": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/db",
            "INCY_SUBSCRIPTION_ENABLED": "true",
        }
        cls.env_patcher = patch.dict(os.environ, cls.base_env)
        cls.env_patcher.start()
        get_settings.cache_clear()

    @classmethod
    def tearDownClass(cls):
        get_settings.cache_clear()
        cls.env_patcher.stop()

    def setUp(self):
        get_settings.cache_clear()

    def tearDown(self):
        get_settings.cache_clear()

    def test_registry_contains_all_integrations(self):
        self.assertIn(IncyIntegration, ALL_INTEGRATIONS)
        self.assertIn(AmneziaBridgeIntegration, ALL_INTEGRATIONS)
        for integration_cls in ALL_INTEGRATIONS:
            self.assertTrue(issubclass(integration_cls, BaseIntegration))

    def test_incy_enabled_and_routes_registered(self):
        with patch.dict(os.environ, {"INCY_SUBSCRIPTION_ENABLED": "true", "DOMAIN": "test.domain.com"}):
            get_settings.cache_clear()
            self.assertTrue(IncyIntegration.is_enabled())

            app = web.Application()
            IncyIntegration.register_web_routes(app)
            registered_paths = [r.resource.canonical for r in app.router.routes()]
            self.assertIn("/sub/{token}", registered_paths)
            self.assertIn("/sub/open/{token}", registered_paths)
            self.assertIn("/subscription/{token}", registered_paths)
            self.assertIn("/subscription/open/{token}", registered_paths)

    def test_incy_disabled_routes_not_registered(self):
        with patch.dict(os.environ, {"INCY_SUBSCRIPTION_ENABLED": "false"}):
            get_settings.cache_clear()
            self.assertFalse(IncyIntegration.is_enabled())

            app = web.Application()
            IncyIntegration.register_web_routes(app)
            registered_paths = [r.resource.canonical for r in app.router.routes()]
            self.assertEqual(len(registered_paths), 0)

    async def test_incy_handlers_fail_closed_when_disabled(self):
        with patch.dict(os.environ, {"INCY_SUBSCRIPTION_ENABLED": "false"}):
            get_settings.cache_clear()
            req = make_mocked_request("GET", "/sub/test_token", match_info={"token": "test_token"})
            resp = await subscription_feed_handler(req)
            self.assertEqual(resp.status, 404)

            req_open = make_mocked_request("GET", "/sub/open/test_token", match_info={"token": "test_token"})
            resp_open = await subscription_open_handler(req_open)
            self.assertEqual(resp_open.status, 404)

    def test_amnezia_bridge_enabled_and_routes_registered(self):
        with patch.dict(os.environ, {
            "AMNEZIA_BRIDGE_HMAC_SECRET": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "DOMAIN": "test.domain.com",
        }):
            get_settings.cache_clear()
            self.assertTrue(AmneziaBridgeIntegration.is_enabled())

            app = web.Application()
            AmneziaBridgeIntegration.register_web_routes(app)
            registered_paths = [r.resource.canonical for r in app.router.routes()]
            self.assertIn("/amnezia/open/{profile_id}", registered_paths)

    def test_amnezia_bridge_disabled_routes_not_registered(self):
        with patch.dict(os.environ, {"AMNEZIA_BRIDGE_HMAC_SECRET": ""}):
            get_settings.cache_clear()
            self.assertFalse(AmneziaBridgeIntegration.is_enabled())

            app = web.Application()
            AmneziaBridgeIntegration.register_web_routes(app)
            registered_paths = [r.resource.canonical for r in app.router.routes()]
            self.assertEqual(len(registered_paths), 0)

    async def test_amnezia_bridge_handler_fail_closed_when_disabled(self):
        with patch.dict(os.environ, {"AMNEZIA_BRIDGE_HMAC_SECRET": ""}):
            get_settings.cache_clear()
            req = make_mocked_request("GET", "/amnezia/open/1", match_info={"profile_id": "1"})
            resp = await amnezia_bridge_handler(req)
            self.assertEqual(resp.status, 404)

    def test_register_all_web_routes_selective(self):
        # INCY disabled, Amnezia Bridge enabled
        with patch.dict(os.environ, {
            "INCY_SUBSCRIPTION_ENABLED": "false",
            "AMNEZIA_BRIDGE_HMAC_SECRET": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "DOMAIN": "test.domain.com",
        }):
            get_settings.cache_clear()
            app = web.Application()
            register_all_web_routes(app)
            registered_paths = [r.resource.canonical for r in app.router.routes()]

            self.assertIn("/amnezia/open/{profile_id}", registered_paths)
            self.assertNotIn("/sub/{token}", registered_paths)
            self.assertNotIn("/sub/open/{token}", registered_paths)

    def test_get_all_bot_routers(self):
        routers = get_all_bot_routers()
        self.assertIsInstance(routers, list)

    def test_register_all_web_routes_fails_fast_on_error(self):
        app = web.Application()
        with patch.object(IncyIntegration, "is_enabled", return_value=True), \
             patch.object(IncyIntegration, "register_web_routes", side_effect=RuntimeError("Web route bind error")):
            with self.assertRaises(RuntimeError) as ctx:
                register_all_web_routes(app)
            self.assertIn("Failed to register web routes for critical integration 'incy'", str(ctx.exception))

    def test_get_all_bot_routers_fails_fast_on_error(self):
        with patch.object(IncyIntegration, "is_enabled", return_value=True), \
             patch.object(IncyIntegration, "get_bot_router", side_effect=ValueError("Router build error")):
            with self.assertRaises(RuntimeError) as ctx:
                get_all_bot_routers()
            self.assertIn("Failed to get bot router for critical integration 'incy'", str(ctx.exception))

    def test_get_all_bot_routers_enabled_vs_disabled(self):
        with patch.dict(os.environ, {"INCY_SUBSCRIPTION_ENABLED": "true", "DOMAIN": "test.domain.com"}):
            get_settings.cache_clear()
            routers = get_all_bot_routers()
            self.assertEqual(len(routers), 1)

        with patch.dict(os.environ, {"INCY_SUBSCRIPTION_ENABLED": "false"}):
            get_settings.cache_clear()
            routers = get_all_bot_routers()
            self.assertEqual(len(routers), 0)

    async def test_setup_bot_dynamic_integration_inclusion(self):
        from unittest.mock import AsyncMock
        from bot.main import setup_bot
        from integrations.incy import incy_router

        with patch.dict(os.environ, {"INCY_SUBSCRIPTION_ENABLED": "true", "DOMAIN": "test.domain.com"}), \
             patch("bot.main.setup_bot_commands", new_callable=AsyncMock):
            get_settings.cache_clear()
            _, dp = await setup_bot()
            self.assertIn(incy_router, dp.sub_routers)

        with patch.dict(os.environ, {"INCY_SUBSCRIPTION_ENABLED": "false"}), \
             patch("bot.main.setup_bot_commands", new_callable=AsyncMock):
            get_settings.cache_clear()
            _, dp = await setup_bot()
            self.assertNotIn(incy_router, dp.sub_routers)
