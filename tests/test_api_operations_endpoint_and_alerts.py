from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from database.models import APIOperation, Server, VPNProfile
from services.api_operations_queue import (
    ensure_delete_operation,
    resolve_profile_endpoint_snapshot,
)
from services.workers.api_operations import (
    clear_alerted_dead_ops,
    notify_dead_operation,
    set_api_operations_bot,
)


class TestApiOperationsEndpointAndAlerts(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        clear_alerted_dead_ops()
        set_api_operations_bot(None)

    def tearDown(self):
        clear_alerted_dead_ops()
        set_api_operations_bot(None)

    async def test_resolve_profile_endpoint_snapshot_prefers_active_server_over_history(self):
        """Active Server row in DB is primary source of truth, ignoring stale historical snapshot."""
        profile = VPNProfile(
            id=104,
            user_id=1,
            server_id=2,
            device_name="Phone",
            peer_id="peer-104",
        )
        active_server = Server(
            id=2,
            name="Нидерланды",
            api_url="https://nl.just1k.pro:8443",
            api_key="secret-key-pro",
        )

        session = AsyncMock()
        session.get = AsyncMock(return_value=active_server)

        server_id, server_name, api_url, api_key = await resolve_profile_endpoint_snapshot(
            session, profile
        )

        self.assertEqual(server_id, 2)
        self.assertEqual(server_name, "Нидерланды")
        self.assertEqual(api_url, "https://nl.just1k.pro:8443")
        self.assertEqual(api_key, "secret-key-pro")
        # Ensure it didn't even need to query historical operations
        session.execute.assert_not_called()

    async def test_resolve_profile_endpoint_snapshot_falls_back_to_history_when_server_deleted(self):
        """When Server row was deleted from DB, fall back to historical snapshot from previous op."""
        profile = VPNProfile(
            id=104,
            user_id=1,
            server_id=2,
            device_name="Phone",
            peer_id="peer-104",
        )
        prev_op = APIOperation(
            id=10,
            profile_id=104,
            server_id=2,
            server_name_snapshot="Legacy Server",
            api_url_snapshot="https://legacy.just1k.best:8443",
            api_key_snapshot="legacy-key",
        )

        session = AsyncMock()
        session.get = AsyncMock(return_value=None)  # Server deleted
        exec_res = MagicMock()
        exec_res.scalar_one_or_none.return_value = prev_op
        session.execute = AsyncMock(return_value=exec_res)

        server_id, server_name, api_url, api_key = await resolve_profile_endpoint_snapshot(
            session, profile
        )

        self.assertEqual(server_id, 2)
        self.assertEqual(server_name, "Legacy Server")
        self.assertEqual(api_url, "https://legacy.just1k.best:8443")
        self.assertEqual(api_key, "legacy-key")

    async def test_ensure_delete_operation_updates_snapshots_on_reviving_dead_operation(self):
        """When reviving dead operation, snapshots must be updated to new server parameters."""
        dead_operation = APIOperation(
            id=285,
            operation_type="delete_peer",
            status="dead",
            attempts=1,
            server_id=2,
            server_name_snapshot="Нидерланды",
            api_url_snapshot="https://nl.just1k.best:8443",
            api_key_snapshot="old-key",
            peer_id="peer-104",
            payload={"managed_workflow": True},
        )

        session = AsyncMock()
        exec_mock = MagicMock()
        exec_mock.scalar_one_or_none.return_value = dead_operation
        session.execute = AsyncMock(return_value=exec_mock)

        result = await ensure_delete_operation(
            session,
            idempotency_key="delete-peer:104:peer-104",
            server_id=2,
            profile_id=104,
            server_name_snapshot="Нидерланды",
            api_url_snapshot="https://nl.just1k.pro:8443",
            api_key_snapshot="new-key",
            peer_id="peer-104",
        )

        self.assertEqual(result.status, "retry")
        self.assertEqual(result.attempts, 0)
        self.assertEqual(result.api_url_snapshot, "https://nl.just1k.pro:8443")
        self.assertEqual(result.api_key_snapshot, "new-key")

    async def test_notify_dead_operation_sends_telegram_alert_to_admins(self):
        """Permanent operation failure dispatches formatted alert to all configured ADMIN_IDS."""
        fake_bot = MagicMock()
        set_api_operations_bot(fake_bot)

        with patch("config.settings.get_settings") as mock_settings, patch(
            "utils.telegram.safe_send_message", new_callable=AsyncMock
        ) as mock_send:
            mock_settings.return_value = MagicMock(ADMIN_IDS=[111, 222])

            await notify_dead_operation(
                operation_id=285,
                operation_type="delete_peer",
                server_id=2,
                server_name="Нидерланды",
                profile_id=104,
                client_name="tg_100_p104_n2",
                error_code="server_endpoint_changed",
                error_message="endpoint snapshot mismatch",
            )

            self.assertEqual(mock_send.await_count, 2)
            first_call_args = mock_send.await_args_list[0]
            self.assertEqual(first_call_args.kwargs["chat_id"], 111)
            msg_text = first_call_args.kwargs["text"]
            self.assertIn("Сбой фоновой операции сервера", msg_text)
            self.assertIn("Нидерланды", msg_text)
            self.assertIn("delete_peer", msg_text)
            self.assertIn("#285", msg_text)
            self.assertIn("server_endpoint_changed", msg_text)

    async def test_notify_dead_operation_deduplicates_alerts(self):
        """Second alert for same operation_id is suppressed to avoid admin spam."""
        fake_bot = MagicMock()
        set_api_operations_bot(fake_bot)

        with patch("config.settings.get_settings") as mock_settings, patch(
            "utils.telegram.safe_send_message", new_callable=AsyncMock
        ) as mock_send:
            mock_settings.return_value = MagicMock(ADMIN_IDS=[111])

            # Call 1: should send
            await notify_dead_operation(
                operation_id=285,
                operation_type="delete_peer",
                server_id=2,
                server_name="Нидерланды",
                profile_id=104,
                client_name="tg_100_p104_n2",
                error_code="server_endpoint_changed",
                error_message="mismatch",
            )
            self.assertEqual(mock_send.await_count, 1)

            # Call 2: duplicate op_id, must not send
            await notify_dead_operation(
                operation_id=285,
                operation_type="delete_peer",
                server_id=2,
                server_name="Нидерланды",
                profile_id=104,
                client_name="tg_100_p104_n2",
                error_code="server_endpoint_changed",
                error_message="mismatch",
            )
            self.assertEqual(mock_send.await_count, 1)

    async def test_notify_dead_operation_noop_when_no_bot(self):
        """Without a running bot instance, notify_dead_operation exits cleanly."""
        set_api_operations_bot(None)
        with patch("utils.telegram.safe_send_message", new_callable=AsyncMock) as mock_send:
            await notify_dead_operation(
                operation_id=999,
                operation_type="create_peer",
                server_id=1,
                server_name="DE",
                profile_id=10,
                client_name="test",
                error_code="fatal",
                error_message="fatal",
            )
            mock_send.assert_not_called()
