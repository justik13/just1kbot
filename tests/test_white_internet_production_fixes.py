"""Tests verifying production fixes for White Internet and observability.

1. No menu duplication in show_white_internet_menu on unchanged traffic refresh.
2. Account balance purchase notification strictly ignores White Internet quotes.
3. WhiteInternetService._new_quote marks purchase_notified_at.
4. INCY store URLs are valid and correct in texts.
5. /sub/wl/ping endpoint returns 200 OK 'pong'.
6. Node monitor synthetic probe flags HTTP 502 on /sub/wl/ping as unhealthy.
"""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramBadRequest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from bot import texts
from bot.handlers.white_internet import show_white_internet_menu
from bot.handlers.white_internet_web import setup_white_internet_web_routes
from config.constants import (
    WHITE_INTERNET_SERVICE_TYPE,
    XRAY_PROTOCOL,
)
from config.enums import (
    ServerHealthState,
    ServerLifecycleStatus,
    TariffQuoteOperation,
    WhiteInternetStatus,
)
from database.models import Server, User, WhiteInternetSubscription
from services.white_internet_service import WhiteInternetService
from services.workers.account_balance import process_balance_purchase_notifications
from services.workers.node_monitor import check_node_resources_and_alerts, clear_monitor_states
from utils.datetime_helpers import now_utc


class TestWhiteInternetProductionFixes(unittest.IsolatedAsyncioTestCase):
    async def test_show_white_internet_menu_message_not_modified_shows_toast(self):
        """When message is not modified, do NOT call query.message.answer, show toast instead."""
        query = MagicMock()
        query.from_user.id = 123456
        query.answer = AsyncMock()
        query.message.edit_text = AsyncMock(
            side_effect=TelegramBadRequest(
                method=MagicMock(),
                message="Bad Request: message is not modified: specified new message content and reply markup are exactly the same",
            )
        )
        query.message.answer = AsyncMock()

        session = AsyncMock()
        user = User(id=1, telegram_id=123456)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=1,
            status=WhiteInternetStatus.ACTIVE,
            traffic_limit_bytes=10 * 1024**3,
            expires_at=now_utc(),
            token="abcdef1234567890abcdef",
        )

        with patch("bot.handlers.white_internet.get_user_by_telegram_id", return_value=user), \
             patch("bot.handlers.white_internet.white_internet_repo.get_subscription_by_user_id", return_value=sub), \
             patch("bot.handlers.white_internet.white_internet_repo.get_available_quota_bytes", return_value=10 * 1024**3), \
             patch("bot.handlers.white_internet._resolve_subscription_domain", return_value="cdn.just1k.best"):

            await show_white_internet_menu(query, session)

            # query.message.answer MUST NOT be called (no new duplicate message)
            query.message.answer.assert_not_called()
            # query.answer must be called with traffic up to date alert
            query.answer.assert_called_once_with(texts.WL_ALERT_TRAFFIC_UP_TO_DATE, show_alert=False)

    async def test_account_balance_purchase_notification_ignores_white_internet_quotes(self):
        """process_balance_purchase_notifications only processes AWG quotes, ignoring white_internet."""
        bot = MagicMock()
        bot.send_message = AsyncMock()

        executed_statements = []

        class MockResult:
            def all(self):
                return []

        async def mock_execute(stmt):
            compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            executed_statements.append(compiled)
            return MockResult()

        session_mock = AsyncMock()
        session_mock.execute = mock_execute

        class SessionContext:
            async def __aenter__(self):
                return session_mock

            async def __aexit__(self, exc_type, exc, tb):
                pass

        with patch("services.workers.account_balance.session_scope", return_value=SessionContext()):
            await process_balance_purchase_notifications(bot)

            self.assertTrue(len(executed_statements) > 0)
            query_sql = executed_statements[0]
            # Ensure query explicitly checks tariff_quotes.service_type = 'awg'
            self.assertIn("tariff_quotes.service_type =", query_sql)
            self.assertIn("'awg'", query_sql)

    def test_white_internet_new_quote_sets_purchase_notified_at(self):
        """WhiteInternetService._new_quote stamps purchase_notified_at to prevent bogus balance alerts."""
        quote = WhiteInternetService._new_quote(
            user_id=1,
            operation_type=TariffQuoteOperation.PURCHASE,
            target_version_id=1,
            amount_due=Decimal("0.00"),
            expires_at=now_utc(),
        )
        self.assertEqual(quote.service_type, WHITE_INTERNET_SERVICE_TYPE)
        self.assertIsNotNone(quote.purchase_notified_at)

    def test_white_internet_texts_have_correct_incy_urls(self):
        """User instructions contain official App Store, Google Play, and GitHub APK URLs."""
        text = texts.WL_INCY_INSTRUCTIONS_TEXT
        # Official App Store ID
        self.assertIn("id6756943388", text)
        self.assertNotIn("id6504289871", text)

        # Official Google Play package
        self.assertIn("llc.itdev.incy", text)
        self.assertNotIn("app.incy.client", text)

        # Direct GitHub APK link
        self.assertIn("github.com/INCY-DEV/incy-platforms/releases", text)


