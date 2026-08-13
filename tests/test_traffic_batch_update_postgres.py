"""PostgreSQL integration tests for batch traffic update."""

import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


from database.models import Server, User, VPNProfile
from services.amnezia_client import AmneziaClientListItem, AmneziaClientTraffic
from services.workers.traffic import _process_server_traffic


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not set")
class TrafficBatchUpdatePostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings_patcher = patch(
            "config.settings.get_settings",
            return_value=SimpleNamespace(
                DB_ENCRYPTION_KEY=os.getenv(
                    "DB_ENCRYPTION_KEY", "CpVTtwjMHfR3GI2GQqg4P7JZnBHkQCINIQrb4N77hsg="
                ),
                ADMIN_IDS=[12345],
            ),
        )
        self.settings_patcher.start()

        self.engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions.begin() as s:
            await s.execute(
                text(
                    "TRUNCATE account_balance_reservations, "
                    "account_ledger_allocations, account_ledger_entries, "
                    "entitlement_entries, paid_value_ledger, "
                    "tariff_quotes, tariff_versions, payments, api_operations, vpn_profiles, users, servers "
                    "RESTART IDENTITY CASCADE"
                )
            )

    async def asyncTearDown(self):
        async with self.sessions.begin() as s:
            await s.execute(
                text(
                    "TRUNCATE account_balance_reservations, "
                    "account_ledger_allocations, account_ledger_entries, "
                    "entitlement_entries, paid_value_ledger, "
                    "tariff_quotes, tariff_versions, payments, api_operations, vpn_profiles, users, servers "
                    "RESTART IDENTITY CASCADE"
                )
            )
        await self.engine.dispose()
        self.settings_patcher.stop()

    async def test_bulk_update_direct_execution_on_postgres(self):
        """Verify that SQLAlchemy 2.0 ORM bulk update by PK works seamlessly on live PostgreSQL."""
        async with self.sessions() as session:
            u = User(telegram_id=888111)
            s = Server(
                name="srv_batch",
                api_url="http://127.0.0.1:9999",
                api_key="key123",
                protocol="amneziawg2",
            )
            session.add_all([u, s])
            await session.flush()

            p1 = VPNProfile(
                user_id=u.id,
                server_id=s.id,
                device_name="device_1",
                is_active=True,
                traffic_down=0,
                traffic_up=0,
            )
            p2 = VPNProfile(
                user_id=u.id,
                server_id=s.id,
                device_name="device_2",
                is_active=True,
                traffic_down=0,
                traffic_up=0,
            )
            session.add_all([p1, p2])
            await session.flush()
            p1_id, p2_id = p1.id, p2.id

            now = datetime.now(timezone.utc)
            bulk_params = [
                {
                    "id": p1_id,
                    "traffic_down": 1048576,
                    "traffic_up": 2097152,
                    "last_connected": now,
                },
                {
                    "id": p2_id,
                    "traffic_down": 5242880,
                    "traffic_up": 10485760,
                    "last_connected": now,
                },
            ]

            await session.execute(update(VPNProfile), bulk_params)
            await session.commit()

        async with self.sessions() as session:
            p1_db = await session.get(VPNProfile, p1_id)
            p2_db = await session.get(VPNProfile, p2_id)

            self.assertEqual(p1_db.traffic_down, 1048576)
            self.assertEqual(p1_db.traffic_up, 2097152)
            self.assertEqual(p2_db.traffic_down, 5242880)
            self.assertEqual(p2_db.traffic_up, 10485760)

    async def test_process_server_traffic_batch_updates_on_postgres(self):
        """Verify that _process_server_traffic updates live profiles using the batch execution path."""
        async with self.sessions() as session:
            u = User(telegram_id=888222, subscription_end=datetime(2030, 1, 1, tzinfo=timezone.utc))
            srv = Server(
                name="node_traffic",
                api_url="http://127.0.0.1:9998",
                api_key="key998",
                protocol="amneziawg2",
            )
            session.add_all([u, srv])
            await session.flush()

            p1 = VPNProfile(
                user_id=u.id,
                server_id=srv.id,
                peer_id="peer_abc_1",
                device_name="phone",
                is_active=True,
                traffic_down=0,
                traffic_up=0,
            )
            p2 = VPNProfile(
                user_id=u.id,
                server_id=srv.id,
                peer_id="peer_abc_2",
                device_name="laptop",
                is_active=True,
                traffic_down=0,
                traffic_up=0,
            )
            session.add_all([p1, p2])
            await session.flush()
            p1_id, p2_id = p1.id, p2.id
            await session.commit()

        # Mock Amnezia clients response
        fake_clients = [
            AmneziaClientListItem(
                id="peer_abc_1",
                username="u1",
                peer_name="p1",
                status="active",
                traffics=AmneziaClientTraffic(totalDownload=1500, totalUpload=2500),
                lastHandshake=1700000000,
            ),
            AmneziaClientListItem(
                id="peer_abc_2",
                username="u2",
                peer_name="p2",
                status="active",
                traffics=AmneziaClientTraffic(totalDownload=3500, totalUpload=4500),
                lastHandshake=1700000500,
            ),
        ]

        api_clients_dict = {c.id: c for c in fake_clients}

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def mock_scope():
            async with self.sessions() as s:
                yield s
                await s.commit()

        with patch("services.workers.traffic.session_scope", mock_scope):
            server_info = {
                "id": srv.id,
                "api_url": "http://127.0.0.1:9998",
                "api_key": "key998",
                "name": "node_traffic",
            }
            await _process_server_traffic(server_info, api_clients_dict)


        async with self.sessions() as session:
            p1_db = await session.get(VPNProfile, p1_id)
            p2_db = await session.get(VPNProfile, p2_id)

            self.assertEqual(p1_db.traffic_down, 1500)
            self.assertEqual(p1_db.traffic_up, 2500)
            self.assertEqual(p2_db.traffic_down, 3500)
            self.assertEqual(p2_db.traffic_up, 4500)
            self.assertIsNotNone(p1_db.last_connected)
            self.assertIsNotNone(p2_db.last_connected)


if __name__ == "__main__":
    unittest.main()
