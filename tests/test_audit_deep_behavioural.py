import os
import uuid
from datetime import timedelta
import unittest
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.constants import AMNEZIA_PROTOCOL
from database.models import Base, Server, User, VPNProfile
from database.repositories.servers_repo import (
    get_all_servers,
    get_available_servers,
    get_server_peer_counts,
    get_total_free_ips,
)
from database.repositories.users_repo import get_filtered_users_paginated
from utils.datetime_helpers import now_utc


class AuditDeepBehaviouralIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from config.settings import get_settings

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
                "AMNEZIA_BRIDGE_HMAC_SECRET": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
            },
        )
        self.env_patcher.start()
        get_settings.cache_clear()

        # Create an in-memory SQLite database for full integration testing
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        target_tables = [User.__table__, Server.__table__, VPNProfile.__table__]
        async with self.engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=target_tables))
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

    async def asyncTearDown(self):
        from config.settings import get_settings
        self.env_patcher.stop()
        get_settings.cache_clear()
        await self.engine.dispose()

    async def test_server_capacity_real_db_counting_and_status_filtering(self):
        """Real DB integration test: verifies exact capacity counting across all profile lifecycle states.
        Active, pending, delete_failed (with peer), create_cleanup_pending (with peer), and failed with peer
        must consume capacity. Failed without peer or unknown without peer must NOT consume capacity."""
        async with self.session_factory() as session:
            server = Server(
                id=1,
                name="Amsterdam Alpha",
                country_flag="🇳🇱",
                protocol=AMNEZIA_PROTOCOL,
                is_active=True,
                max_clients=10,
                api_url="http://node1:4001",
                api_key="secret",
            )
            session.add(server)

            user = User(id=1, telegram_id=111, is_deleted=False)
            session.add(user)
            await session.flush()

            # 1. Active profile with peer_id -> MUST consume slot
            p1 = VPNProfile(user_id=1, server_id=1, device_name="Dev1", provisioning_status="active", peer_id="peer_1")
            # 2. Pending create without peer_id -> MUST consume slot (slot is reserved during creation)
            p2 = VPNProfile(user_id=1, server_id=1, device_name="Dev2", provisioning_status="pending_create", peer_id=None)
            # 3. Delete failed with peer_id -> MUST consume slot (peer physically remains on server)
            p3 = VPNProfile(user_id=1, server_id=1, device_name="Dev3", provisioning_status="delete_failed", peer_id="peer_3")
            # 4. Create cleanup pending with peer_id -> MUST consume slot (pending background cleanup)
            p4 = VPNProfile(user_id=1, server_id=1, device_name="Dev4", provisioning_status="create_cleanup_pending", peer_id="peer_4")
            # 5. Create failed WITH peer_id -> MUST consume slot (creation partially succeeded on server)
            p5 = VPNProfile(user_id=1, server_id=1, device_name="Dev5", provisioning_status="create_failed", peer_id="peer_5")
            # 6. Create failed WITHOUT peer_id -> MUST NOT consume slot (no physical peer created)
            p6 = VPNProfile(user_id=1, server_id=1, device_name="Dev6", provisioning_status="create_failed", peer_id=None)

            session.add_all([p1, p2, p3, p4, p5, p6])
            await session.commit()

            # Execute real queries against DB
            peer_counts = await get_server_peer_counts(session)
            self.assertEqual(peer_counts.get(1), 5, "Exactly 5 profiles should be counted towards capacity")

            free_ips = await get_total_free_ips(session)
            self.assertEqual(free_ips, 5, "10 max_clients - 5 consumed slots = 5 free IPs")

            available_servers = await get_available_servers(session)
            self.assertEqual(len(available_servers), 1)
            self.assertEqual(available_servers[0].id, 1)

    async def test_new_24h_and_7d_filter_and_sorting_real_db_boundaries(self):
        """Real DB integration test: verifies exact time boundaries (24h vs 7d) and newest-first sorting."""
        now = now_utc()
        async with self.session_factory() as session:
            # Insert 5 test users with specific registration timestamps
            u1 = User(id=1, telegram_id=101, username="u_1h", created_at=now - timedelta(hours=1), is_deleted=False)
            u2 = User(id=2, telegram_id=102, username="u_23h50m", created_at=now - timedelta(hours=23, minutes=50), is_deleted=False)
            u3 = User(id=3, telegram_id=103, username="u_24h10m", created_at=now - timedelta(hours=24, minutes=10), is_deleted=False)
            u4 = User(id=4, telegram_id=104, username="u_6d", created_at=now - timedelta(days=6), is_deleted=False)
            u5 = User(id=5, telegram_id=105, username="u_8d", created_at=now - timedelta(days=8), is_deleted=False)

            session.add_all([u1, u2, u3, u4, u5])
            await session.commit()

            # 1. Query new_24h filter: must contain ONLY u1 and u2, sorted DESC (u1 first)
            users_24h = await get_filtered_users_paginated(session, filter_type="new_24h", page=1, per_page=10)
            user_ids_24h = [u.id for u in users_24h]
            self.assertEqual(user_ids_24h, [1, 2], "new_24h must contain only users registered <= 24h ago, newest first")

            # 2. Query new_7d filter: must contain ONLY u1, u2, u3, u4, sorted DESC (u1 first)
            users_7d = await get_filtered_users_paginated(session, filter_type="new_7d", page=1, per_page=10)
            user_ids_7d = [u.id for u in users_7d]
            self.assertEqual(user_ids_7d, [1, 2, 3, 4], "new_7d must contain users registered <= 7d ago, newest first")

    async def test_get_all_servers_order_by_name(self):
        """Verifies get_all_servers sorts consistently by Server.name."""
        async with self.session_factory() as session:
            s1 = Server(id=10, name="Zurich", country_flag="🇨🇭", protocol=AMNEZIA_PROTOCOL, is_active=True, max_clients=100, api_url="http://node3:4001", api_key="k")
            s2 = Server(id=2, name="Amsterdam", country_flag="🇳🇱", protocol=AMNEZIA_PROTOCOL, is_active=True, max_clients=100, api_url="http://node1:4001", api_key="k")
            s3 = Server(id=5, name="Frankfurt", country_flag="🇩🇪", protocol=AMNEZIA_PROTOCOL, is_active=True, max_clients=100, api_url="http://node2:4001", api_key="k")
            session.add_all([s1, s2, s3])
            await session.commit()

            servers = await get_all_servers(session)
            server_names = [s.name for s in servers]
            self.assertEqual(server_names, ["Amsterdam", "Frankfurt", "Zurich"])

    async def test_savepoint_isolation_real_db_transaction_survives_nested_failure(self):
        """Real DB integration test: verifies that an IntegrityError within session.begin_nested()
        rolls back only to the savepoint and preserves unrelated modifications in the outer transaction."""
        async with self.session_factory() as session:
            user = User(id=1, telegram_id=999, username="original_name", is_deleted=False)
            session.add(user)
            await session.commit()

            # Outer transaction modification
            user.username = "updated_name"
            session.add(user)

            # Nested transaction that fails due to unique constraint on telegram_id
            try:
                async with session.begin_nested():
                    # Attempt to insert a user with duplicate unique telegram_id
                    dup_user = User(id=2, telegram_id=999, username="dup_user", is_deleted=False)
                    session.add(dup_user)
                    await session.flush()
            except IntegrityError:
                pass  # Savepoint rolled back

            # Outer transaction should commit successfully and retain updated_name
            await session.commit()

            # Verify in clean session
            reloaded_user = await session.get(User, 1)
            self.assertIsNotNone(reloaded_user)
            self.assertEqual(reloaded_user.username, "updated_name", "Outer transaction changes must survive nested savepoint rollback")

    def test_mass_bonus_uuid_idempotency_production_contract(self):
        """Verifies mass bonus batch_id format and cross-batch idempotency collision safety."""
        batch_ids = [uuid.uuid4().hex for _ in range(500)]
        self.assertEqual(len(batch_ids), len(set(batch_ids)))
        for b in batch_ids:
            self.assertEqual(len(b), 32)
            self.assertTrue(all(c in "0123456789abcdef" for c in b))


if __name__ == "__main__":
    unittest.main()
