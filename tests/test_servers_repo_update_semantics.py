import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from database.repositories.servers_repo import update_server


class ServerRepoUpdateSemanticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_ordinary_update_does_not_issue_row_lock_query(self):
        session = AsyncMock()
        server = SimpleNamespace(id=1, name="old", max_clients=50)

        result = await update_server(session, server, name="new")

        self.assertIs(result, server)
        self.assertEqual(server.name, "new")
        session.execute.assert_not_called()
        session.flush.assert_awaited_once()
        session.refresh.assert_awaited_once_with(server)

    async def test_health_update_uses_locked_current_row_and_rejects_stale_snapshot(self):
        session = AsyncMock()
        stale = SimpleNamespace(
            id=2,
            name="node",
            is_active=True,
            disabled_reason=None,
            disabled_at=None,
            last_successful_check=None,
            health_state="ONLINE",
            problem_started_at=None,
            next_check_at=None,
            consecutive_fails=0,
            consecutive_successes=0,
            recovery_notice_sent=False,
            last_alert_sent_state=None,
        )
        current = SimpleNamespace(**stale.__dict__)
        current.health_state = "PROBLEM"
        session.execute.return_value.scalar_one_or_none.return_value = current

        result = await update_server(
            session,
            stale,
            health_state="ONLINE",
            consecutive_fails=0,
        )

        self.assertIs(result, current)
        self.assertEqual(current.health_state, "PROBLEM")
        session.execute.assert_awaited_once()
        session.flush.assert_awaited_once()
        session.refresh.assert_awaited_once_with(current)


if __name__ == "__main__":
    unittest.main()
