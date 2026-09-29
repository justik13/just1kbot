"""Postgres behavioral tests for migration grace deletion (MissingGreenlet fix).

Runs only with TEST_DATABASE_URL (CI). Skipped on Windows dev machines.

Covers the real ORM path of
`services.api_operations_finalizer._schedule_migration_grace_deletion`:
server relation must resolve without lazy-load crash and the scheduled
delete must carry the old server protocol.
"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import Server, User, VPNProfile
from services.api_operations_finalizer import _schedule_migration_grace_deletion

DB = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class MigrationGraceDeletionPgTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env_patcher = patch.dict(
            os.environ,
            {
                "BOT_TOKEN": "123:test",
                "REDIS_URL": "redis://localhost:6379/1",
                "REDIS_PASSWORD": "test",
                "ADMIN_IDS": "[123456789]",
                "SUPPORT_USERNAME": "test_support",
                "DOMAIN": "test.domain",
                "SSL_EMAIL": "test@domain.com",
                "YOOKASSA_SHOP_ID": "123456",
                "YOOKASSA_SECRET_KEY": "test_secret",
                "YOOKASSA_RETURN_URL": "https://t.me/{bot_username}",
                "YOOKASSA_WEBHOOK_PORT": "8080",
                "DB_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
                "DATABASE_URL": os.getenv(
                    "TEST_DATABASE_URL",
                    "postgresql+asyncpg://projectx:projectx@localhost:5432/projectx_test",
                ),
            },
        )
        self.env_patcher.start()
        from config.settings import get_settings

        get_settings.cache_clear()

        self.engine = create_async_engine(DB)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        try:
            from tests.db_utils import TRUNCATE_SQL
        except ImportError:
            from db_utils import TRUNCATE_SQL

        async with self.engine.begin() as conn:
            await conn.execute(text(TRUNCATE_SQL))

    async def asyncTearDown(self):
        from config.settings import get_settings

        get_settings.cache_clear()
        self.env_patcher.stop()
        await self.engine.dispose()

    async def _make_migration_pair(self, session):
        user = User(telegram_id=910001)
        session.add(user)
        await session.flush()
        server = Server(
            name="OldServer",
            api_url="https://old.server",
            api_key="key",
            protocol="amneziawg2",
        )
        session.add(server)
        await session.flush()
        old = VPNProfile(
            user_id=user.id,
            server_id=server.id,
            device_name="old-phone",
            peer_id="peer-55",
            client_name="tg_910001_p55",
            provisioning_status="active",
        )
        new = VPNProfile(
            user_id=user.id,
            server_id=server.id,
            device_name="new-phone",
            provisioning_status="active",
        )
        session.add_all([old, new])
        await session.flush()
        return old, new

    async def test_grace_deletion_resolves_protocol_without_lazy_load_crash(self):
        async with self.sessions.begin() as session:
            old, new = await self._make_migration_pair(session)
            operation = SimpleNamespace(payload={"migrating_from_id": old.id})

            with (
                patch(
                    "services.api_operations_queue.resolve_profile_endpoint_snapshot",
                    new_callable=AsyncMock,
                    return_value=(old.server_id, "OldServer", "https://old.server", "key"),
                ),
                patch(
                    "services.api_operations_queue.ensure_delete_operation",
                    new_callable=AsyncMock,
                ) as mock_ensure_delete,
            ):
                await _schedule_migration_grace_deletion(session, operation, new)

            self.assertEqual(old.provisioning_status, "deleting")
            mock_ensure_delete.assert_awaited_once()
            _, kwargs = mock_ensure_delete.call_args
            self.assertEqual(kwargs["protocol"], "amneziawg2")
            self.assertEqual(kwargs["peer_id"], "peer-55")
            self.assertEqual(kwargs["server_id"], old.server_id)

    async def test_no_migration_payload_is_noop(self):
        async with self.sessions.begin() as session:
            old, new = await self._make_migration_pair(session)
            operation = SimpleNamespace(payload={})

            with patch(
                "services.api_operations_queue.ensure_delete_operation",
                new_callable=AsyncMock,
            ) as mock_ensure_delete:
                await _schedule_migration_grace_deletion(session, operation, new)

            mock_ensure_delete.assert_not_awaited()
            self.assertEqual(old.provisioning_status, "active")


if __name__ == "__main__":
    unittest.main()
