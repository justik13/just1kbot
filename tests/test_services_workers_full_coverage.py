import datetime
import os
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import User, Server, VPNProfile
from services.workers import (
    notifications,
    cleanup,
    heartbeat,
    queue_health,
)
from services.payment_queue_health import QueueSnapshot, PaymentQueueHealthSnapshot

DB = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class ServicesWorkersFullCoverageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(DB)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.sessions.begin() as session:
            await session.execute(
                text(
                    "TRUNCATE account_balance_reservations, "
                    "account_ledger_allocations, account_ledger_entries, "
                    "entitlement_entries, paid_value_ledger, "
                    "tariff_quotes, tariff_versions, payments, vpn_profiles, "
                    "maintenance_mode, audit_logs, hub_messages, users, tariffs, servers, system_settings "
                    "RESTART IDENTITY CASCADE"

                )
            )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_notifications_helpers(self):
        # Countdown formatting helper
        d1 = datetime.timedelta(days=2, hours=3)
        fmt1 = notifications._format_countdown(d1)
        self.assertIn("2", fmt1)

        d2 = datetime.timedelta(seconds=-10)
        fmt2 = notifications._format_countdown(d2)
        self.assertIsNotNone(fmt2)

        # Backoff calculation
        delay = notifications._get_backoff_delay(0)
        self.assertTrue(delay > 0)

    async def test_notifications_worker_single_step(self):
        bot = AsyncMock()
        async with self.sessions.begin() as session:
            now = datetime.datetime.now(datetime.timezone.utc)
            u = User(
                telegram_id=999111222,
                subscription_end=now + datetime.timedelta(hours=1),
                notified_2h=False,
            )
            session.add(u)
            await session.flush()

        current_time = datetime.datetime.now(datetime.timezone.utc)
        async with self.sessions.begin() as s2:
            with patch("services.workers.notifications.session_scope") as mock_scope:
                mock_scope.return_value.__aenter__.return_value = s2
                await notifications._send_pre_expiry_notifications(bot, current_time)

    async def test_cleanup_worker_single_step(self):
        async with self.sessions.begin() as session:
            now = datetime.datetime.now(datetime.timezone.utc)
            u = User(telegram_id=888111, subscription_end=now - datetime.timedelta(days=10))
            s = Server(name="CleanupS", api_url="https://cleanup.test", api_key="k")
            session.add_all((u, s))
            await session.flush()

            p = VPNProfile(user_id=u.id, server_id=s.id, device_name="OldKey", peer_id="peer1", raw_config="ss://old")
            session.add(p)
            await session.flush()

        async with self.sessions.begin() as s2:
            with patch("services.workers.cleanup.session_scope") as mock_scope:
                mock_scope.return_value.__aenter__.return_value = s2
                await cleanup._cleanup_expired_profiles_grace()

    async def test_heartbeat_worker_single_step(self):
        async with self.sessions.begin() as s2:
            with patch("database.connection.session_scope") as mock_scope:
                mock_scope.return_value.__aenter__.return_value = s2
                await heartbeat._check_circuit_breakers()

    async def test_queue_health_monitor_step(self):
        bot = AsyncMock()
        monitor = queue_health.QueueHealthMonitor(bot)
        q_snap = QueueSnapshot(
            name="test_q",
            pending=0,
            retry=0,
            due=0,
            overdue=0,
            processing=0,
            stale_processing=0,
            dead=0,
            oldest_due_age_seconds=None,
            oldest_stale_age_seconds=None,
            oldest_dead_age_seconds=None,
            examples=(),
        )
        snapshot = PaymentQueueHealthSnapshot(observed_at=datetime.datetime.now(datetime.timezone.utc), queues=(q_snap,))
        monitor.observe(snapshot)


if __name__ == "__main__":
    unittest.main()
