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

import time
import unittest
from datetime import timedelta
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

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

    async def test_transient_glitch_resets_consecutive_successes_to_zero(self):
        """FAIL #1 puts server in WAITING_CONFIRMATION. Next check OK resets consecutive_successes to 0."""
        server = MagicMock(spec=Server)
        server.id = 2
        server.name = "Finland-1"
        server.api_url = "https://vpn2.example.com"
        server.api_key = "secret"
        server.is_active = True
        server.disabled_reason = None
        server.health_state = "ONLINE"
        server.consecutive_fails = 0
        server.consecutive_successes = 5
        server.problem_started_at = None
        server.next_check_at = None

        with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
             patch("services.workers.node_monitor.AmneziaClient") as mock_client_cls, \
             patch("services.workers.node_monitor.get_server_by_id", return_value=server), \
             patch("services.workers.node_monitor.update_server") as mock_update:

            client_instance = mock_client_cls.return_value

            # Tick 1: healthcheck fails -> WAITING_CONFIRMATION
            client_instance.healthcheck = AsyncMock(return_value=False)
            await check_node_resources_and_alerts(self.mock_bot)

            st = get_server_monitor_state(2)
            self.assertEqual(st.health_state, ServerHealthState.WAITING_CONFIRMATION)

            # Fast-forward 30 seconds -> healthcheck succeeds -> returns to ONLINE with consecutive_successes=0
            st.next_check_at = time.monotonic() - 1.0
            client_instance.healthcheck = AsyncMock(return_value=True)
            client_instance.get_server_load = AsyncMock(return_value={"disk_percent": 25.0})
            await check_node_resources_and_alerts(self.mock_bot)

            self.mock_bot.send_message.assert_not_called()
            self.assertEqual(st.health_state, ServerHealthState.ONLINE)
            self.assertEqual(st.consecutive_successes, 0)
            mock_update.assert_called()
            self.assertEqual(mock_update.call_args.kwargs["consecutive_successes"], 0)

    async def test_recovery_streak_persisted_across_bot_restarts(self):
        """Every recovery check in PROBLEM saves consecutive_successes to DB and persists across restarts."""
        server = MagicMock(spec=Server)
        server.id = 50
        server.name = "Streak-Node"
        server.api_url = "https://vpn50.example.com"
        server.api_key = "secret"
        server.is_active = True
        server.disabled_reason = None
        server.health_state = "PROBLEM"
        server.consecutive_fails = 2
        server.consecutive_successes = 0
        server.problem_started_at = now_utc() - timedelta(minutes=5)
        server.next_check_at = None

        async def fake_update_server(session, db_srv, **kwargs):
            for k, v in kwargs.items():
                setattr(db_srv, k, v)
            return db_srv

        with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
             patch("services.workers.node_monitor.AmneziaClient") as mock_client_cls, \
             patch("services.workers.node_monitor.get_server_by_id", return_value=server), \
             patch("services.workers.node_monitor.update_server", side_effect=fake_update_server):

            client_instance = mock_client_cls.return_value
            client_instance.healthcheck = AsyncMock(return_value=True)
            client_instance.get_server_load = AsyncMock(return_value={"disk_percent": 30.0})

            # Check 1: OK #1 -> consecutive_successes = 1
            await check_node_resources_and_alerts(self.mock_bot)
            self.mock_bot.send_message.assert_not_called()
            self.assertEqual(server.consecutive_successes, 1)

            # Check 2: OK #2 -> consecutive_successes = 2
            await check_node_resources_and_alerts(self.mock_bot)
            self.mock_bot.send_message.assert_not_called()
            self.assertEqual(server.consecutive_successes, 2)

            # Simulate Bot Restart: clear RAM state and re-hydrate from DB server!
            _server_states.clear()
            st_rehydrated = get_server_monitor_state(50, server)
            self.assertEqual(st_rehydrated.consecutive_successes, 2)
            self.assertEqual(st_rehydrated.health_state, ServerHealthState.PROBLEM)

            # Check 3 (after restart): OK #3 -> reaches threshold 3 -> Recovers to ONLINE + 1 alert!
            await check_node_resources_and_alerts(self.mock_bot)
            self.assertEqual(self.mock_bot.send_message.call_count, 1)
            text = self.mock_bot.send_message.call_args.kwargs["text"]
            self.assertIn("VPN-сервер восстановлен", text)
            self.assertEqual(st_rehydrated.health_state, ServerHealthState.ONLINE)

    async def test_full_lifecycle_flow(self):
        """ONLINE -> WAITING_CONFIRMATION -> PROBLEM -> AUTO_DISABLED -> RECOVERY -> ONLINE."""
        server = MagicMock(spec=Server)
        server.id = 99
        server.name = "Lifecycle-Node"
        server.api_url = "https://vpn99.example.com"
        server.api_key = "secret"
        server.is_active = True
        server.disabled_reason = None
        server.health_state = "ONLINE"
        server.consecutive_fails = 0
        server.consecutive_successes = 0
        server.problem_started_at = None
        server.next_check_at = None
        server.recovery_notice_sent = False

        async def fake_update_server(session, db_srv, **kwargs):
            for k, v in kwargs.items():
                setattr(db_srv, k, v)
            return db_srv

        with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
             patch("services.workers.node_monitor.AmneziaClient") as mock_client_cls, \
             patch("services.workers.node_monitor.get_server_by_id", return_value=server), \
             patch("services.workers.node_monitor.update_server", side_effect=fake_update_server):

            client_instance = mock_client_cls.return_value
            client_instance.get_server_load = AsyncMock(return_value={"disk_percent": 30.0})

            # 1. FAIL #1 -> WAITING_CONFIRMATION
            client_instance.healthcheck = AsyncMock(return_value=False)
            await check_node_resources_and_alerts(self.mock_bot)
            st = get_server_monitor_state(99)
            self.assertEqual(st.health_state, ServerHealthState.WAITING_CONFIRMATION)
            self.mock_bot.send_message.assert_not_called()

            # 2. Fast-forward 30s -> FAIL #2 -> PROBLEM (1 alert)
            st.next_check_at = time.monotonic() - 1.0
            await check_node_resources_and_alerts(self.mock_bot)
            self.assertEqual(st.health_state, ServerHealthState.PROBLEM)
            self.assertEqual(self.mock_bot.send_message.call_count, 1)
            self.assertIn("Проблема с VPN-сервером", self.mock_bot.send_message.call_args.kwargs["text"])

            # 3. Simulate 16 minutes elapsed in PROBLEM -> AUTO_DISABLED (1 alert)
            st.problem_started_at = time.monotonic() - (16 * 60.0)
            self.mock_bot.send_message.reset_mock()
            await check_node_resources_and_alerts(self.mock_bot)
            self.assertEqual(st.health_state, ServerHealthState.AUTO_DISABLED)
            self.assertEqual(self.mock_bot.send_message.call_count, 1)
            self.assertIn("Сервер автоматически отключён", self.mock_bot.send_message.call_args.kwargs["text"])

            # 4. 3x OK in AUTO_DISABLED -> 1 recovery notice, remains is_active=False
            client_instance.healthcheck = AsyncMock(return_value=True)
            self.mock_bot.send_message.reset_mock()
            st.last_check_monotonic = 0.0

            # OK #1
            await check_node_resources_and_alerts(self.mock_bot)
            self.assertEqual(st.consecutive_successes, 1)
            self.mock_bot.send_message.assert_not_called()

            # OK #2
            st.last_check_monotonic = 0.0
            await check_node_resources_and_alerts(self.mock_bot)
            self.assertEqual(st.consecutive_successes, 2)
            self.mock_bot.send_message.assert_not_called()

            # OK #3 -> recovery notice sent
            st.last_check_monotonic = 0.0
            await check_node_resources_and_alerts(self.mock_bot)
            self.assertEqual(st.consecutive_successes, 3)
            self.assertEqual(self.mock_bot.send_message.call_count, 1)
            self.assertIn("Сервер восстановлен", self.mock_bot.send_message.call_args.kwargs["text"])
            self.assertTrue(st.recovery_notice_sent)

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
