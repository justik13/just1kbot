import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

from database.connection import _run_alembic_migrations


class DatabaseStartupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/database")
        self.env_patcher = patch.dict(
            os.environ,
            {
                "ENVIRONMENT": "development",
                "BOT_TOKEN": "1234567890:TEST_BOT_TOKEN_FOR_TESTS",
                "REDIS_URL": "redis://localhost:6379/1",
                "REDIS_PASSWORD": "test_redis_password",
                "ADMIN_IDS": "[123456789]",
                "SUPPORT_USERNAME": "test_support_username",
                "DOMAIN": "test.domain.example.com",
                "SSL_EMAIL": "test@example.com",
                "YOOKASSA_SHOP_ID": "123456",
                "YOOKASSA_SECRET_KEY": "test_secret_key",
                "YOOKASSA_RETURN_URL": "https://t.me/{bot_username}",
                "YOOKASSA_WEBHOOK_PORT": "8080",
                "DB_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
                "DATABASE_URL": db_url,
            },
        )
        self.env_patcher.start()
        from config.settings import get_settings
        get_settings.cache_clear()

    async def asyncTearDown(self):
        self.env_patcher.stop()
        from config.settings import get_settings
        get_settings.cache_clear()

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

    @unittest.skipUnless(
        os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL"),
        "Live database URL is not configured",
    )
    async def test_close_db_lifecycle_and_reinitialization(self):
        import database.connection as conn
        from sqlalchemy import text

        # 1. Close any existing DB
        await conn.close_db()
        self.assertIsNone(conn._engine)
        self.assertIsNone(conn._sessionmaker)

        # 2. get_session() should trigger lazy init_db()
        session1 = await conn.get_session()
        self.assertIsNotNone(conn._engine)
        self.assertIsNotNone(conn._sessionmaker)
        res1 = await session1.execute(text("SELECT 1"))
        self.assertEqual(res1.scalar(), 1)
        await session1.close()

        # 3. close_db() disposes engine and clears references
        engine_ref = conn._engine
        await conn.close_db()
        self.assertIsNone(conn._engine)
        self.assertIsNone(conn._sessionmaker)

        # 4. Next get_session() cleanly reinitializes a brand new engine/sessionmaker
        session2 = await conn.get_session()
        self.assertIsNotNone(conn._engine)
        self.assertIsNotNone(conn._sessionmaker)
        self.assertIsNot(conn._engine, engine_ref)

        res2 = await session2.execute(text("SELECT 2"))
        self.assertEqual(res2.scalar(), 2)
        await session2.close()

        # Cleanup
        await conn.close_db()

    @unittest.skipUnless(
        os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL"),
        "Live database URL is not configured",
    )
    async def test_concurrent_get_session_initialization_safety(self):
        import database.connection as conn
        from sqlalchemy import text
        from unittest.mock import patch

        await conn.close_db()
        conn._db_lock = None  # ensure fresh lock for this test

        original_init = conn.init_db
        init_call_count = 0

        async def counting_init():
            nonlocal init_call_count
            init_call_count += 1
            return await original_init()

        # Concurrently request sessions from uninitialized state;
        # with the lock in place, init_db() should be called exactly once.
        async def fetch_one(val):
            async with conn.session_scope() as s:
                r = await s.execute(text(f"SELECT {val}"))
                return r.scalar()

        with patch.object(conn, "init_db", side_effect=counting_init):
            results = await asyncio.gather(*[fetch_one(i) for i in range(10)])

        self.assertEqual(sorted(results), list(range(10)))
        self.assertEqual(init_call_count, 1, f"init_db called {init_call_count} times instead of 1")

        # Cleanup
        await conn.close_db()


if __name__ == "__main__":
    unittest.main()