class TestWhiteInternetPingWebRoute(AioHTTPTestCase):
    async def get_application(self):
        app = web.Application()
        setup_white_internet_web_routes(app)
        return app

    async def test_sub_wl_ping_returns_200_pong(self):
        """GET /sub/wl/ping must return HTTP 200 with text 'pong'."""
        resp = await self.client.get("/sub/wl/ping")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertEqual(text, "pong")
        self.assertIn("no-store", resp.headers.get("Cache-Control", ""))


class TestNodeMonitorSyntheticProbe(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        clear_monitor_states()

    def tearDown(self):
        clear_monitor_states()

    async def test_node_monitor_ingress_probe_decoupled_from_core_health(self):
        """When origin Nginx returns 502 on /sub/wl/ping:
        1. Core health remains healthy (ONLINE, 0 consecutive fails, not auto-disabled).
        2. Ingress alert (ALERT_INGRESS_PROBLEM) is sent to admins.
        3. When /sub/wl/ping recovers with 200 OK, ALERT_INGRESS_RESTORED is sent.
        """
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=True)

        server = Server(
            id=10,
            name="Origin Moscow",
            protocol=XRAY_PROTOCOL,
            api_url="https://194.113.106.134:8444",
            api_key="secret-key",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            capabilities=["xray_origin"],
            extra_data={
                "cdn_domain": "cdn.just1k.best",
                "domain": "origin.just1k.best",
                "relays": [{"code": "de", "ip": "217.60.183.229"}],
            },
        )

        class MockProbeResponse:
            def __init__(self, status):
                self.status = status

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

        class MockSession:
            def __init__(self, status=502):
                self.status = status

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

            def get(self, url, **kwargs):
                return MockProbeResponse(status=self.status)

        class MockXrayClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

            async def check_health(self, api_url, api_key):
                return True, 1, {"status": "ok"}

        session_mock = AsyncMock()
        mock_settings = MagicMock()
        mock_settings.ADMIN_IDS = [999999]

        with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
             patch("services.workers.node_monitor.session_scope") as mock_scope, \
             patch("services.xray_node_client.XrayNodeClient", MockXrayClient), \
             patch("services.workers.node_monitor.aiohttp.ClientSession", lambda **kw: MockSession(status=502)), \
             patch("services.workers.node_monitor.update_server_xray_epoch_cas", new_callable=AsyncMock, return_value=(True, server)), \
             patch("services.workers.node_monitor.update_server_health_snapshot", new_callable=AsyncMock) as mock_snap, \
             patch("services.workers.node_monitor.get_server_by_id", new_callable=AsyncMock, return_value=server), \
             patch("services.workers.node_monitor.update_server", new_callable=AsyncMock), \
             patch("services.workers.node_monitor.get_settings", return_value=mock_settings):

            mock_scope.return_value.__aenter__.return_value = session_mock
            mock_snap.return_value = (server, True)

            # --- Check 1: Ingress fails with 502 on cdn_domain ---
            await check_node_resources_and_alerts(bot)

            # 1. Core health MUST stay ONLINE and consecutive_fails MUST remain 0!
            mock_snap.assert_called_once()
            called_kwargs = mock_snap.call_args[1]
            self.assertEqual(called_kwargs["consecutive_fails"], 0)
            self.assertEqual(called_kwargs["health_state"], ServerHealthState.ONLINE)

            # 2. Ingress alert MUST be sent to admin with cdn_domain priority
            bot.send_message.assert_called_once()
            call_args = bot.send_message.call_args[1]
            self.assertEqual(call_args["chat_id"], 999999)
            self.assertIn("cdn.just1k.best", call_args["text"])
            self.assertIn("502", call_args["text"])

        # Reset bot mock for Check 2
        bot.send_message.reset_mock()

        # --- Check 2: Ingress recovers with 200 OK ---
        with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
             patch("services.workers.node_monitor.session_scope") as mock_scope, \
             patch("services.xray_node_client.XrayNodeClient", MockXrayClient), \
             patch("services.workers.node_monitor.aiohttp.ClientSession", lambda **kw: MockSession(status=200)), \
             patch("services.workers.node_monitor.update_server_xray_epoch_cas", new_callable=AsyncMock, return_value=(True, server)), \
             patch("services.workers.node_monitor.update_server_health_snapshot", new_callable=AsyncMock) as mock_snap, \
             patch("services.workers.node_monitor.get_server_by_id", new_callable=AsyncMock, return_value=server), \
             patch("services.workers.node_monitor.update_server", new_callable=AsyncMock), \
             patch("services.workers.node_monitor.get_settings", return_value=mock_settings):

            mock_scope.return_value.__aenter__.return_value = session_mock
            mock_snap.return_value = (server, True)

            await check_node_resources_and_alerts(bot)

            # Core health remains ONLINE
            mock_snap.assert_called_once()
            called_kwargs = mock_snap.call_args[1]
            self.assertEqual(called_kwargs["consecutive_fails"], 0)
            self.assertEqual(called_kwargs["health_state"], ServerHealthState.ONLINE)

            # Restored ingress alert sent to admin
            bot.send_message.assert_called_once()
            call_args = bot.send_message.call_args[1]
            self.assertEqual(call_args["chat_id"], 999999)
            self.assertIn("cdn.just1k.best", call_args["text"])
            self.assertIn("восстановлено", call_args["text"])

    async def test_node_monitor_ingress_probe_flags_404_non_200_as_failure(self):
        """Any non-200 HTTP response (including 404 Not Found) triggers ALERT_INGRESS_PROBLEM."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=True)

        server = Server(
            id=11,
            name="Origin CDN Check",
            protocol=XRAY_PROTOCOL,
            api_url="https://194.113.106.134:8444",
            api_key="secret-key",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            capabilities=["xray_origin"],
            extra_data={"cdn_domain": "cdn.just1k.best"},
        )

        class MockProbeResponse:
            def __init__(self, status):
                self.status = status

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

        class MockXrayClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

            async def check_health(self, api_url, api_key):
                return True, 1, {"status": "ok"}

        session_mock = AsyncMock()
        mock_settings = MagicMock()
        mock_settings.ADMIN_IDS = [999999]

        with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
             patch("services.workers.node_monitor.session_scope") as mock_scope, \
             patch("services.xray_node_client.XrayNodeClient", MockXrayClient), \
             patch("services.workers.node_monitor.aiohttp.ClientSession") as mock_http, \
             patch("services.workers.node_monitor.update_server_xray_epoch_cas", new_callable=AsyncMock, return_value=(True, server)), \
             patch("services.workers.node_monitor.update_server_health_snapshot", new_callable=AsyncMock) as mock_snap, \
             patch("services.workers.node_monitor.get_server_by_id", new_callable=AsyncMock, return_value=server), \
             patch("services.workers.node_monitor.update_server", new_callable=AsyncMock), \
             patch("services.workers.node_monitor.get_settings", return_value=mock_settings):

            mock_sess = MagicMock()
            mock_sess.get.return_value = MockProbeResponse(status=404)
            mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
            mock_sess.__aexit__ = AsyncMock(return_value=None)
            mock_http.return_value = mock_sess

            mock_scope.return_value.__aenter__.return_value = session_mock
            mock_snap.return_value = (server, True)

            await check_node_resources_and_alerts(bot)

            # Core health stays ONLINE
            self.assertEqual(mock_snap.call_args[1]["health_state"], ServerHealthState.ONLINE)
            self.assertEqual(mock_snap.call_args[1]["consecutive_fails"], 0)

            # Admin receives alert with 404
            bot.send_message.assert_called_once()
            call_args = bot.send_message.call_args[1]
            self.assertIn("404", call_args["text"])
            self.assertIn("cdn.just1k.best", call_args["text"])


if __name__ == "__main__":
    unittest.main()

