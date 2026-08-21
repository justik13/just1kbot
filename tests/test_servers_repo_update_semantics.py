import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from database.repositories.servers_repo import update_server, update_server_health_snapshot


class ServerRepoUpdateSemanticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_ordinary_update_does_not_issue_row_lock_query(self):
        session = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = session
        session.begin_nested.return_value.__aexit__.return_value = None
        server = SimpleNamespace(id=1, name="old", max_clients=50)

        result = await update_server(session, server, name="new")

        self.assertIs(result, server)
        self.assertEqual(server.name, "new")
        session.execute.assert_not_called()
        session.flush.assert_awaited_once()
        session.refresh.assert_awaited_once_with(server)

    async def test_health_update_uses_locked_current_row_and_rejects_stale_snapshot(self):
        session = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = session
        session.begin_nested.return_value.__aexit__.return_value = None
        current = SimpleNamespace(
            id=2,
            name="node",
            is_active=True,
            disabled_reason=None,
            disabled_at=None,
            health_state="PROBLEM",
            consecutive_fails=2,
        )

        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = current
        session.execute.return_value = exec_result

        result_server, applied = await update_server_health_snapshot(
            session,
            server_id=2,
            expected_health_state="ONLINE",
            new_health_state="ONLINE",
            consecutive_fails=0,
        )

        self.assertIs(result_server, current)
        self.assertFalse(applied)
        self.assertEqual(current.health_state, "PROBLEM")
        session.execute.assert_awaited_once()

    async def test_health_update_rejects_consecutive_fails_mismatch(self):
        session = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = session
        session.begin_nested.return_value.__aexit__.return_value = None
        current = SimpleNamespace(
            id=3,
            name="node-3",
            is_active=True,
            disabled_reason=None,
            disabled_at=None,
            health_state="PROBLEM",
            consecutive_fails=3,
        )

        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = current
        session.execute.return_value = exec_result

        result_server, applied = await update_server_health_snapshot(
            session,
            server_id=3,
            expected_health_state="PROBLEM",  # Matching health_state
            expected_consecutive_fails=2,     # Mismatched consecutive_fails (DB has 3)
            new_health_state="PROBLEM",
            consecutive_fails=4,
        )

        self.assertIs(result_server, current)
        self.assertFalse(applied)
        self.assertEqual(current.consecutive_fails, 3)

    async def test_health_update_rejects_consecutive_successes_mismatch(self):
        session = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = session
        session.begin_nested.return_value.__aexit__.return_value = None
        current = SimpleNamespace(
            id=4,
            name="node-4",
            is_active=True,
            disabled_reason=None,
            disabled_at=None,
            health_state="ONLINE",
            consecutive_fails=0,
            consecutive_successes=5,
        )

        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = current
        session.execute.return_value = exec_result

        result_server, applied = await update_server_health_snapshot(
            session,
            server_id=4,
            expected_health_state="ONLINE",
            expected_consecutive_fails=0,
            expected_consecutive_successes=4,  # Mismatched consecutive_successes (DB has 5)
            new_health_state="ONLINE",
            consecutive_successes=6,
        )

        self.assertIs(result_server, current)
        self.assertFalse(applied)
        self.assertEqual(current.consecutive_successes, 5)


if __name__ == "__main__":
    unittest.main()
