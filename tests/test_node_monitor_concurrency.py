import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from database.repositories.servers_repo import update_server, update_server_health_snapshot
from services.workers.node_monitor import check_node_resources_and_alerts, get_server_monitor_state


class NodeMonitorConcurrencyUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_server_is_pure_crud_without_kwargs_stripping(self):
        session = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = session
        session.begin_nested.return_value.__aexit__.return_value = None
        server = SimpleNamespace(id=10, name="Node A", is_active=True, health_state="ONLINE", disabled_reason=None)

        result = await update_server(
            session,
            server,
            is_active=False,
            disabled_reason="MANUAL",
            health_state="MANUAL_DISABLED",
        )

        self.assertIs(result, server)
        self.assertFalse(server.is_active)
        self.assertEqual(server.disabled_reason, "MANUAL")
        self.assertEqual(server.health_state, "MANUAL_DISABLED")
        session.flush.assert_awaited_once()

    async def test_update_server_health_snapshot_rejects_inactive_or_manually_disabled_server(self):
        session = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = session
        session.begin_nested.return_value.__aexit__.return_value = None
        inactive_server = SimpleNamespace(
            id=1, is_active=False, health_state="MANUAL_DISABLED", disabled_reason="MANUAL"
        )
        exec_res = MagicMock()
        exec_res.scalar_one_or_none.return_value = inactive_server
        session.execute.return_value = exec_res

        db_server, applied = await update_server_health_snapshot(
            session,
            server_id=1,
            expected_health_state="ONLINE",
            new_health_state="PROBLEM",
            consecutive_fails=2,
        )

        self.assertIs(db_server, inactive_server)
        self.assertFalse(applied)
        self.assertEqual(inactive_server.health_state, "MANUAL_DISABLED")

    async def test_update_server_health_snapshot_rejects_stale_expected_health_mismatch(self):
        session = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = session
        session.begin_nested.return_value.__aexit__.return_value = None
        db_server = SimpleNamespace(
            id=2, is_active=True, health_state="PROBLEM", consecutive_fails=2
        )
        exec_res = MagicMock()
        exec_res.scalar_one_or_none.return_value = db_server
        session.execute.return_value = exec_res

        res_server, applied = await update_server_health_snapshot(
            session,
            server_id=2,
            expected_health_state="ONLINE",  # Mismatches DB state "PROBLEM"
            new_health_state="ONLINE",
            consecutive_fails=0,
        )

        self.assertIs(res_server, db_server)
        self.assertFalse(applied)
        self.assertEqual(db_server.health_state, "PROBLEM")

    async def test_node_monitor_resyncs_ram_state_when_db_write_is_rejected(self):
        bot = AsyncMock()
        server_initial = SimpleNamespace(
            id=100,
            name="DE-Node",
            api_url="http://127.0.0.1:3000",
            api_key="secret",
            is_active=True,
            disabled_reason=None,
            health_state="ONLINE",
            problem_started_at=None,
            next_check_at=None,
            consecutive_fails=0,
            consecutive_successes=0,
            recovery_notice_sent=False,
            last_alert_sent_state=None,
        )

        st = get_server_monitor_state(100, server_initial)
        self.assertEqual(st.health_state, "ONLINE")

        # Simulate network failure during healthcheck
        client_mock = AsyncMock()
        client_mock.healthcheck.return_value = False

        mock_scope = AsyncMock()
        mock_scope.__aenter__.return_value = AsyncMock()

        # Simulate DB returning a server that was manually disabled by admin during the healthcheck
        server_disabled_in_db = SimpleNamespace(
            id=100,
            name="DE-Node",
            api_url="http://127.0.0.1:3000",
            api_key="secret",
            is_active=False,
            disabled_reason="MANUAL",
            health_state="MANUAL_DISABLED",
            problem_started_at=None,
            next_check_at=None,
            consecutive_fails=0,
            consecutive_successes=0,
            recovery_notice_sent=False,
            last_alert_sent_state=None,
        )

        settings_obj = MagicMock()
        settings_obj.ADMIN_IDS = [999]

        with (
            patch("services.workers.node_monitor.session_scope", return_value=mock_scope),
            patch("services.workers.node_monitor.get_all_servers", AsyncMock(return_value=[server_initial])),
            patch("services.workers.node_monitor.update_server_health_snapshot", AsyncMock(return_value=(server_disabled_in_db, False))),
            patch("services.workers.node_monitor.AmneziaClient", return_value=client_mock),
            patch("services.workers.node_monitor.get_settings", return_value=settings_obj),
            patch("config.settings.get_settings", return_value=settings_obj),
        ):
            await check_node_resources_and_alerts(bot)

            # RAM state must be resynced immediately from server_disabled_in_db!
            self.assertEqual(st.health_state, "MANUAL_DISABLED")

    async def test_node_monitor_skips_telegram_alert_when_cas_write_is_rejected(self):
        bot = AsyncMock()
        server_initial = SimpleNamespace(
            id=200,
            name="FR-Node",
            api_url="http://127.0.0.1:3001",
            api_key="secret",
            is_active=True,
            disabled_reason=None,
            health_state="WAITING_CONFIRMATION",
            problem_started_at=None,
            next_check_at=None,
            consecutive_fails=1,
            consecutive_successes=0,
            recovery_notice_sent=False,
            last_alert_sent_state=None,
        )

        st = get_server_monitor_state(200, server_initial)

        # Healthcheck fails -> state machine generates PROBLEM alert
        client_mock = AsyncMock()
        client_mock.healthcheck.return_value = False

        mock_scope = AsyncMock()
        mock_scope.__aenter__.return_value = AsyncMock()

        # Admin disabled server in DB while healthcheck was running -> CAS returns applied=False
        server_disabled_in_db = SimpleNamespace(
            id=200,
            name="FR-Node",
            api_url="http://127.0.0.1:3001",
            api_key="secret",
            is_active=False,
            disabled_reason="MANUAL",
            health_state="MANUAL_DISABLED",
            problem_started_at=None,
            next_check_at=None,
            consecutive_fails=0,
            consecutive_successes=0,
            recovery_notice_sent=False,
            last_alert_sent_state=None,
        )

        send_alert_mock = AsyncMock()

        with (
            patch("services.workers.node_monitor.session_scope", return_value=mock_scope),
            patch("services.workers.node_monitor.get_all_servers", AsyncMock(return_value=[server_initial])),
            patch("services.workers.node_monitor.update_server_health_snapshot", AsyncMock(return_value=(server_disabled_in_db, False))),
            patch("services.workers.node_monitor._send_admin_alert_msg", send_alert_mock),
            patch("services.workers.node_monitor.AmneziaClient", return_value=client_mock),
        ):
            await check_node_resources_and_alerts(bot)

            # Verification: Because CAS returned applied=False, Telegram alert WAS NOT SENT!
            send_alert_mock.assert_not_called()
            self.assertEqual(st.health_state, "MANUAL_DISABLED")


if __name__ == "__main__":
    unittest.main()
