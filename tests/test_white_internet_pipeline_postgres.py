"""True PostgreSQL End-to-End Pipeline Integration Test for White Internet.

Tests real database transactions, real ledger debit, real state transitions,
real reconciliation worker, real traffic worker, quota exhaustion, real top-up,
and renewal against PostgreSQL (TEST_DATABASE_URL).
"""

from __future__ import annotations

import os
import unittest
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config.constants import WHITE_INTERNET_BASE_PRICE_RUB, WHITE_INTERNET_BASE_TRAFFIC_BYTES
from config.enums import (
    ServerHealthState,
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from database.models import (
    Server,
    Tariff,
    TariffVersion,
    User,
)
from database.repositories import white_internet_repo
from services.white_internet_service import WhiteInternetService
from services.workers.white_internet_reconciliation import WhiteInternetReconciliationWorker
from services.workers.white_internet_traffic import WhiteInternetTrafficWorker
from services.xray_node_client import XrayNodeClient

try:
    from tests.db_utils import TRUNCATE_SQL
except ImportError:
    from db_utils import TRUNCATE_SQL

DB = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class WhiteInternetPostgresPipelineTests(unittest.IsolatedAsyncioTestCase):
    """Full lifecycle testing against a real PostgreSQL database instance."""

    async def asyncSetUp(self):
        self.env_patcher = patch.dict(
            os.environ,
            {
                "BOT_TOKEN": "123456:TEST_TOKEN",
                "REDIS_URL": "redis://localhost:6379/1",
                "REDIS_PASSWORD": "test",
                "ADMIN_IDS": "[123456789]",
                "SUPPORT_USERNAME": "test_support",
                "DOMAIN": "test.vpn.online",
                "SSL_EMAIL": "test@domain.com",
                "YOOKASSA_SHOP_ID": "123456",
                "YOOKASSA_SECRET_KEY": "test_secret",
                "YOOKASSA_RETURN_URL": "https://t.me/{bot_username}",
                "YOOKASSA_WEBHOOK_PORT": "8080",
                "DB_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
                "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
                "WHITE_INTERNET_CDN_DOMAIN": "cdn.test.vpn.online",
            },
        )
        self.env_patcher.start()

        from config.settings import get_settings
        get_settings.cache_clear()

        self.engine = create_async_engine(DB, pool_size=5, max_overflow=5)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.sessions.begin() as session:
            await session.execute(text(TRUNCATE_SQL))

            self.user = User(telegram_id=int(uuid.uuid4().int % 1000000000))
            session.add(self.user)
            await session.flush()

            from database.repositories.account_ledger_repo import create_admin_adjustment

            # Credit user balance with 1000 RUB using real ledger transaction
            await create_admin_adjustment(
                session,
                user_id=self.user.id,
                signed_amount=1000,
                idempotency_key=str(uuid.uuid4()),
                metadata={"reason": "test setup"},
            )

            self.server = Server(
                name="Origin-MSK-Pipeline",
                protocol="xray",
                capabilities=["xray_origin"],
                api_url="https://origin.test.vpn.online:8444",
                api_key="secret-key-123",
                health_state=ServerHealthState.ONLINE,
                is_active=True,
                xray_instance_epoch="epoch_pipe_100",
                xray_instance_boot_id="boot_pipe_01",
                xray_instance_starttime=1000,
            )
            session.add(self.server)

            # Tariff & TariffVersion for White Internet
            self.tariff = Tariff(
                name="Белый Интернет 50 ГБ",
                service_type="white_internet",
                device_limit=1,
                duration_days=30,
                price_rub=int(WHITE_INTERNET_BASE_PRICE_RUB),
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
                price_rub=Decimal(str(WHITE_INTERNET_BASE_PRICE_RUB)),
                currency="RUB",
            )
            session.add(self.tariff_version)
            await session.flush()

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.env_patcher.stop()

    async def test_full_pipeline_against_real_postgres(self):
        """Execute complete lifecycle: Purchase -> Recon -> Traffic -> Exhaust -> Topup -> Renew."""
        node_epoch = "epoch_pipe_100"

        # 1. Purchase White Internet Subscription via real WhiteInternetService
        async with self.sessions.begin() as session:
            ok, msg, sub = await WhiteInternetService.purchase_subscription(
                session=session,
                user_id=self.user.id,
            )
            self.assertTrue(ok, msg)
            self.assertIsNotNone(sub)
            self.assertEqual(sub.status, WhiteInternetStatus.PENDING)
            self.assertEqual(sub.provisioning_status, WhiteInternetProvisioningStatus.PENDING_CREATE)
            self.assertEqual(sub.desired_version, 1)
            self.assertEqual(sub.actual_version, 0)
            sub_id = sub.id
            sub_uuid = sub.uuid

        # 2. Run Reconciliation Worker: sync with Xray Node
        mock_client = AsyncMock(spec=XrayNodeClient)
        mock_client.check_health.return_value = (
            True,
            node_epoch,
            {"status": "ok", "grpc_ok": True, "xray_running": True, "boot_id": "boot_pipe_01", "starttime": 1000},
        )
        mock_client.sync_client.return_value = (True, None)

        recon_worker = WhiteInternetReconciliationWorker(node_client=mock_client)
        async with self.sessions.begin() as session:
            synced = await recon_worker.run_reconciliation_cycle(session)
            self.assertEqual(synced, 1)

        # Verify DB state after reconciliation: ACTIVE, version=1, aligned epoch
        async with self.sessions.begin() as session:
            sub = await white_internet_repo.get_subscription_by_id(session, sub_id)
            self.assertEqual(sub.status, WhiteInternetStatus.ACTIVE)
            self.assertEqual(sub.provisioning_status, WhiteInternetProvisioningStatus.ACTIVE)
            self.assertEqual(sub.actual_version, 1)
            self.assertEqual(sub.last_reconciled_node_epoch, node_epoch)

        # 3. Traffic Worker consumes 20 GB (delta: 5 GB up, 15 GB down)
        up_1 = 5 * 1024**3
        down_1 = 15 * 1024**3
        mock_client.get_traffic_snapshot.return_value = (
            node_epoch,
            "boot_pipe_01",
            1000,
            {sub_uuid: {"uplink": up_1, "downlink": down_1}},
        )

        traffic_worker = WhiteInternetTrafficWorker(node_client=mock_client)
        async with self.sessions.begin() as session:
            processed = await traffic_worker.run_traffic_cycle(session)
            self.assertEqual(processed, 1)

        async with self.sessions.begin() as session:
            sub = await white_internet_repo.get_subscription_by_id(session, sub_id)
            self.assertEqual(sub.traffic_used_bytes, up_1 + down_1)
            self.assertEqual(sub.traffic_uplink_bytes, up_1)
            self.assertEqual(sub.traffic_downlink_bytes, down_1)
            available = await white_internet_repo.get_available_quota_bytes(session, sub.id)
            self.assertEqual(available, WHITE_INTERNET_BASE_TRAFFIC_BYTES - (up_1 + down_1))

        # 4. Traffic Worker consumes remaining 30 GB + 2 GB overage -> Quota Exhaustion
        up_2 = 10 * 1024**3
        down_2 = 42 * 1024**3  # Total 52 GB > 50 GB
        mock_client.get_traffic_snapshot.return_value = (
            node_epoch,
            "boot_pipe_01",
            1000,
            {sub_uuid: {"uplink": up_2, "downlink": down_2}},
        )
        async with self.sessions.begin() as session:
            processed = await traffic_worker.run_traffic_cycle(session)
            self.assertEqual(processed, 1)

        async with self.sessions.begin() as session:
            sub = await white_internet_repo.get_subscription_by_id(session, sub_id)
            self.assertEqual(sub.status, WhiteInternetStatus.EXHAUSTED)
            self.assertEqual(sub.desired_version, 2)
            available = await white_internet_repo.get_available_quota_bytes(session, sub.id)
            self.assertEqual(available, 0)

        # 5. Reconciliation de-provisions exhausted user from Xray
        mock_client.sync_client.reset_mock()
        mock_client.sync_client.return_value = (True, None)
        async with self.sessions.begin() as session:
            synced = await recon_worker.run_reconciliation_cycle(session)
            self.assertEqual(synced, 1)
            mock_client.sync_client.assert_awaited_once_with(
                self.server.api_url,
                self.server.api_key,
                sub_uuid,
                is_active=False,
            )

        async with self.sessions.begin() as session:
            sub = await white_internet_repo.get_subscription_by_id(session, sub_id)
            self.assertEqual(sub.actual_version, 2)
            self.assertEqual(sub.provisioning_status, WhiteInternetProvisioningStatus.SYNCED_INACTIVE)

        # 6. User purchases a real 25 GB top-up pack -> Re-activates subscription
        async with self.sessions.begin() as session:
            ok, msg, grant = await WhiteInternetService.topup_quota(
                session=session,
                user_id=self.user.id,
                pack_gb=25,
            )
            self.assertTrue(ok, msg)
            self.assertIsNotNone(grant)

        async with self.sessions.begin() as session:
            topup_sub = await white_internet_repo.get_subscription_by_id(session, sub_id)
            self.assertEqual(topup_sub.status, WhiteInternetStatus.ACTIVE)
            self.assertEqual(topup_sub.desired_version, 3)

        # 7. Reconciliation re-enables user on Xray node
        mock_client.sync_client.reset_mock()
        mock_client.sync_client.return_value = (True, None)
        async with self.sessions.begin() as session:
            synced = await recon_worker.run_reconciliation_cycle(session)
            self.assertEqual(synced, 1)
            mock_client.sync_client.assert_awaited_once_with(
                self.server.api_url,
                self.server.api_key,
                sub_uuid,
                is_active=True,
            )

        async with self.sessions.begin() as session:
            sub = await white_internet_repo.get_subscription_by_id(session, sub_id)
            self.assertEqual(sub.actual_version, 3)
            self.assertEqual(sub.provisioning_status, WhiteInternetProvisioningStatus.ACTIVE)

        # 8. Renewal: resets current period usage while carrying active topup grant
        async with self.sessions.begin() as session:
            ok, msg, renewed_sub = await WhiteInternetService.renew_subscription(
                session=session,
                user_id=self.user.id,
            )
            self.assertTrue(ok, msg)
            self.assertIsNotNone(renewed_sub)
            self.assertEqual(renewed_sub.traffic_used_bytes, 0)
            self.assertEqual(renewed_sub.traffic_uplink_bytes, 0)
            self.assertEqual(renewed_sub.traffic_downlink_bytes, 0)
            # Baseline is locked to current Xray counters
            self.assertEqual(renewed_sub.last_uplink_snapshot, up_2)
            self.assertEqual(renewed_sub.last_downlink_snapshot, down_2)
            # Available bytes includes fresh 50 GiB base + carried 25 GiB topup
            available = await white_internet_repo.get_available_quota_bytes(session, sub_id)
            self.assertEqual(available, (50 + 25) * 1024**3)
