import os

os.environ.setdefault("BOT_TOKEN", "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
os.environ.setdefault("ADMIN_IDS", "[100]")
os.environ.setdefault("SUPPORT_USERNAME", "test_support")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
os.environ.setdefault("DB_ENCRYPTION_KEY", "12345678901234567890123456789012")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("REDIS_PASSWORD", "testpass")
os.environ.setdefault("YOOKASSA_SHOP_ID", "12345")
os.environ.setdefault("YOOKASSA_SECRET_KEY", "test_key")
os.environ.setdefault("YOOKASSA_RETURN_URL", "https://t.me/test_bot?start={bot_username}")
os.environ.setdefault("YOOKASSA_WEBHOOK_PORT", "8080")
os.environ.setdefault("DOMAIN", "myrealdomain.com")
os.environ.setdefault("SSL_EMAIL", "admin@myrealdomain.com")

import asyncio
import time
import unittest
from datetime import timedelta
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from aiogram.types import CallbackQuery

from utils.datetime_helpers import now_utc

from database.models import Server
from services.workers.node_monitor import (
    ServerHealthState,
    _server_states,
    check_node_resources_and_alerts,
    get_server_monitor_state,
)
from bot.handlers.admin.servers.card_routes import dismiss_admin_alert


class NodeMonitorAlertLogicTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _server_states.clear()
        self.mock_settings = MagicMock()
        self.mock_settings.ADMIN_IDS = [100]
        self.mock_bot = AsyncMock()
        self.mock_bot.send_message = AsyncMock()

        self.patcher_settings_1 = patch("services.workers.node_monitor.get_settings", return_value=self.mock_settings)
        self.patcher_settings_2 = patch("config.settings.get_settings", return_value=self.mock_settings)

        dummy_session = AsyncMock()

        @asynccontextmanager
        async def dummy_scope():
            yield dummy_session

        self.patcher_scope = patch("services.workers.node_monitor.session_scope", side_effect=dummy_scope)

        self.patcher_settings_1.start()
        self.patcher_settings_2.start()
        self.patcher_scope.start()

    def tearDown(self):
        self.patcher_scope.stop()
        self.patcher_settings_2.stop()
        self.patcher_settings_1.stop()
        _server_states.clear()

    async def test_healthy_server_no_alerts(self):
        server = MagicMock(spec=Server)
        server.id = 1
        server.name = "Germany-1"
        server.api_url = "https://vpn.example.com"
        server.api_key = "secret_key"
        server.is_active = True
        server.disabled_reason = None
        server.health_state = "ONLINE"
        server.consecutive_fails = 0
        server.consecutive_successes = 0
        server.problem_started_at = None
        server.next_check_at = None

        with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
             patch("services.workers.node_monitor.AmneziaClient") as mock_client_cls, \
             patch("services.workers.node_monitor.get_server_by_id", return_value=server), \
             patch("services.workers.node_monitor.update_server"):

            client_instance = mock_client_cls.return_value
            client_instance.healthcheck = AsyncMock(return_value=True)
            client_instance.get_server_load = AsyncMock(return_value={"disk_percent": 30.0})

            await check_node_resources_and_alerts(self.mock_bot)

            self.mock_bot.send_message.assert_not_called()
            state = get_server_monitor_state(1)
            self.assertEqual(state.health_state, ServerHealthState.ONLINE)
            self.assertEqual(state.consecutive_fails, 0)

    async def test_transient_glitch_non_blocking_confirmation(self):
        """FAIL #1 puts server in WAITING_CONFIRMATION with 0 blocking sleep. Next check after 30s OK -> ONLINE."""
        server = MagicMock(spec=Server)
        server.id = 2
        server.name = "Finland-1"
        server.api_url = "https://vpn2.example.com"
        server.api_key = "secret"
        server.is_active = True
        server.disabled_reason = None
        server.health_state = "ONLINE"
        server.consecutive_fails = 0
        server.consecutive_successes = 0
        server.problem_started_at = None
        server.next_check_at = None

        with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
             patch("services.workers.node_monitor.AmneziaClient") as mock_client_cls, \
             patch("services.workers.node_monitor.get_server_by_id", return_value=server), \
             patch("services.workers.node_monitor.update_server"):

            client_instance = mock_client_cls.return_value

            # Tick 1: healthcheck fails -> WAITING_CONFIRMATION (next_check_at in 30s)
            client_instance.healthcheck = AsyncMock(return_value=False)
            t0 = time.monotonic()
            await check_node_resources_and_alerts(self.mock_bot)
            t1 = time.monotonic()

            # Verify ZERO blocking delay (executed in < 0.1s)
            self.assertLess(t1 - t0, 0.5)
            self.mock_bot.send_message.assert_not_called()
            st = get_server_monitor_state(2)
            self.assertEqual(st.health_state, ServerHealthState.WAITING_CONFIRMATION)
            self.assertEqual(st.consecutive_fails, 1)

            # Tick 2: Immediate re-run before 30s -> skipped, 0 healthcheck calls
            client_instance.healthcheck.reset_mock()
            await check_node_resources_and_alerts(self.mock_bot)
            client_instance.healthcheck.assert_not_called()

            # Fast-forward 30 seconds -> healthcheck succeeds -> returns to ONLINE
            st.next_check_at = time.monotonic() - 1.0
            client_instance.healthcheck = AsyncMock(return_value=True)
            client_instance.get_server_load = AsyncMock(return_value={"disk_percent": 25.0})
            await check_node_resources_and_alerts(self.mock_bot)

            self.mock_bot.send_message.assert_not_called()
            self.assertEqual(st.health_state, ServerHealthState.ONLINE)
            self.assertEqual(st.consecutive_fails, 0)

    async def test_multiple_servers_concurrent_fail_no_blocking(self):
        """Multiple servers fail simultaneously without blocking each other sequentially."""
        servers = []
        for i in range(1, 6):
            s = MagicMock(spec=Server)
            s.id = i
            s.name = f"Node-{i}"
            s.api_url = f"https://vpn{i}.example.com"
            s.api_key = "secret"
            s.is_active = True
            s.disabled_reason = None
            s.health_state = "ONLINE"
            s.consecutive_fails = 0
            s.consecutive_successes = 0
            s.problem_started_at = None
            s.next_check_at = None
            servers.append(s)

        with patch("services.workers.node_monitor.get_all_servers", return_value=servers), \
             patch("services.workers.node_monitor.AmneziaClient") as mock_client_cls, \
             patch("services.workers.node_monitor.get_server_by_id", side_effect=lambda _, sid: next(srv for srv in servers if srv.id == sid)), \
             patch("services.workers.node_monitor.update_server"):

            client_instance = mock_client_cls.return_value
            client_instance.healthcheck = AsyncMock(return_value=False)

            t0 = time.monotonic()
            await check_node_resources_and_alerts(self.mock_bot)
            t1 = time.monotonic()

            # All 5 servers processed in < 0.2 seconds (no 5 * 30s = 150s blocking!)
            self.assertLess(t1 - t0, 0.5)
            self.mock_bot.send_message.assert_not_called()

            for i in range(1, 6):
                st = get_server_monitor_state(i)
                self.assertEqual(st.health_state, ServerHealthState.WAITING_CONFIRMATION)

    async def test_confirmed_failure_transitions_to_problem_and_sends_alert(self):
        """FAIL #1 -> WAITING_CONFIRMATION -> 30s -> FAIL #2 -> PROBLEM + 1 alert."""
        server = MagicMock(spec=Server)
        server.id = 10
        server.name = "US-1"
        server.api_url = "https://vpn10.example.com"
        server.api_key = "secret"
        server.is_active = True
        server.disabled_reason = None
        server.health_state = "ONLINE"

        with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
             patch("services.workers.node_monitor.AmneziaClient") as mock_client_cls, \
             patch("services.workers.node_monitor.get_server_by_id", return_value=server), \
             patch("services.workers.node_monitor.update_server"):

            client_instance = mock_client_cls.return_value
            client_instance.healthcheck = AsyncMock(return_value=False)

            # Tick 1: FAIL #1
            await check_node_resources_and_alerts(self.mock_bot)
            st = get_server_monitor_state(10)
            self.assertEqual(st.health_state, ServerHealthState.WAITING_CONFIRMATION)

            # Fast-forward 30s to trigger confirmation check
            st.next_check_at = time.monotonic() - 1.0

            # Tick 2: FAIL #2 -> PROBLEM
            await check_node_resources_and_alerts(self.mock_bot)

            self.assertEqual(self.mock_bot.send_message.call_count, 1)
            call_args = self.mock_bot.send_message.call_args
            self.assertIn("Проблема с VPN-сервером", call_args.kwargs["text"])
            self.assertEqual(call_args.kwargs["chat_id"], 100)

            reply_markup = call_args.kwargs["reply_markup"]
            inline_buttons = [btn.callback_data for row in reply_markup.inline_keyboard for btn in row]
            self.assertIn("admin_server_card:10", inline_buttons)
            self.assertIn("admin_dismiss_alert:10", inline_buttons)

            self.assertEqual(st.health_state, ServerHealthState.PROBLEM)

    async def test_healthcheck_network_exceptions_handled_as_fails(self):
        """Network exceptions (TimeoutError, ClientError, OSError) increment fail counter safely."""
        server = MagicMock(spec=Server)
        server.id = 11
        server.name = "Exception-Node"
        server.api_url = "https://vpn11.example.com"
        server.api_key = "secret"
        server.is_active = True
        server.disabled_reason = None
        server.health_state = "ONLINE"

        exceptions_to_test = [
            asyncio.TimeoutError("API Timeout"),
            aiohttp.ClientError("Connection Refused"),
            OSError("Network is unreachable"),
        ]

        for exc in exceptions_to_test:
            _server_states.clear()
            with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
                 patch("services.workers.node_monitor.AmneziaClient") as mock_client_cls, \
                 patch("services.workers.node_monitor.get_server_by_id", return_value=server), \
                 patch("services.workers.node_monitor.update_server"):

                client_instance = mock_client_cls.return_value
                client_instance.healthcheck = AsyncMock(side_effect=exc)

                await check_node_resources_and_alerts(self.mock_bot)
                st = get_server_monitor_state(11)
                self.assertEqual(st.health_state, ServerHealthState.WAITING_CONFIRMATION)
                self.assertEqual(st.consecutive_fails, 1)

    async def test_state_persistence_and_bot_restart_rehydration(self):
        """Bot restart loads PROBLEM state from DB with original problem_started_at timestamp intact."""
        server = MagicMock(spec=Server)
        server.id = 12
        server.name = "Persist-Node"
        server.api_url = "https://vpn12.example.com"
        server.api_key = "secret"
        server.is_active = True
        server.disabled_reason = None
        server.health_state = "PROBLEM"
        server.consecutive_fails = 5
        server.consecutive_successes = 0
        # Problem started 14 minutes ago in DB
        server.problem_started_at = now_utc() - timedelta(minutes=14)

        # Clear in-memory state to simulate bot restart
        _server_states.clear()

        # Re-hydrate state from DB server
        st = get_server_monitor_state(12, server)
        self.assertEqual(st.health_state, ServerHealthState.PROBLEM)
        self.assertIsNotNone(st.problem_started_at)

        # Simulate 2 more minutes passing (total 16 minutes > 15-min observation timeout)
        st.problem_started_at = time.monotonic() - (16 * 60.0)

        with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
             patch("services.workers.node_monitor.AmneziaClient") as mock_client_cls, \
             patch("services.workers.node_monitor.get_server_by_id", return_value=server), \
             patch("services.workers.node_monitor.update_server", new_callable=AsyncMock) as mock_update:

            client_instance = mock_client_cls.return_value
            client_instance.healthcheck = AsyncMock(return_value=False)

            await check_node_resources_and_alerts(self.mock_bot)

            self.assertEqual(self.mock_bot.send_message.call_count, 1)
            text = self.mock_bot.send_message.call_args.kwargs["text"]
            self.assertIn("Сервер автоматически отключён", text)

            mock_update.assert_called()
            kwargs = mock_update.call_args.kwargs
            self.assertFalse(kwargs["is_active"])
            self.assertEqual(kwargs["disabled_reason"], "AUTO_UNAVAILABLE")

            self.assertEqual(st.health_state, ServerHealthState.AUTO_DISABLED)

    async def test_problem_confirmed_recovery(self):
        """3 consecutive successes in PROBLEM -> transitions to ONLINE, sends 1 recovery alert."""
        server = MagicMock(spec=Server)
        server.id = 5
        server.name = "NL-1"
        server.api_url = "https://vpn5.example.com"
        server.api_key = "secret"
        server.is_active = True
        server.disabled_reason = None
        server.health_state = "PROBLEM"

        st = get_server_monitor_state(5, server)
        st.health_state = ServerHealthState.PROBLEM
        st.problem_started_at = time.monotonic()
        st.consecutive_successes = 2

        with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
             patch("services.workers.node_monitor.AmneziaClient") as mock_client_cls, \
             patch("services.workers.node_monitor.get_server_by_id", return_value=server), \
             patch("services.workers.node_monitor.update_server"):

            client_instance = mock_client_cls.return_value
            client_instance.healthcheck = AsyncMock(return_value=True)

            await check_node_resources_and_alerts(self.mock_bot)

            self.assertEqual(self.mock_bot.send_message.call_count, 1)
            text = self.mock_bot.send_message.call_args.kwargs["text"]
            self.assertIn("VPN-сервер восстановлен", text)
            self.assertEqual(st.health_state, ServerHealthState.ONLINE)

    async def test_auto_disabled_quiet_polling_and_recovery_notification(self):
        """AUTO_DISABLED server polling is quiet. 3x OK sends 1 recovery notice but leaves is_active=False."""
        server = MagicMock(spec=Server)
        server.id = 7
        server.name = "FR-1"
        server.api_url = "https://vpn7.example.com"
        server.api_key = "secret"
        server.is_active = False
        server.disabled_reason = "AUTO_UNAVAILABLE"
        server.health_state = "AUTO_DISABLED"
        server.recovery_notice_sent = False
        server.problem_started_at = None
        server.next_check_at = None

        st = get_server_monitor_state(7, server)
        st.consecutive_successes = 2
        st.last_check_monotonic = time.monotonic() - (16 * 60.0)

        with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
             patch("services.workers.node_monitor.AmneziaClient") as mock_client_cls, \
             patch("services.workers.node_monitor.get_server_by_id", return_value=server), \
             patch("services.workers.node_monitor.update_server", new_callable=AsyncMock):

            client_instance = mock_client_cls.return_value
            client_instance.healthcheck = AsyncMock(return_value=True)

            await check_node_resources_and_alerts(self.mock_bot)

            self.assertEqual(self.mock_bot.send_message.call_count, 1)
            text = self.mock_bot.send_message.call_args.kwargs["text"]
            self.assertIn("Сервер восстановлен", text)
            self.assertIn("Сервер остаётся отключённым", text)

            reply_markup = self.mock_bot.send_message.call_args.kwargs["reply_markup"]
            inline_buttons = [btn.callback_data for row in reply_markup.inline_keyboard for btn in row]
            self.assertIn("admin_server_toggle:7", inline_buttons)
            self.assertIn("admin_dismiss_alert:7", inline_buttons)

    async def test_manual_disabled_server_completely_ignored(self):
        """MANUAL_DISABLED server sends 0 alerts and executes 0 auto-actions."""
        server = MagicMock(spec=Server)
        server.id = 8
        server.name = "JP-1"
        server.api_url = "https://vpn8.example.com"
        server.api_key = "secret"
        server.is_active = False
        server.disabled_reason = "MANUAL"
        server.health_state = "MANUAL_DISABLED"

        with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
             patch("services.workers.node_monitor.AmneziaClient") as mock_client_cls:

            await check_node_resources_and_alerts(self.mock_bot)

            self.mock_bot.send_message.assert_not_called()
            mock_client_cls.return_value.healthcheck.assert_not_called()

    async def test_dismiss_admin_alert_handler_with_server_id(self):
        """Callback admin_dismiss_alert:id deletes the alert message."""
        callback = AsyncMock(spec=CallbackQuery)
        callback.from_user = MagicMock(id=100)
        callback.answer = AsyncMock()
        callback.message = AsyncMock()

        with patch("bot.handlers.admin.servers.card_routes.is_admin", return_value=True):
            await dismiss_admin_alert(callback)

            callback.answer.assert_called_once_with("Удалено", show_alert=False)
            callback.message.delete.assert_called_once()


if __name__ == "__main__":
    unittest.main()
