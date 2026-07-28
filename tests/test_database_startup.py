import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from database.connection import _run_alembic_migrations


class DatabaseStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_migration_does_not_run_in_active_event_loop(self):
        upgrade_thread_id = None

        def fake_upgrade(config, revision):
            nonlocal upgrade_thread_id
            upgrade_thread_id = __import__("threading").get_ident()
            self.assertEqual(revision, "head")

        current_thread_id = __import__("threading").get_ident()
        with (
            patch("database.connection.upgrade", side_effect=fake_upgrade),
            patch(
                "database.connection._seed_default_data",
                new=AsyncMock(),
            ) as seed,
        ):
            await _run_alembic_migrations(
                "postgresql+asyncpg://user:pass@localhost/database"
            )

        self.assertIsNotNone(asyncio.get_running_loop())
        self.assertNotEqual(upgrade_thread_id, current_thread_id)
        seed.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
