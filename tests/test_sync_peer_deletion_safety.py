import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from database.models import PendingAPIDeletion
from services.subscription import SubscriptionService
from services.workers import cleanup


class _Scalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _Result:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _Scalars(self._values)


class PendingDeletionSafetyTests(unittest.IsolatedAsyncioTestCase):
    def _pending(self, reason):
        return PendingAPIDeletion(
            id=10,
            server_name="server-one",
            api_url="https://vpn.invalid",
            api_key="secret",
            peer_id="peer-identifier-which-is-long",
            reason=reason,
            attempts=0,
            last_error="original failure",
        )

    async def _run_cleanup(self, pending, delete_result=True):
        selected = False
        statements = []

        class FakeSession:
            async def execute(self, statement):
                nonlocal selected
                statements.append(statement)
                if not selected:
                    selected = True
                    return _Result([pending] if pending.attempts >= 0 else [])
                return MagicMock()

        @asynccontextmanager
        async def fake_scope():
            yield FakeSession()

        delete_user = AsyncMock(return_value=delete_result)
        client = MagicMock(delete_user=delete_user)
        with (
            patch.object(cleanup, "session_scope", fake_scope),
            patch.object(cleanup, "AmneziaClient", return_value=client),
        ):
            await cleanup._process_pending_deletions()

        return delete_user, statements

    async def test_sync_expires_failed_is_quarantined(self):
        pending = self._pending("sync_expires_failed")
        delete_user, _ = await self._run_cleanup(pending)

        delete_user.assert_not_awaited()
        self.assertEqual(pending.attempts, -1)
        self.assertTrue(
            pending.last_error.startswith(cleanup.QUARANTINE_ERROR_PREFIX)
        )

        # The production query requires attempts >= 0, so it cannot select it again.
        delete_user, _ = await self._run_cleanup(pending)
        delete_user.assert_not_awaited()

    async def test_sync_expires_critical_failure_is_quarantined(self):
        pending = self._pending("sync_expires_critical_failure")
        delete_user, _ = await self._run_cleanup(pending)

        delete_user.assert_not_awaited()
        self.assertEqual(pending.attempts, -1)

    async def test_unknown_sync_expires_reason_is_quarantined(self):
        pending = self._pending("sync_expires_future_failure")
        delete_user, _ = await self._run_cleanup(pending)

        delete_user.assert_not_awaited()
        self.assertEqual(pending.attempts, -1)

    async def test_real_deletion_still_calls_api_and_removes_pending(self):
        pending = self._pending("device_delete_api_failed")
        delete_user, statements = await self._run_cleanup(pending)

        delete_user.assert_awaited_once_with(client_id=pending.peer_id)
        self.assertEqual(len(statements), 2)
        self.assertIn("DELETE FROM pending_api_deletions", str(statements[1]))


class SubscriptionSyncSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def _run_sync(self, profiles, servers, update_results):
        sessions = []

        @asynccontextmanager
        async def fake_scope():
            session = MagicMock()
            session.add = MagicMock()
            sessions.append(session)
            yield session

        clients = []
        for result in update_results:
            client = MagicMock()
            if isinstance(result, Exception):
                client.update_client = AsyncMock(side_effect=result)
            else:
                client.update_client = AsyncMock(return_value=result)
            client.delete_user = AsyncMock()
            clients.append(client)

        with (
            patch("database.connection.session_scope", fake_scope),
            patch(
                "services.subscription.get_user_profiles",
                new=AsyncMock(return_value=profiles),
            ),
            patch(
                "services.subscription.get_server_by_id",
                new=AsyncMock(side_effect=lambda _session, sid: servers.get(sid)),
            ),
            patch(
                "services.subscription.AmneziaClient",
                side_effect=clients,
            ),
        ):
            await SubscriptionService._sync_expires_to_servers(
                user_id=42,
                expires_ts=123456,
                target_status="active",
            )

        return clients, sessions

    async def test_missing_server_does_not_shift_failed_job(self):
        missing = SimpleNamespace(
            id=1, user_id=42, server_id=100, peer_id="missing-peer"
        )
        valid = SimpleNamespace(
            id=2, user_id=42, server_id=200, peer_id="valid-peer"
        )
        server = SimpleNamespace(
            id=200,
            name="valid-server",
            api_url="https://vpn.invalid",
            api_key="secret",
        )

        with self.assertLogs("services.subscription", level="WARNING") as logs:
            clients, sessions = await self._run_sync(
                [missing, valid], {100: None, 200: server}, [RuntimeError("down")]
            )

        clients[0].update_client.assert_awaited_once_with(
            client_id="valid-peer",
            expires_at=123456,
            status="active",
            clear_expires_at=False,
        )
        clients[0].delete_user.assert_not_awaited()
        for session in sessions:
            session.add.assert_not_called()
        output = "\n".join(logs.output)
        self.assertIn("profile_id=1", output)
        self.assertIn("profile_id=2", output)
        self.assertNotIn("sync_expires_failed", output)

    async def test_successful_update_is_counted_without_delete_queue(self):
        profile = SimpleNamespace(
            id=2, user_id=42, server_id=200, peer_id="valid-peer"
        )
        server = SimpleNamespace(
            id=200,
            name="valid-server",
            api_url="https://vpn.invalid",
            api_key="secret",
        )

        with self.assertLogs("services.subscription", level="INFO") as logs:
            clients, sessions = await self._run_sync(
                [profile], {200: server}, [True]
            )

        clients[0].update_client.assert_awaited_once()
        clients[0].delete_user.assert_not_awaited()
        for session in sessions:
            session.add.assert_not_called()
        self.assertIn("1/1 servers updated", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
