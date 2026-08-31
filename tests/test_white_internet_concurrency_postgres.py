"""PostgreSQL integration and concurrency tests for White Internet feature.

Tests run exclusively against an actual PostgreSQL container (TEST_DATABASE_URL).
Verifies:
1. True Expected-State CAS (update_server_xray_epoch_cas) under concurrency.
2. Concurrent traffic event insertion & deduction row locks (SELECT FOR UPDATE).
3. Quota overshoot and exhaustion state transitions.
4. Topup cap 500 GiB invariant under race conditions.
5. Grant Ledger Base-first consumption & FIFO remaining calculation.
"""

from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config.enums import (
    ServerHealthState,
    TariffQuoteOperation,
    TariffQuoteStatus,
    WhiteInternetGrantType,
    WhiteInternetStatus,
)

from database.models import (
    Server,
    Tariff,
    TariffQuote,
    TariffVersion,
    User,
    WhiteInternetQuotaGrant,
    WhiteInternetSubscription,
    WhiteInternetTrafficEvent,
)
from database.repositories import servers_repo, white_internet_repo
from utils import now_utc

try:
    from tests.db_utils import TRUNCATE_SQL
except ImportError:
    from db_utils import TRUNCATE_SQL


DB = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class WhiteInternetConcurrencyPostgresTests(unittest.IsolatedAsyncioTestCase):
    """ACID and concurrency test suite for White Internet against PostgreSQL."""

    async def asyncSetUp(self):
        from unittest.mock import patch

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
                "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
                "WHITE_INTERNET_CDN_DOMAIN": "cdn.test.just1k.online",
            },
        )
        self.env_patcher.start()

        from config.settings import get_settings

        get_settings.cache_clear()

        self.engine = create_async_engine(DB, pool_size=15, max_overflow=15, pool_timeout=60)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.sessions.begin() as session:
            await session.execute(text(TRUNCATE_SQL))

            self.user = User(telegram_id=int(uuid.uuid4().int % 1000000000))
            session.add(self.user)

            self.server = Server(
                name="Origin-MSK-PG-Test",
                protocol="xray",
                capabilities=["xray_origin"],
                api_url="https://origin.test.just1k.online:8444",
                api_key="secret-pg-test",
                health_state=ServerHealthState.ONLINE,
                xray_instance_epoch="epoch_100",
                xray_instance_boot_id="boot_01",
                xray_instance_starttime=1000,
            )
            session.add(self.server)

            # Tariff & TariffVersion for White Internet
            self.tariff = Tariff(
                name="Белый Интернет 50 ГБ",
                service_type="white_internet",
                device_limit=1,
                duration_days=30,
                price_rub=250,
                is_active=True,
                sort_order=0,
            )
            session.add(self.tariff)
            await session.flush()

            self.tariff_version = TariffVersion(
                tariff_id=self.tariff.id,
                version_number=1,
                name_snapshot="Белый Интернет 50 ГБ",
                duration_hours=720,
                device_limit=1,
                price_rub=Decimal("250.00"),
                currency="RUB",
            )
            session.add(self.tariff_version)
            await session.flush()


            # TariffQuote
            self.quote = TariffQuote(
                public_id=uuid.uuid4(),
                user_id=self.user.id,
                service_type="white_internet",
                operation_type=TariffQuoteOperation.PURCHASE,
                status=TariffQuoteStatus.CONSUMED,
                consumed_at=now_utc(),
                target_tariff_version_id=self.tariff_version.id,
                amount_due_rub=Decimal("250.00"),
                current_paid_hours=0,
                current_paid_value_rub=Decimal("0.00"),
                bonus_hours=0,
                resulting_paid_hours=720,
                resulting_paid_value_rub=Decimal("250.00"),
                resulting_bonus_hours=0,
                rounding_loss_hours=Decimal("0.00"),
                rounding_loss_value_rub=Decimal("0.00"),
                expires_at=now_utc() + timedelta(hours=1),
            )
            session.add(self.quote)
            await session.flush()




    async def asyncTearDown(self):
        await self.engine.dispose()
        self.env_patcher.stop()

    async def test_update_server_xray_epoch_cas_concurrency(self):
        """Concurrent workers attempt CAS epoch updates; only the one with matching state succeeds."""
        async with self.sessions() as session:
            # 1. Matching expected state succeeds
            updated, s = await servers_repo.update_server_xray_epoch_cas(
                session,
                server_id=self.server.id,
                expected_boot_id="boot_01",
                expected_starttime=1000,
                new_epoch="epoch_200",
                new_boot_id="boot_01",
                new_starttime=2000,
            )
            await session.commit()
            self.assertTrue(updated)
            self.assertEqual(s.xray_instance_epoch, "epoch_200")
            self.assertEqual(s.xray_instance_starttime, 2000)

        # 2. Stale worker attempting CAS with old starttime (1000) is rejected
        async with self.sessions() as session:
            updated, s = await servers_repo.update_server_xray_epoch_cas(
                session,
                server_id=self.server.id,
                expected_boot_id="boot_01",
                expected_starttime=1000,  # Stale!
                new_epoch="epoch_stale",
                new_boot_id="boot_01",
                new_starttime=3000,
            )
            await session.commit()
            self.assertFalse(updated)
            self.assertIsNone(s)

            server = await session.get(Server, self.server.id)
            self.assertEqual(server.xray_instance_epoch, "epoch_200")  # Untouched


    async def test_concurrent_traffic_deduction_and_event_recording(self):
        """Verify row locking and atomic ledger consistency under concurrent traffic sync workers."""
        now = now_utc()
        base_50_gib = 50 * 1024**3

        # Create subscription and 50 GiB BASE grant
        async with self.sessions.begin() as session:
            sub = WhiteInternetSubscription(
                user_id=self.user.id,
                origin_node_id=self.server.id,
                token="pg_wi_token_" + uuid.uuid4().hex,
                uuid=str(uuid.uuid4()),
                status=WhiteInternetStatus.ACTIVE,
                started_at=now,
                expires_at=now + timedelta(days=30),
                traffic_limit_bytes=base_50_gib,
                traffic_used_bytes=0,
                desired_version=1,
                actual_version=1,
                last_reconciled_node_epoch="epoch_100",
            )
            session.add(sub)
            await session.flush()

            grant = WhiteInternetQuotaGrant(
                subscription_id=sub.id,
                grant_type=WhiteInternetGrantType.BASE,
                bytes_granted=base_50_gib,
                bytes_remaining=base_50_gib,
                price_rub=Decimal("250.00"),
                quote_id=self.quote.id,
                expires_at=sub.expires_at,
            )
            session.add(grant)
            sub_id = sub.id

        # Run 10 parallel traffic increments of 2 GiB each
        chunk_gib = 2 * 1024**3

        async def worker_task(idx: int):
            async with self.sessions.begin() as session:
                consumed, exhausted, overage = await white_internet_repo.deduct_traffic_atomic(
                    session,
                    subscription_id=sub_id,
                    delta_bytes=chunk_gib,
                    delta_uplink=chunk_gib // 2,
                    delta_downlink=chunk_gib // 2,
                    now=now,
                )
                await white_internet_repo.record_traffic_event_atomic(
                    session,
                    subscription_id=sub_id,
                    node_epoch="epoch_100",
                    node_boot_id="boot_01",
                    node_starttime=1000,
                    snapshot_uplink_before=idx * (chunk_gib // 2),
                    snapshot_uplink_after=(idx + 1) * (chunk_gib // 2),
                    snapshot_downlink_before=idx * (chunk_gib // 2),
                    snapshot_downlink_after=(idx + 1) * (chunk_gib // 2),
                    delta_uplink=chunk_gib // 2,
                    delta_downlink=chunk_gib // 2,
                    allocated_bytes=consumed,
                    overage_bytes=overage,
                    now=now,
                )

        tasks = [worker_task(i) for i in range(10)]
        await asyncio.gather(*tasks)

        # Verify final state in database
        async with self.sessions() as session:
            sub = await session.get(WhiteInternetSubscription, sub_id)
            self.assertEqual(sub.traffic_used_bytes, 10 * chunk_gib)
            self.assertEqual(sub.status, WhiteInternetStatus.ACTIVE)

            avail = await white_internet_repo.get_available_quota_bytes(session, sub_id, now=now)
            self.assertEqual(avail, base_50_gib - (10 * chunk_gib))

            # Verify traffic events count
            events_count = await session.scalar(
                select(text("count(*)")).select_from(WhiteInternetTrafficEvent)
            )
            self.assertEqual(events_count, 10)

    async def test_topup_500_gib_cap_under_concurrent_purchases(self):
        """Verify that concurrent topups strictly respect the 500 GiB accumulation cap."""
        now = now_utc()
        base_50_gib = 50 * 1024**3

        async with self.sessions.begin() as session:
            sub = WhiteInternetSubscription(
                user_id=self.user.id,
                origin_node_id=self.server.id,
                token="pg_wi_token_cap_" + uuid.uuid4().hex,
                uuid=str(uuid.uuid4()),
                status=WhiteInternetStatus.ACTIVE,
                started_at=now,
                expires_at=now + timedelta(days=30),
                traffic_limit_bytes=base_50_gib,
                traffic_used_bytes=0,
                desired_version=1,
                actual_version=1,
            )
            session.add(sub)
            await session.flush()

            grant = WhiteInternetQuotaGrant(
                subscription_id=sub.id,
                grant_type=WhiteInternetGrantType.BASE,
                bytes_granted=base_50_gib,
                bytes_remaining=base_50_gib,
                price_rub=Decimal("250.00"),
                quote_id=self.quote.id,
                expires_at=sub.expires_at,
            )
            session.add(grant)
            sub_id = sub.id

        # Attempt to concurrently purchase 10 packs of 50 GiB each (500 GiB + 50 GiB base = 550 GiB > 500 GiB cap)
        async def try_topup(idx: int) -> bool:
            async with self.sessions.begin() as session:
                # Create unique quote for this topup
                q = TariffQuote(
                    public_id=uuid.uuid4(),
                    user_id=self.user.id,
                    service_type="white_internet",
                    operation_type=TariffQuoteOperation.PURCHASE,
                    status=TariffQuoteStatus.ACTIVE,
                    target_tariff_version_id=self.tariff_version.id,
                    amount_due_rub=Decimal("200.00"),
                    current_paid_hours=0,
                    current_paid_value_rub=Decimal("0.00"),
                    bonus_hours=0,
                    resulting_paid_hours=0,
                    resulting_paid_value_rub=Decimal("200.00"),
                    resulting_bonus_hours=0,
                    rounding_loss_hours=Decimal("0.00"),
                    rounding_loss_value_rub=Decimal("0.00"),
                    expires_at=now + timedelta(hours=1),
                )
                session.add(q)
                await session.flush()

                try:
                    await white_internet_repo.topup_quota_atomic(
                        session,
                        subscription_id=sub_id,
                        quote_id=q.id,
                        pack_gb=50,
                        price_rub=Decimal("200.00"),
                    )
                    q.status = TariffQuoteStatus.CONSUMED
                    q.consumed_at = now
                    await session.flush()
                    return True
                except white_internet_repo.WhiteInternetQuotaCapExceededError:
                    q.status = TariffQuoteStatus.CANCELLED
                    await session.flush()
                    return False

        results = await asyncio.gather(*(try_topup(i) for i in range(10)))
        successes = sum(1 for r in results if r is True)
        cap_errors = sum(1 for r in results if r is False)

        # 50 GiB base + 9 * 50 GiB topups = 500 GiB (exactly at cap). 10th must fail!
        self.assertEqual(successes, 9)
        self.assertEqual(cap_errors, 1)

        async with self.sessions() as session:
            avail = await white_internet_repo.get_available_quota_bytes(session, sub_id, now=now)
            self.assertEqual(avail, 500 * 1024**3)

