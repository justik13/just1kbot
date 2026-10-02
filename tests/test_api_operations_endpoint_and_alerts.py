from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

from database.models import APIOperation, Server, VPNProfile
from services.api_operations_queue import (
    ensure_delete_operation,
    resolve_profile_endpoint_snapshot,
)


class TestApiOperationsEndpointAndAlerts(unittest.IsolatedAsyncioTestCase):

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

