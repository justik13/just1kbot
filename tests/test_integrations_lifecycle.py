"""Unit and integration tests for modular integrations architecture and lifecycle."""

import os
import unittest
from unittest.mock import patch

from aiohttp import web

from config.settings import get_settings
from integrations import (
    ALL_INTEGRATIONS,
    get_all_bot_routers,
    register_all_web_routes,
)
from integrations.base import BaseIntegration


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
            "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/db",
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

    def test_registry_contains_valid_integrations(self):
        for integration_cls in ALL_INTEGRATIONS:
            self.assertTrue(issubclass(integration_cls, BaseIntegration))

    def test_get_all_bot_routers_empty_or_valid(self):
        routers = get_all_bot_routers()
        self.assertIsInstance(routers, list)

    def test_non_critical_integration_failure_does_not_abort_startup(self):
        class DummyNonCriticalIntegration(BaseIntegration):
            name = "dummy_optional"
            is_critical = False

            @classmethod
            def is_enabled(cls) -> bool:
                return True

            @classmethod
            def register_web_routes(cls, app: web.Application) -> None:
                raise RuntimeError("Optional plugin failed")

            @classmethod
            def get_bot_router(cls):
                raise ValueError("Optional router failed")

        with patch("integrations.ALL_INTEGRATIONS", (DummyNonCriticalIntegration,)):
            app = web.Application()
            # Should NOT raise RuntimeError for non-critical integration
            register_all_web_routes(app)
            routers = get_all_bot_routers()
            self.assertEqual(len(routers), 0)

    def test_optional_integration_failure_rolls_back_partial_routes(self):
        async def dummy_handler(request):
            return web.Response(text="dummy")

        class HalfFailingOptionalIntegration(BaseIntegration):
            name = "half_failing"
            is_critical = False

            @classmethod
            def is_enabled(cls) -> bool:
                return True

            @classmethod
            def register_web_routes(cls, app: web.Application) -> None:
                # Adds a route, then crashes
                app.router.add_get("/half_failing/route", dummy_handler)
                raise RuntimeError("Failed after adding one route")

        app = web.Application()
        app.router.add_get("/preexisting/route", dummy_handler)
        self.assertEqual(len(app.router.resources()), 1)
        self.assertEqual(len(app.router.routes()), 2)

        with patch("integrations.ALL_INTEGRATIONS", (HalfFailingOptionalIntegration,)):
            # Does not crash startup
            register_all_web_routes(app)
            # Partial route /half_failing/route was rolled back
            self.assertEqual(len(app.router.resources()), 1)
            self.assertEqual(len(app.router.routes()), 2)
            self.assertEqual(list(app.router.routes())[0].resource.canonical, "/preexisting/route")

    def test_is_enabled_exception_handling(self):
        class BrokenEnabledOptional(BaseIntegration):
            name = "broken_optional"
            is_critical = False

            @classmethod
            def is_enabled(cls) -> bool:
                raise ValueError("Bad config parse")

        class BrokenEnabledCritical(BaseIntegration):
            name = "broken_critical"
            is_critical = True

            @classmethod
            def is_enabled(cls) -> bool:
                raise ValueError("Fatal config missing")

        with patch("integrations.ALL_INTEGRATIONS", (BrokenEnabledOptional,)):
            app = web.Application()
            register_all_web_routes(app)
            routers = get_all_bot_routers()
            self.assertEqual(len(routers), 0)

        with patch("integrations.ALL_INTEGRATIONS", (BrokenEnabledCritical,)):
            app = web.Application()
            with self.assertRaises(RuntimeError) as ctx:
                register_all_web_routes(app)
            self.assertIn("Critical integration 'broken_critical' failed during is_enabled check", str(ctx.exception))

    def test_register_all_web_routes_fails_fast_on_critical_error(self):
        class BrokenCriticalIntegration(BaseIntegration):
            name = "broken_critical_web"
            is_critical = True

            @classmethod
            def is_enabled(cls) -> bool:
                return True

            @classmethod
            def register_web_routes(cls, app: web.Application) -> None:
                raise RuntimeError("Web route bind error")

        app = web.Application()
        with patch("integrations.ALL_INTEGRATIONS", (BrokenCriticalIntegration,)):
            with self.assertRaises(RuntimeError) as ctx:
                register_all_web_routes(app)
            self.assertIn("Failed to register web routes for critical integration 'broken_critical_web'", str(ctx.exception))

    def test_get_all_bot_routers_fails_fast_on_critical_error(self):
        class BrokenCriticalRouterIntegration(BaseIntegration):
            name = "broken_critical_router"
            is_critical = True

            @classmethod
            def is_enabled(cls) -> bool:
                return True

            @classmethod
            def get_bot_router(cls):
                raise ValueError("Router build error")

        with patch("integrations.ALL_INTEGRATIONS", (BrokenCriticalRouterIntegration,)):
            with self.assertRaises(RuntimeError) as ctx:
                get_all_bot_routers()
            self.assertIn("Failed to get bot router for critical integration 'broken_critical_router'", str(ctx.exception))

