import os
import unittest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import Server
from database.repositories.servers_repo import update_server, update_server_health_snapshot


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not set")
class ServerMonitorConcurrencyPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
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

    async def test_admin_disable_always_overrides_in_flight_monitor_check_on_postgres(self):
        async with self.sessions.begin() as s:
            server = Server(
                name="Node PG-1",
                api_url="http://127.0.0.1:8443",
                api_key="secret",
                is_active=True,
                health_state="ONLINE",
            )
            s.add(server)
            await s.flush()
            server_id = server.id

        # 1. Transaction A (Admin) disables the server
        async with self.sessions.begin() as s_admin:
            db_admin_server = await s_admin.get(Server, server_id)
            await update_server(
                s_admin,
                db_admin_server,
                is_active=False,
                disabled_reason="MANUAL",
                health_state="MANUAL_DISABLED",
            )

        # 2. Transaction B (Monitor) attempts health snapshot update using expected_health_state="ONLINE"
        async with self.sessions.begin() as s_monitor:
            current, applied = await update_server_health_snapshot(
                s_monitor,
                server_id,
                expected_health_state="ONLINE",
                new_health_state="PROBLEM",
                consecutive_fails=2,
            )

        # Verify Monitor write was rejected and Admin disable persisted cleanly
        self.assertFalse(applied)
        self.assertEqual(current.health_state, "MANUAL_DISABLED")
        self.assertFalse(current.is_active)

        # Verify final state in database
        async with self.sessions() as s_check:
            final_server = await s_check.get(Server, server_id)
            self.assertFalse(final_server.is_active)
            self.assertEqual(final_server.disabled_reason, "MANUAL")
            self.assertEqual(final_server.health_state, "MANUAL_DISABLED")

    async def test_concurrent_monitor_updates_enforce_cas_on_postgres(self):
        async with self.sessions.begin() as s:
            server = Server(
                name="Node PG-2",
                api_url="http://127.0.0.1:8444",
                api_key="secret",
                is_active=True,
                health_state="ONLINE",
            )
            s.add(server)
            await s.flush()
            server_id = server.id

        # Monitor 1 moves state from ONLINE -> PROBLEM
        async with self.sessions.begin() as s1:
            curr1, app1 = await update_server_health_snapshot(
                s1,
                server_id,
                expected_health_state="ONLINE",
                new_health_state="PROBLEM",
                consecutive_fails=2,
            )
        self.assertTrue(app1)
        self.assertEqual(curr1.health_state, "PROBLEM")

        # Monitor 2 (which held snapshot expected_health_state="ONLINE") attempts update
        async with self.sessions.begin() as s2:
            curr2, app2 = await update_server_health_snapshot(
                s2,
                server_id,
                expected_health_state="ONLINE",  # Stale! DB is now PROBLEM
                new_health_state="ONLINE",
                consecutive_fails=0,
            )
        self.assertFalse(app2)
        self.assertEqual(curr2.health_state, "PROBLEM")


if __name__ == "__main__":
    unittest.main()
