import os
import unittest

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import Server
from database.repositories.servers_repo import (
    update_server,
    update_server_health_snapshot,
)

try:
    from tests.db_utils import TRUNCATE_SQL
except ImportError:  # direct unittest discover run
    from db_utils import TRUNCATE_SQL



@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not set")
class ServerMonitorConcurrencyPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions.begin() as s:
            await s.execute(text(TRUNCATE_SQL))

    async def asyncTearDown(self):
        async with self.sessions.begin() as s:
            await s.execute(text(TRUNCATE_SQL))
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

    async def test_concurrent_blocking_transactions_on_postgres(self):
        import asyncio

        from sqlalchemy import select

        async with self.sessions.begin() as s:
            server = Server(
                name="Node PG-3",
                api_url="http://127.0.0.1:8445",
                api_key="secret",
                is_active=True,
                health_state="ONLINE",
                consecutive_fails=0,
            )
            s.add(server)
            await s.flush()
            server_id = server.id

        admin_locked = asyncio.Event()
        admin_can_commit = asyncio.Event()
        monitor_result = {}

        async def admin_task():
            async with self.sessions.begin() as s_admin:
                srv = (
                    await s_admin.execute(
                        select(Server).where(Server.id == server_id).with_for_update()
                    )
                ).scalar_one()
                srv.is_active = False
                srv.health_state = "MANUAL_DISABLED"
                srv.disabled_reason = "MANUAL"
                await s_admin.flush()

                admin_locked.set()
                await admin_can_commit.wait()

        async def monitor_task():
            await admin_locked.wait()
            async with self.sessions.begin() as s_mon:
                admin_can_commit.set()
                curr, applied = await update_server_health_snapshot(
                    s_mon,
                    server_id,
                    expected_health_state="ONLINE",
                    expected_consecutive_fails=0,
                    new_health_state="PROBLEM",
                    consecutive_fails=1,
                )
                monitor_result["current"] = curr
                monitor_result["applied"] = applied

        await asyncio.gather(admin_task(), monitor_task())

        self.assertFalse(monitor_result["applied"])
        self.assertEqual(monitor_result["current"].health_state, "MANUAL_DISABLED")
        self.assertFalse(monitor_result["current"].is_active)

    async def test_monitor_vs_manual_enable_on_postgres(self):
        async with self.sessions.begin() as s:
            server = Server(
                name="Node PG-4",
                api_url="http://127.0.0.1:8446",
                api_key="secret",
                is_active=False,
                disabled_reason="AUTO_UNAVAILABLE",
                health_state="AUTO_DISABLED",
                consecutive_fails=5,
            )
            s.add(server)
            await s.flush()
            server_id = server.id

        # 1. Admin re-enables server in DB
        async with self.sessions.begin() as s_admin:
            srv = await s_admin.get(Server, server_id)
            await update_server(
                s_admin,
                srv,
                is_active=True,
                disabled_reason=None,
                disabled_at=None,
                health_state="ONLINE",
                consecutive_fails=0,
                consecutive_successes=0,
            )

        # 2. First monitor healthcheck post-enable expected_health_state="ONLINE", expected_consecutive_fails=0
        async with self.sessions.begin() as s_mon:
            curr, applied = await update_server_health_snapshot(
                s_mon,
                server_id,
                expected_health_state="ONLINE",
                expected_consecutive_fails=0,
                expected_consecutive_successes=0,
                new_health_state="ONLINE",
                consecutive_successes=1,
            )

        self.assertTrue(applied)
        self.assertTrue(curr.is_active)
        self.assertEqual(curr.health_state, "ONLINE")
        self.assertEqual(curr.consecutive_successes, 1)

    async def test_rollback_and_retry_on_postgres(self):
        async with self.sessions.begin() as s:
            server = Server(
                name="Node PG-5",
                api_url="http://127.0.0.1:8447",
                api_key="secret",
                is_active=True,
                health_state="ONLINE",
                consecutive_fails=0,
            )
            s.add(server)
            await s.flush()
            server_id = server.id

        # Transaction 1: Aborts due to simulated exception after lock
        try:
            async with self.sessions.begin() as s_err:
                curr, applied = await update_server_health_snapshot(
                    s_err,
                    server_id,
                    expected_health_state="ONLINE",
                    new_health_state="WAITING_CONFIRMATION",
                    consecutive_fails=1,
                )
                raise RuntimeError("Simulated network/DB transient crash after lock")
        except RuntimeError:
            pass

        # Verify DB remained in ONLINE state after rollback
        async with self.sessions() as s_check:
            srv_check = await s_check.get(Server, server_id)
            self.assertEqual(srv_check.health_state, "ONLINE")
            self.assertEqual(srv_check.consecutive_fails, 0)

        # Transaction 2: Retry succeeds cleanly
        async with self.sessions.begin() as s_retry:
            curr_retry, applied_retry = await update_server_health_snapshot(
                s_retry,
                server_id,
                expected_health_state="ONLINE",
                expected_consecutive_fails=0,
                new_health_state="WAITING_CONFIRMATION",
                consecutive_fails=1,
            )

        self.assertTrue(applied_retry)
        self.assertEqual(curr_retry.health_state, "WAITING_CONFIRMATION")


if __name__ == "__main__":
    unittest.main()
