"""Tests verifying production fixes for White Internet and observability.

1. No menu duplication in show_white_internet_menu on unchanged traffic refresh.
2. Account balance purchase notification strictly ignores White Internet quotes.
3. WhiteInternetService._new_quote marks purchase_notified_at.
4. INCY store URLs are valid and correct in texts.
5. /sub/wl/ping endpoint returns 200 OK 'pong'.
6. Node monitor synthetic probe flags HTTP 502 on /sub/wl/ping as unhealthy.
"""

from __future__ import annotations

import ssl
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
from services.workers.node_monitor import (
    ServerMonitorState,
    check_node_resources_and_alerts,
    clear_monitor_states,
)
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

    async def test_node_monitor_ingress_probe_redirect_301_fails(self):
        """Redirect 301 is not followed and is reported as failure with allow_redirects=False."""
        clear_monitor_states()
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=True)

        server = Server(
            id=12,
            name="Origin 301 Check",
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
            mock_sess.get.return_value = MockProbeResponse(status=301)
            mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
            mock_sess.__aexit__ = AsyncMock(return_value=None)
            mock_http.return_value = mock_sess

            mock_scope.return_value.__aenter__.return_value = session_mock
            mock_snap.return_value = (server, True)

            await check_node_resources_and_alerts(bot)

            # Core health stays ONLINE
            self.assertEqual(mock_snap.call_args[1]["health_state"], ServerHealthState.ONLINE)

            # GET called with allow_redirects=False and ssl!=False
            mock_sess.get.assert_called_once()
            call_kwargs = mock_sess.get.call_args[1]
            self.assertEqual(call_kwargs.get("allow_redirects"), False)
            self.assertNotEqual(call_kwargs.get("ssl"), False)

            # Admin receives alert with 301
            bot.send_message.assert_called_once()
            call_args = bot.send_message.call_args[1]
            self.assertIn("301", call_args["text"])
            self.assertIn("cdn.just1k.best", call_args["text"])

    async def test_node_monitor_ingress_probe_redirect_302_fails(self):
        """Redirect 302 is treated as failure."""
        clear_monitor_states()
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=True)

        server = Server(
            id=13,
            name="Origin 302 Check",
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
            mock_sess.get.return_value = MockProbeResponse(status=302)
            mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
            mock_sess.__aexit__ = AsyncMock(return_value=None)
            mock_http.return_value = mock_sess

            mock_scope.return_value.__aenter__.return_value = session_mock
            mock_snap.return_value = (server, True)

            await check_node_resources_and_alerts(bot)

            # Core health stays ONLINE
            self.assertEqual(mock_snap.call_args[1]["health_state"], ServerHealthState.ONLINE)

            # Admin receives alert with 302
            bot.send_message.assert_called_once()
            call_args = bot.send_message.call_args[1]
            self.assertIn("302", call_args["text"])

    async def test_node_monitor_ingress_probe_tls_certificate_error_fails(self):
        """TLS certificate verification failure triggers alert and keeps core node healthy."""
        clear_monitor_states()
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=True)

        server = Server(
            id=14,
            name="Origin TLS Error Check",
            protocol=XRAY_PROTOCOL,
            api_url="https://194.113.106.134:8444",
            api_key="secret-key",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            capabilities=["xray_origin"],
            extra_data={"cdn_domain": "cdn.just1k.best"},
        )

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
            mock_sess.get.side_effect = ssl.SSLCertVerificationError("certificate verify failed: certificate has expired")
            mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
            mock_sess.__aexit__ = AsyncMock(return_value=None)
            mock_http.return_value = mock_sess

            mock_scope.return_value.__aenter__.return_value = session_mock
            mock_snap.return_value = (server, True)

            await check_node_resources_and_alerts(bot)

            # Core health stays ONLINE
            self.assertEqual(mock_snap.call_args[1]["health_state"], ServerHealthState.ONLINE)

            # Alert sent with SSL detail
            bot.send_message.assert_called_once()
            call_args = bot.send_message.call_args[1]
            self.assertIn("certificate verify failed", call_args["text"])

    def test_server_monitor_state_sync_from_db_restores_ingress_problem(self):
        """ServerMonitorState restores ingress_problem from db_server.extra_data on worker restart."""
        server = Server(
            id=42,
            name="Origin Extra Test",
            protocol=XRAY_PROTOCOL,
            api_url="https://194.113.106.134:8444",
            api_key="secret-key",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            extra_data={"ingress_problem": True, "cdn_domain": "cdn.just1k.best"},
        )
        state = ServerMonitorState(server_id=42)
        state.sync_from_db_server(server)
        self.assertTrue(state.ingress_problem)

        # And when False or missing, state is False
        server.extra_data = {"cdn_domain": "cdn.just1k.best"}
        state2 = ServerMonitorState(server_id=42)
        state2.sync_from_db_server(server)
        self.assertFalse(state2.ingress_problem)


    async def test_node_monitor_ingress_alert_delivery_failure_allows_retry(self):
        """When sending alert message fails, ingress_problem is NOT marked as True, allowing retry next tick."""
        clear_monitor_states()
        bot = MagicMock()
        bot.send_message = AsyncMock(side_effect=Exception("Telegram connection timeout"))

        server = Server(
            id=15,
            name="Origin Delivery Fail Check",
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
             patch("services.workers.node_monitor.update_server", new_callable=AsyncMock) as mock_update, \
             patch("services.workers.node_monitor.get_settings", return_value=mock_settings):

            mock_sess = MagicMock()
            mock_sess.get.return_value = MockProbeResponse(status=502)
            mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
            mock_sess.__aexit__ = AsyncMock(return_value=None)
            mock_http.return_value = mock_sess

            mock_scope.return_value.__aenter__.return_value = session_mock
            mock_snap.return_value = (server, True)

            # Execution must NOT raise an unhandled exception
            await check_node_resources_and_alerts(bot)

            # Retrieve cached monitor state
            from services.workers.node_monitor import get_server_monitor_state
            cached_st = get_server_monitor_state(server.id)
            self.assertFalse(cached_st.ingress_problem)
            mock_update.assert_not_called()

    async def test_node_monitor_ingress_probe_executes_when_xray_client_fails(self):
        """When core Xray client check_health throws an unhandled exception, ingress synthetic probe still executes."""
        clear_monitor_states()
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MagicMock())

        server = Server(
            id=16,
            name="Origin Decoupled Test",
            protocol=XRAY_PROTOCOL,
            api_url="https://194.113.106.134:8444",
            api_key="secret-key",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            capabilities=["xray_origin"],
            extra_data={"cdn_domain": "cdn.just1k.best"},
        )

        class FailingXrayClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

            async def check_health(self, api_url, api_key):
                raise RuntimeError("Core API connection refused")

        class MockProbeResponse:
            def __init__(self, status):
                self.status = status

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

        session_mock = AsyncMock()
        mock_settings = MagicMock()
        mock_settings.ADMIN_IDS = [999999]

        with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
             patch("services.workers.node_monitor.session_scope") as mock_scope, \
             patch("services.xray_node_client.XrayNodeClient", FailingXrayClient), \
             patch("services.workers.node_monitor.aiohttp.ClientSession") as mock_http, \
             patch("services.workers.node_monitor.update_server_health_snapshot", new_callable=AsyncMock) as mock_snap, \
             patch("services.workers.node_monitor.get_server_by_id", new_callable=AsyncMock, return_value=server), \
             patch("services.workers.node_monitor.update_server", new_callable=AsyncMock) as mock_update, \
             patch("services.workers.node_monitor.get_settings", return_value=mock_settings):

            mock_sess = MagicMock()
            mock_sess.get.return_value = MockProbeResponse(status=502)
            mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
            mock_sess.__aexit__ = AsyncMock(return_value=None)
            mock_http.return_value = mock_sess

            mock_scope.return_value.__aenter__.return_value = session_mock
            mock_snap.return_value = (server, True)

            await check_node_resources_and_alerts(bot)

            # Ingress probe MUST have been called despite FailingXrayClient
            mock_sess.get.assert_called_once()
            call_url = mock_sess.get.call_args[0][0]
            self.assertTrue(call_url.endswith("/sub/wl/ping"))

            # Alert delivered for ingress
            from services.workers.node_monitor import get_server_monitor_state
            cached_st = get_server_monitor_state(server.id)
            self.assertTrue(cached_st.ingress_problem)
            mock_update.assert_called()

    async def test_node_monitor_ingress_probe_respects_custom_sub_prefix(self):
        """Ingress synthetic probe respects WHITE_INTERNET_SUB_PATH_PREFIX env variable."""
        clear_monitor_states()
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MagicMock())

        server = Server(
            id=17,
            name="Origin Prefix Test",
            protocol=XRAY_PROTOCOL,
            api_url="https://194.113.106.134:8444",
            api_key="secret-key",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            capabilities=["xray_origin"],
            extra_data={"cdn_domain": "cdn.just1k.best"},
        )

        class MockXrayClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

            async def check_health(self, api_url, api_key):
                return True, 1, {"status": "ok"}

        class MockProbeResponse:
            def __init__(self, status):
                self.status = status

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

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
             patch("services.workers.node_monitor.get_settings", return_value=mock_settings), \
             patch.dict("os.environ", {"WHITE_INTERNET_SUB_PATH_PREFIX": "/custom_feed"}):

            mock_sess = MagicMock()
            mock_sess.get.return_value = MockProbeResponse(status=200)
            mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
            mock_sess.__aexit__ = AsyncMock(return_value=None)
            mock_http.return_value = mock_sess

            mock_scope.return_value.__aenter__.return_value = session_mock
            mock_snap.return_value = (server, True)

            await check_node_resources_and_alerts(bot)

            mock_sess.get.assert_called_once()
            call_url = mock_sess.get.call_args[0][0]
            self.assertEqual(call_url, "https://cdn.just1k.best/custom_feed/ping")

    async def test_node_monitor_ingress_probe_prioritizes_server_extra_data_sub_prefix(self):
        """Ingress synthetic probe prioritizes server.extra_data['sub_path_prefix'] over env variable."""
        clear_monitor_states()
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MagicMock())

        server = Server(
            id=170,
            name="Origin Prefix Priority Test",
            protocol=XRAY_PROTOCOL,
            api_url="https://194.113.106.134:8444",
            api_key="secret-key",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            capabilities=["xray_origin"],
            extra_data={"cdn_domain": "cdn.just1k.best", "sub_path_prefix": "/node_custom_feed"},
        )

        class MockXrayClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

            async def check_health(self, api_url, api_key):
                return True, 1, {"status": "ok", "sub_path_prefix": "/node_custom_feed"}

        class MockProbeResponse:
            def __init__(self, status):
                self.status = status

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

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
             patch("services.workers.node_monitor.get_settings", return_value=mock_settings), \
             patch.dict("os.environ", {"WHITE_INTERNET_SUB_PATH_PREFIX": "/env_fallback_feed"}):

            mock_sess = MagicMock()
            mock_sess.get.return_value = MockProbeResponse(status=200)
            mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
            mock_sess.__aexit__ = AsyncMock(return_value=None)
            mock_http.return_value = mock_sess

            mock_scope.return_value.__aenter__.return_value = session_mock
            mock_snap.return_value = (server, True)

            await check_node_resources_and_alerts(bot)

            mock_sess.get.assert_called_once()
            call_url = mock_sess.get.call_args[0][0]
            self.assertEqual(call_url, "https://cdn.just1k.best/node_custom_feed/ping")

            # Verify snapshot update persisted sub_path_prefix from xray_data
            mock_snap.assert_called_once()
            call_kwargs = mock_snap.call_args[1]
            self.assertIn("extra_data", call_kwargs)
            self.assertEqual(call_kwargs["extra_data"].get("sub_path_prefix"), "/node_custom_feed")

    async def test_node_monitor_ingress_alert_displays_custom_sub_prefix_in_text(self):
        """Ingress problem and restored alerts display the custom endpoint in message text."""
        clear_monitor_states()
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MagicMock())

        server = Server(
            id=171,
            name="Origin Dynamic Alert Test",
            protocol=XRAY_PROTOCOL,
            api_url="https://194.113.106.134:8444",
            api_key="secret-key",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            capabilities=["xray_origin"],
            extra_data={"cdn_domain": "cdn.just1k.best", "sub_path_prefix": "/custom_alert_feed"},
        )

        class MockXrayClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

            async def check_health(self, api_url, api_key):
                return True, 1, {"status": "ok"}

        class MockProbeResponse:
            def __init__(self, status):
                self.status = status

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

        session_mock = AsyncMock()
        mock_settings = MagicMock()
        mock_settings.ADMIN_IDS = [999999]

        # 1. Ingress problem alert
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
            mock_sess.get.return_value = MockProbeResponse(status=502)
            mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
            mock_sess.__aexit__ = AsyncMock(return_value=None)
            mock_http.return_value = mock_sess

            mock_scope.return_value.__aenter__.return_value = session_mock
            mock_snap.return_value = (server, True)

            await check_node_resources_and_alerts(bot)

            bot.send_message.assert_called_once()
            alert_text = bot.send_message.call_args[1]["text"]
            self.assertIn("<code>/custom_alert_feed/ping</code>", alert_text)

        # 2. Ingress restored alert
        bot.send_message.reset_mock()
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
            mock_sess.get.return_value = MockProbeResponse(status=200)
            mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
            mock_sess.__aexit__ = AsyncMock(return_value=None)
            mock_http.return_value = mock_sess

            mock_scope.return_value.__aenter__.return_value = session_mock
            mock_snap.return_value = (server, True)

            await check_node_resources_and_alerts(bot)

            bot.send_message.assert_called_once()
            restored_text = bot.send_message.call_args[1]["text"]
            self.assertIn("<code>/custom_alert_feed/ping</code>", restored_text)

    async def test_node_monitor_ingress_alert_dedup_against_db_state(self):
        """When DB already marks ingress_problem=True, repeated failures do not dispatch duplicate alerts."""
        clear_monitor_states()
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MagicMock())

        server = Server(
            id=18,
            name="Origin Dedup Test",
            protocol=XRAY_PROTOCOL,
            api_url="https://194.113.106.134:8444",
            api_key="secret-key",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            capabilities=["xray_origin"],
            extra_data={"cdn_domain": "cdn.just1k.best", "ingress_problem": True},
        )

        class MockXrayClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

            async def check_health(self, api_url, api_key):
                return True, 1, {"status": "ok"}

        class MockProbeResponse:
            def __init__(self, status):
                self.status = status

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

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
             patch("services.workers.node_monitor.update_server", new_callable=AsyncMock) as mock_update, \
             patch("services.workers.node_monitor.get_settings", return_value=mock_settings):

            mock_sess = MagicMock()
            mock_sess.get.return_value = MockProbeResponse(status=502)
            mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
            mock_sess.__aexit__ = AsyncMock(return_value=None)
            mock_http.return_value = mock_sess

            mock_scope.return_value.__aenter__.return_value = session_mock
            mock_snap.return_value = (server, True)

            await check_node_resources_and_alerts(bot)

            # Ingress probe was called
            mock_sess.get.assert_called_once()
            # Bot send_message MUST NOT be called because DB already had ingress_problem=True
            bot.send_message.assert_not_called()
            mock_update.assert_not_called()


if __name__ == "__main__":
    unittest.main()

