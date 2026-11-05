import os

os.environ.setdefault("BOT_TOKEN", "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
os.environ.setdefault("ADMIN_IDS", "[100]")
os.environ.setdefault("SUPPORT_USERNAME", "test_support")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
os.environ.setdefault("DB_ENCRYPTION_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
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
from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import CallbackQuery

from bot.handlers.admin.servers.card_routes import dismiss_admin_alert
from database.models import Server
from services.workers.node_monitor import (
    ServerHealthState,
    _server_states,
    check_node_resources_and_alerts,
    get_server_monitor_state,
)
from utils.datetime_helpers import now_utc


class NodeMonitorAlertLogicTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _server_states.clear()
        self.mock_settings = MagicMock()
        self.mock_settings.ADMIN_IDS = [100]
        self.mock_bot = AsyncMock()
        self.mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))

        self.patcher_settings_1 = patch("services.workers.node_monitor.get_settings", return_value=self.mock_settings)
        self.patcher_settings_2 = patch("config.settings.get_settings", return_value=self.mock_settings)

        dummy_session = AsyncMock()

        @asynccontextmanager
        async def dummy_scope():
            yield dummy_session

        self.patcher_scope = patch("services.workers.node_monitor.session_scope", side_effect=dummy_scope)

        async def mock_update_snapshot(session, server_id, expected_health_state, new_health_state, **kwargs):
            from services.workers.node_monitor import (
                get_all_servers as node_get_all_servers,
            )
            servers = await node_get_all_servers(session)
            target = next((s for s in servers if s.id == server_id), None) if servers else None
            if target:
                for k, v in kwargs.items():
                    setattr(target, k, v)
                target.health_state = new_health_state
                return target, True
            dummy = MagicMock()
            dummy.is_active = True
            dummy.health_state = new_health_state
            return dummy, True

        self.patcher_snapshot = patch(
            "services.workers.node_monitor.update_server_health_snapshot",
            side_effect=mock_update_snapshot,
        )

        self.mock_xray_client = AsyncMock()
        self.mock_xray_client.check_health = AsyncMock(return_value=(True, "epoch-1", {}))
        self.patcher_xray = patch("services.xray_node_client.XrayNodeClient")
        self.mock_xray_cls = self.patcher_xray.start()
        self.mock_xray_cls.return_value.__aenter__.return_value = self.mock_xray_client
        self.mock_xray_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        self.patcher_cas = patch("services.workers.node_monitor.update_server_xray_epoch_cas", new_callable=AsyncMock)
        self.mock_cas = self.patcher_cas.start()
        self.mock_cas.side_effect = lambda *args, **kwargs: (True, MagicMock())

        self.patcher_settings_1.start()
        self.patcher_settings_2.start()
        self.patcher_scope.start()
        self.patcher_snapshot.start()

    def tearDown(self):
        self.patcher_cas.stop()
        self.patcher_xray.stop()
        self.patcher_snapshot.stop()
        self.patcher_scope.stop()
        self.patcher_settings_2.stop()
        self.patcher_settings_1.stop()
        _server_states.clear()

    def _make_server(self, server_id=1, name="Server-1", **kwargs):
        s = MagicMock(spec=Server)
        s.id = server_id
        s.name = name
        s.protocol = "vless"
        s.capabilities = ["vless"]
        s.extra_data = {}
        s.api_url = f"https://vpn{server_id}.example.com:8443"
        s.api_key = "secret"
        s.is_active = True
        s.disabled_reason = None
        s.health_state = "ONLINE"
        s.consecutive_fails = 0
        s.consecutive_successes = 0
        s.problem_started_at = None
        s.next_check_at = None
        s.recovery_notice_sent = False
        s.last_alert_sent_state = None
        for k, v in kwargs.items():
            setattr(s, k, v)
        return s

    async def test_healthy_server_no_alerts(self):
        server = self._make_server(1, "Germany-1")

        with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
             patch("services.workers.node_monitor.get_server_by_id", return_value=server), \
             patch("services.workers.node_monitor.update_server"):

            self.mock_xray_client.check_health.return_value = (True, "epoch-1", {})

            await check_node_resources_and_alerts(self.mock_bot)

            self.mock_bot.send_message.assert_not_called()
            state = get_server_monitor_state(1)
            self.assertEqual(state.health_state, ServerHealthState.ONLINE)
            self.assertEqual(state.consecutive_fails, 0)

    async def test_transient_glitch_resets_consecutive_successes_to_zero(self):
        """FAIL #1 puts server in WAITING_CONFIRMATION. Next check OK resets consecutive_successes to 0."""
        server = self._make_server(2, "Finland-1", consecutive_successes=5)

        with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
             patch("services.workers.node_monitor.get_server_by_id", return_value=server), \
             patch("services.workers.node_monitor.update_server"):

            # Tick 1: healthcheck fails -> WAITING_CONFIRMATION
            self.mock_xray_client.check_health.return_value = (False, None, None)
            await check_node_resources_and_alerts(self.mock_bot)

            st = get_server_monitor_state(2)
            self.assertEqual(st.health_state, ServerHealthState.WAITING_CONFIRMATION)

            # Fast-forward 30 seconds -> healthcheck succeeds -> returns to ONLINE with consecutive_successes=0
            st.next_check_at = time.monotonic() - 1.0
            self.mock_xray_client.check_health.return_value = (True, "epoch-1", {})
            await check_node_resources_and_alerts(self.mock_bot)

            self.mock_bot.send_message.assert_not_called()
            self.assertEqual(st.health_state, ServerHealthState.ONLINE)
            self.assertEqual(st.consecutive_successes, 0)

    async def test_recovery_streak_persisted_across_bot_restarts(self):
        """Every recovery check in PROBLEM saves consecutive_successes to DB and persists across restarts."""
        server = self._make_server(
            50,
            "Streak-Node",
            health_state="PROBLEM",
            consecutive_fails=2,
            consecutive_successes=0,
            problem_started_at=now_utc() - timedelta(minutes=5),
            last_alert_sent_state="PROBLEM",
        )

        async def fake_update_server(session, db_srv, **kwargs):
            for k, v in kwargs.items():
                setattr(db_srv, k, v)
            return db_srv

        with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
             patch("services.workers.node_monitor.get_server_by_id", return_value=server), \
             patch("services.workers.node_monitor.update_server", side_effect=fake_update_server):

            self.mock_xray_client.check_health.return_value = (True, "epoch-1", {})

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
            self.assertIn("Связь с сервером восстановлена", text)
            self.assertEqual(st_rehydrated.health_state, ServerHealthState.ONLINE)

    async def test_manual_enable_resets_db_and_allows_subsequent_recovery_notice(self):
        """Manual enable by admin resets recovery_notice_sent=False so subsequent failure cycles send recovery notice."""
        server = self._make_server(
            77,
            "Manual-Enable-Node",
            is_active=False,
            disabled_reason="MANUAL",
            health_state="MANUAL_DISABLED",
            consecutive_fails=3,
            consecutive_successes=3,
            recovery_notice_sent=True,
        )

        async def fake_update_server(session, db_srv, **kwargs):
            for k, v in kwargs.items():
                setattr(db_srv, k, v)
            return db_srv

        with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
             patch("services.workers.node_monitor.get_server_by_id", return_value=server), \
             patch("services.workers.node_monitor.update_server", side_effect=fake_update_server):

            self.mock_xray_client.check_health.return_value = (True, "epoch-1", {})

            # Admin toggles server ACTIVE (is_active = True)
            server.is_active = True

            await check_node_resources_and_alerts(self.mock_bot)
            st = get_server_monitor_state(77)

            # DB and RAM must be reset to ONLINE and recovery_notice_sent=False
            self.assertEqual(server.health_state, ServerHealthState.ONLINE)
            self.assertFalse(server.recovery_notice_sent)
            self.assertFalse(st.recovery_notice_sent)

            # Server fails again: ONLINE -> WAITING_CONFIRMATION -> PROBLEM
            self.mock_xray_client.check_health.return_value = (False, None, None)
            st.next_check_at = time.monotonic() - 1.0
            await check_node_resources_and_alerts(self.mock_bot)  # WAITING_CONFIRMATION
            st.next_check_at = time.monotonic() - 1.0
            await check_node_resources_and_alerts(self.mock_bot)  # PROBLEM
            self.assertEqual(st.health_state, ServerHealthState.PROBLEM)

            # Server recovers again in PROBLEM (3x OK)
            self.mock_xray_client.check_health.return_value = (True, "epoch-1", {})
            self.mock_bot.send_message.reset_mock()

            st.next_check_at = time.monotonic() - 1.0
            await check_node_resources_and_alerts(self.mock_bot)  # OK #1
            st.next_check_at = time.monotonic() - 1.0
            await check_node_resources_and_alerts(self.mock_bot)  # OK #2
            st.next_check_at = time.monotonic() - 1.0
            await check_node_resources_and_alerts(self.mock_bot)  # OK #3

            # Verify SECOND recovery notice IS sent!
            self.assertEqual(self.mock_bot.send_message.call_count, 1)
            text = self.mock_bot.send_message.call_args.kwargs["text"]
            self.assertIn("Связь с сервером восстановлена", text)
            self.assertEqual(st.health_state, ServerHealthState.ONLINE)

    async def test_waiting_confirmation_next_check_at_persisted_and_restored_on_restart(self):
        """WAITING_CONFIRMATION next_check_at is saved in DB and restored on bot restart to respect confirmation schedule."""
        server = self._make_server(
            88,
            "Auto-Schedule-Node",
            is_active=True,
            health_state="WAITING_CONFIRMATION",
            consecutive_fails=1,
            next_check_at=now_utc() + timedelta(minutes=10),
        )

        with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
             patch("services.workers.node_monitor.get_server_by_id", return_value=server), \
             patch("services.workers.node_monitor.update_server"):

            self.mock_xray_client.check_health.return_value = (True, "epoch-1", {})

            # Re-hydrate state from DB server (simulating bot restart)
            _server_states.clear()
            st = get_server_monitor_state(88, server)

            # next_check_at should be ~10 minutes in the future
            self.assertIsNotNone(st.next_check_at)
            self.assertGreater(st.next_check_at, time.monotonic())

            # Worker tick runs -> should skip checking because 10m remaining!
            await check_node_resources_and_alerts(self.mock_bot)
            self.mock_xray_client.check_health.assert_not_called()

    async def test_full_lifecycle_flow(self):
        """ONLINE -> WAITING_CONFIRMATION -> PROBLEM -> RECOVERY -> ONLINE."""
        server = self._make_server(99, "Lifecycle-Node")

        async def fake_update_server(session, db_srv, **kwargs):
            for k, v in kwargs.items():
                setattr(db_srv, k, v)
            return db_srv

        with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
             patch("services.workers.node_monitor.get_server_by_id", return_value=server), \
             patch("services.workers.node_monitor.update_server", side_effect=fake_update_server):

            # 1. FAIL #1 -> WAITING_CONFIRMATION
            self.mock_xray_client.check_health.return_value = (False, None, None)
            await check_node_resources_and_alerts(self.mock_bot)
            st = get_server_monitor_state(99)
            self.assertEqual(st.health_state, ServerHealthState.WAITING_CONFIRMATION)
            self.mock_bot.send_message.assert_not_called()

            # 2. Fast-forward 60s -> FAIL #2 -> PROBLEM (1 alert)
            st.next_check_at = time.monotonic() - 1.0
            await check_node_resources_and_alerts(self.mock_bot)
            self.assertEqual(st.health_state, ServerHealthState.PROBLEM)
            self.assertEqual(self.mock_bot.send_message.call_count, 1)
            self.assertIn("Проблема со связью", self.mock_bot.send_message.call_args.kwargs["text"])

            # 3. Simulate continued outage in PROBLEM: server remains is_active=True
            st.problem_started_at = time.monotonic() - (16 * 60.0)
            self.mock_bot.send_message.reset_mock()
            await check_node_resources_and_alerts(self.mock_bot)
            self.assertEqual(st.health_state, ServerHealthState.PROBLEM)
            self.assertTrue(server.is_active)
            self.mock_bot.send_message.assert_not_called()

            # 4. 3x OK in PROBLEM -> 1 recovery notice, transitions to ONLINE
            self.mock_xray_client.check_health.return_value = (True, "epoch-1", {})
            self.mock_bot.send_message.reset_mock()

            # OK #1
            st.next_check_at = time.monotonic() - 1.0
            await check_node_resources_and_alerts(self.mock_bot)
            self.assertEqual(st.consecutive_successes, 1)
            self.mock_bot.send_message.assert_not_called()

            # OK #2
            st.next_check_at = time.monotonic() - 1.0
            await check_node_resources_and_alerts(self.mock_bot)
            self.assertEqual(st.consecutive_successes, 2)
            self.mock_bot.send_message.assert_not_called()

            # OK #3 -> recovery notice sent
            st.next_check_at = time.monotonic() - 1.0
            await check_node_resources_and_alerts(self.mock_bot)
            self.assertEqual(st.consecutive_successes, 3)
            self.assertEqual(self.mock_bot.send_message.call_count, 1)
            self.assertIn("Связь с сервером восстановлена", self.mock_bot.send_message.call_args.kwargs["text"])
            self.assertEqual(st.health_state, ServerHealthState.ONLINE)
            self.assertTrue(server.is_active)

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


    async def test_telegram_send_failure_retries_until_delivered(self):
        """If Telegram message delivery fails, node_monitor retries on subsequent ticks until delivered."""
        server = self._make_server(101, "Retry-Alert-Node")

        async def fake_update_server(session, db_srv, **kwargs):
            for k, v in kwargs.items():
                setattr(db_srv, k, v)
            return db_srv

        with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
             patch("services.workers.node_monitor.get_server_by_id", return_value=server), \
             patch("services.workers.node_monitor.update_server", side_effect=fake_update_server):

            self.mock_xray_client.check_health.return_value = (False, None, None)

            st = get_server_monitor_state(101, server)

            # Tick 1: FAIL #1 -> WAITING_CONFIRMATION
            await check_node_resources_and_alerts(self.mock_bot)
            self.assertEqual(st.health_state, ServerHealthState.WAITING_CONFIRMATION)

            # Fast-forward 30s: FAIL #2 -> PROBLEM
            st.next_check_at = time.monotonic() - 1.0

            # Simulate Telegram API error on delivery attempt (e.g. exception thrown)
            self.mock_bot.send_message = AsyncMock(side_effect=Exception("Telegram Network Error"))

            await check_node_resources_and_alerts(self.mock_bot)

            # Health state is PROBLEM, BUT last_alert_sent_state remains None because Telegram failed!
            self.assertEqual(st.health_state, ServerHealthState.PROBLEM)
            self.assertNotEqual(st.last_alert_sent_state, ServerHealthState.PROBLEM)
            self.assertNotEqual(server.last_alert_sent_state, ServerHealthState.PROBLEM)

            # Tick 3 (15s later): Telegram API recovers!
            self.mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))

            await check_node_resources_and_alerts(self.mock_bot)

            # Telegram alert WAS successfully retried and delivered!
            self.assertEqual(self.mock_bot.send_message.call_count, 1)
            self.assertEqual(st.last_alert_sent_state, ServerHealthState.PROBLEM)
            self.assertEqual(server.last_alert_sent_state, ServerHealthState.PROBLEM)

    async def test_origin_relays_tspu_alert_and_recovery(self):
        origin_server = MagicMock(spec=Server)
        origin_server.id = 10
        origin_server.name = "Origin-RU"
        origin_server.protocol = "vless"
        origin_server.capabilities = ["xray_origin"]
        origin_server.extra_data = {
            "domain": "origin.test.pro",
            "relays": [{"ip": "191.44.113.0", "port": 10443, "name": "Poland-1"}],
        }
        origin_server.api_url = "https://origin.test.pro:8444"
        origin_server.api_key = "secret_origin"
        origin_server.is_active = True
        origin_server.disabled_reason = None
        origin_server.health_state = "ONLINE"
        origin_server.consecutive_fails = 0
        origin_server.consecutive_successes = 0
        origin_server.problem_started_at = None
        origin_server.next_check_at = None
        origin_server.recovery_notice_sent = False
        origin_server.last_alert_sent_state = None

        relay_server = MagicMock(spec=Server)
        relay_server.id = 20
        relay_server.name = "Poland-1"
        relay_server.protocol = "vless"
        relay_server.capabilities = ["xray"]
        relay_server.extra_data = {"ip": "191.44.113.0"}
        relay_server.api_url = "https://pl-node.test.pro:8443"
        relay_server.api_key = "secret_relay"
        relay_server.is_active = True
        relay_server.disabled_reason = None
        relay_server.health_state = "ONLINE"
        relay_server.consecutive_fails = 0
        relay_server.consecutive_successes = 0
        relay_server.problem_started_at = None
        relay_server.next_check_at = None
        relay_server.recovery_notice_sent = False
        relay_server.last_alert_sent_state = None

        all_servers = [origin_server, relay_server]

        with patch("services.workers.node_monitor.get_all_servers", return_value=all_servers), \
             patch("services.workers.node_monitor.get_server_by_id", side_effect=lambda session, s_id, **kw: next((s for s in all_servers if s.id == s_id), None)), \
             patch("services.workers.node_monitor.update_server", new_callable=AsyncMock), \
             patch("services.workers.node_monitor._probe_single_ingress_endpoint", new_callable=AsyncMock, return_value=(True, "OK")), \
             patch("services.xray_node_client.XrayNodeClient") as mock_xray_cls, \
             patch("services.workers.node_monitor.update_server_xray_epoch_cas", new_callable=AsyncMock) as mock_cas:

            mock_cas.return_value = (True, origin_server)
            mock_xray_client = AsyncMock()
            mock_xray_client.check_health = AsyncMock(return_value=(True, "epoch-1", {}))
            mock_xray_cls.return_value.__aenter__.return_value = mock_xray_client
            mock_xray_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            # 1. 1st fail
            mock_xray_client.get_relays_health = AsyncMock(return_value=(
                True,
                {"relays": [{"name": "Poland-1", "code": "pl", "ip": "191.44.113.0", "port": 10443, "healthy": False, "error": "Timeout (5.0s)", "rtt_ms": None}]},
                None,
            ))
            await check_node_resources_and_alerts(self.mock_bot)
            self.mock_bot.send_message.assert_not_called()

            # 2. 2nd-14th fails (still no alert until 15 consecutive fails)
            for _ in range(13):
                await check_node_resources_and_alerts(self.mock_bot)
                self.mock_bot.send_message.assert_not_called()

            # 3. 15th fail -> triggers alert
            await check_node_resources_and_alerts(self.mock_bot)
            self.assertEqual(self.mock_bot.send_message.call_count, 1)
            call_args = self.mock_bot.send_message.call_args
            sent_text = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("text")
            self.assertIn("191.44.113.0", sent_text)
            self.assertIn("Poland-1", sent_text)

            # 3b. 16th fail -> should NOT send duplicate alert
            await check_node_resources_and_alerts(self.mock_bot)
            self.assertEqual(self.mock_bot.send_message.call_count, 1)

            # Verify button links to relay_server.id (20) via extra_data["ip"] mapping
            kb = call_args[1].get("reply_markup")
            self.assertIsNotNone(kb)
            button_callback = kb.inline_keyboard[0][0].callback_data
            self.assertEqual(button_callback, "admin_server_card:20")

            # 4. Recovery cycle 1 & 2 -> no recovery notice yet (requires 3 consecutive successes)
            mock_xray_client.get_relays_health = AsyncMock(return_value=(
                True,
                {"relays": [{"name": "Poland-1", "code": "pl", "ip": "191.44.113.0", "port": 10443, "healthy": True, "error": None, "rtt_ms": 42.5}]},
                None,
            ))
            await check_node_resources_and_alerts(self.mock_bot)
            self.assertEqual(self.mock_bot.send_message.call_count, 1)
            await check_node_resources_and_alerts(self.mock_bot)
            self.assertEqual(self.mock_bot.send_message.call_count, 1)

            # 5. Recovery cycle 3 (success 3) -> recovery notice sent
            await check_node_resources_and_alerts(self.mock_bot)
            self.assertEqual(self.mock_bot.send_message.call_count, 2)
            rec_args = self.mock_bot.send_message.call_args
            rec_text = rec_args[0][1] if len(rec_args[0]) > 1 else rec_args[1].get("text")
            self.assertIn("42.5", rec_text)

    async def test_problem_server_hourly_outage_reminder(self):
        """Server in PROBLEM triggers periodic reminder alert every 1 hour (3600s)."""
        server = self._make_server(
            201,
            "Disabled-Remind-Node",
            is_active=True,
            health_state="PROBLEM",
            consecutive_fails=5,
            last_alert_sent_state=ServerHealthState.PROBLEM,
        )

        async def fake_update_server(session, db_srv, **kwargs):
            for k, v in kwargs.items():
                setattr(db_srv, k, v)
            return db_srv

        with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
             patch("services.workers.node_monitor.get_server_by_id", return_value=server), \
             patch("services.workers.node_monitor.update_server", side_effect=fake_update_server):

            self.mock_xray_client.check_health.return_value = (False, None, None)

            st = get_server_monitor_state(201, server)
            st.health_state = ServerHealthState.PROBLEM
            st.last_alert_sent_state = ServerHealthState.PROBLEM

            # 1. First tick in PROBLEM -> establishes last_core_reminder_at, no duplicate alert
            await check_node_resources_and_alerts(self.mock_bot)
            self.mock_bot.send_message.assert_not_called()
            self.assertIsNotNone(st.last_core_reminder_at)

            # 2. Advance clock by 3500s (< 1 hour) -> no reminder yet
            st.next_check_at = None
            with patch("time.monotonic", return_value=st.last_core_reminder_at + 3500.0):
                await check_node_resources_and_alerts(self.mock_bot)
                self.mock_bot.send_message.assert_not_called()

            # 3. Advance clock by 3605s (>= 1 hour) -> reminder alert sent
            st.next_check_at = None
            with patch("time.monotonic", return_value=st.last_core_reminder_at + 3605.0):
                await check_node_resources_and_alerts(self.mock_bot)
                self.assertEqual(self.mock_bot.send_message.call_count, 1)
                sent_text = self.mock_bot.send_message.call_args[1]["text"]
                self.assertIn("Напоминание: Связь отсутствует", sent_text)
                self.assertIn("Disabled-Remind-Node", sent_text)


if __name__ == "__main__":
    unittest.main()
