from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from database.models import APIOperation, Server, VPNProfile
from services.api_operations_executor import execute_claimed_api_operation
from services.api_operations_queue import (
    ClaimedAPIOperation,
    ensure_delete_operation,
    resolve_profile_endpoint_snapshot,
)


class TestApiOperationsEndpoint(unittest.IsolatedAsyncioTestCase):

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
            api_url="https://nl.example.org:8443",
            api_key="secret-key-pro",
        )

        session = AsyncMock()
        session.get = AsyncMock(return_value=active_server)

        server_id, server_name, api_url, api_key = await resolve_profile_endpoint_snapshot(
            session, profile
        )

        self.assertEqual(server_id, 2)
        self.assertEqual(server_name, "Нидерланды")
        self.assertEqual(api_url, "https://nl.example.org:8443")
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
            api_url_snapshot="https://legacy.example.com:8443",
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
        self.assertEqual(api_url, "https://legacy.example.com:8443")
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
            api_url_snapshot="https://nl.example.com:8443",
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
            api_url_snapshot="https://nl.example.org:8443",
            api_key_snapshot="new-key",
            peer_id="peer-104",
        )

        self.assertEqual(result.status, "retry")
        self.assertEqual(result.attempts, 0)
        self.assertEqual(result.api_url_snapshot, "https://nl.example.org:8443")
        self.assertEqual(result.api_key_snapshot, "new-key")

    async def test_operation_285_e2e_reconciliation_after_server_domain_change(self):
        """End-to-end regression test for operation 285 incident:

        1. Server 2 changed domain from https://nl.example.com:8443 to https://nl.example.org:8443.
        2. Claimed operation with old snapshot fails with server_endpoint_changed.
        3. ensure_delete_operation updates operation snapshots to the active server endpoint.
        4. Next execution with updated snapshot succeeds and calls finalize_delete_success.
        """
        active_server = Server(
            id=2,
            name="Нидерланды",
            api_url="https://nl.example.org:8443",
            api_key="secret-key-pro",
            protocol="amneziawg2",
        )

        # Step A: Execute operation with old snapshot -> fails with server_endpoint_changed
        stale_op = ClaimedAPIOperation(
            id=285,
            operation_type="delete_peer",
            idempotency_key="delete-peer:104:peer-104",
            server_id=2,
            profile_id=104,
            server_name_snapshot="Нидерланды",
            api_url_snapshot="https://nl.example.com:8443",  # stale domain
            api_key_snapshot="secret-key-pro",
            peer_id="peer-104",
            client_name="tg_100_p104_n2",
            payload={"managed_workflow": True},
            attempt_number=1,
            max_attempts=10,
            locked_by="worker-1",
            last_error_code=None,
            last_error=None,
        )

        with patch("services.api_operations_executor.session_scope") as mock_scope, patch(
            "services.api_operations_executor.finalize_operation_failure",
            new_callable=AsyncMock,
        ) as mock_fail:
            session_mock = AsyncMock()
            session_mock.get = AsyncMock(return_value=active_server)
            mock_scope.return_value.__aenter__.return_value = session_mock

            await execute_claimed_api_operation(stale_op)

            mock_fail.assert_awaited_once_with(
                285,
                worker_id="worker-1",
                expected_attempt_number=1,
                retryable=False,
                error_code="server_endpoint_changed",
                error_message="operation endpoint snapshot does not match the current server identity",
            )

        # Step B: reconcile dead operation using ensure_delete_operation with active server parameters
        db_operation = APIOperation(
            id=285,
            operation_type="delete_peer",
            status="dead",
            attempts=1,
            server_id=2,
            server_name_snapshot="Нидерланды",
            api_url_snapshot="https://nl.example.com:8443",
            api_key_snapshot="secret-key-pro",
            peer_id="peer-104",
            payload={"managed_workflow": True},
        )
        queue_session = AsyncMock()
        exec_mock = MagicMock()
        exec_mock.scalar_one_or_none.return_value = db_operation
        queue_session.execute = AsyncMock(return_value=exec_mock)

        reconciled = await ensure_delete_operation(
            queue_session,
            idempotency_key="delete-peer:104:peer-104",
            server_id=active_server.id,
            profile_id=104,
            server_name_snapshot=active_server.name,
            api_url_snapshot=active_server.api_url,
            api_key_snapshot=active_server.api_key,
            peer_id="peer-104",
        )
        self.assertEqual(reconciled.status, "retry")
        self.assertEqual(reconciled.attempts, 0)
        self.assertEqual(reconciled.api_url_snapshot, "https://nl.example.org:8443")

        # Step C: Re-execute claimed operation with updated snapshot -> succeeds
        updated_op = ClaimedAPIOperation(
            id=285,
            operation_type="delete_peer",
            idempotency_key="delete-peer:104:peer-104",
            server_id=2,
            profile_id=104,
            server_name_snapshot="Нидерланды",
            api_url_snapshot="https://nl.example.org:8443",  # updated domain
            api_key_snapshot="secret-key-pro",
            peer_id="peer-104",
            client_name="tg_100_p104_n2",
            payload={"managed_workflow": True},
            attempt_number=1,
            max_attempts=10,
            locked_by="worker-1",
            last_error_code=None,
            last_error=None,
        )

        fake_client = AsyncMock()
        fake_client.delete_user_result = AsyncMock(
            return_value=SimpleNamespace(ok=True, retryable=False, ambiguous=False)
        )

        with patch("services.api_operations_executor.session_scope") as mock_scope, patch(
            "services.api_operations_executor.AmneziaClient",
            return_value=fake_client,
        ), patch(
            "services.api_operations_executor.finalize_delete_success",
            new_callable=AsyncMock,
        ) as mock_success:
            session_mock = AsyncMock()
            session_mock.get = AsyncMock(return_value=active_server)
            mock_scope.return_value.__aenter__.return_value = session_mock

            await execute_claimed_api_operation(updated_op)

            fake_client.delete_user_result.assert_awaited_once_with("peer-104")
            mock_success.assert_awaited_once_with(
                285, worker_id="worker-1", expected_attempt_number=1
            )
