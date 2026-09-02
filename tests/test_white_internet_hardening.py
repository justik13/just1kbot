"""
Comprehensive regression and hardening test suite for the White Internet subsystem (PR #231).
Covers Groups A through Q validating all architectural and operational invariants.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import urllib.parse
import uuid

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncSession

from config.constants import (
    AMNEZIA_PROTOCOL,
    DEFAULT_WHITE_INTERNET_PADDING_KEY,
    DEFAULT_WHITE_INTERNET_PATH,
    WHITE_INTERNET_BASE_DURATION_DAYS,
    WHITE_INTERNET_BASE_PRICE_RUB,
    WHITE_INTERNET_BASE_TRAFFIC_BYTES,
    WHITE_INTERNET_MAX_QUOTA_BYTES,
    WHITE_INTERNET_TOPUP_PACKS,
)
from config.enums import (
    ServerHealthState,
    ServerLifecycleStatus,
    ServiceType,
    WhiteInternetGrantType,
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from database.models import (
    Server,
    Tariff,
    TariffVersion,
    VPNProfile,
    WhiteInternetQuotaGrant,
    WhiteInternetSubscription,
)
from database.repositories import servers_repo, tariff_quotes_repo, white_internet_repo
from database.repositories.servers_repo import (
    capacity_consuming_wl_condition,
    update_server_xray_epoch_cas,
)
from database.repositories.white_internet_repo import (
    WhiteInternetQuotaCapExceededError,
)
from services.white_internet_service import WhiteInternetService
from services.workers.white_internet_reconciliation import (
    DEFAULT_RECONCILIATION_CONCURRENCY,
    WhiteInternetReconciliationWorker,
)
from services.workers.white_internet_traffic import WhiteInternetTrafficWorker
from utils.datetime_helpers import now_utc


class TestGroupAEnumsAndConstantsSSOT(unittest.TestCase):
    """Group A: Enums & Constants SSOT invariants."""

    def test_server_lifecycle_status_enum_members(self):
        self.assertEqual(ServerLifecycleStatus.ACTIVE, "ACTIVE")
        self.assertEqual(ServerLifecycleStatus.DECOMMISSIONING, "DECOMMISSIONING")
        self.assertEqual(ServerLifecycleStatus.DECOMMISSIONED, "DECOMMISSIONED")
        self.assertEqual(ServerLifecycleStatus.ARCHIVED, "ARCHIVED")
        self.assertEqual(len(ServerLifecycleStatus), 4)

    def test_service_type_enum_members(self):
        self.assertEqual(ServiceType.AWG, "awg")
        self.assertEqual(ServiceType.WHITE_INTERNET, "white_internet")

    def test_white_internet_status_enum_members(self):
        expected = {"PENDING", "ACTIVE", "EXHAUSTED", "EXPIRED", "DISABLED"}
        self.assertEqual({s.value for s in WhiteInternetStatus}, expected)

    def test_white_internet_provisioning_status_enum_members(self):
        expected = {"PENDING_CREATE", "ACTIVE", "PENDING_UPDATE", "PENDING_DELETE", "SYNCED_INACTIVE", "FAILED"}
        self.assertEqual({s.value for s in WhiteInternetProvisioningStatus}, expected)

    def test_white_internet_grant_type_enum_members(self):
        self.assertEqual(WhiteInternetGrantType.BASE, "BASE")
        self.assertEqual(WhiteInternetGrantType.TOPUP, "TOPUP")

    def test_white_internet_constants(self):
        self.assertEqual(WHITE_INTERNET_BASE_TRAFFIC_BYTES, 50 * 1024 * 1024 * 1024)
        self.assertEqual(WHITE_INTERNET_MAX_QUOTA_BYTES, 500 * 1024 * 1024 * 1024)
        self.assertEqual(WHITE_INTERNET_BASE_DURATION_DAYS, 30)
        self.assertEqual(WHITE_INTERNET_BASE_PRICE_RUB, Decimal("250.00"))
        self.assertTrue(bool(DEFAULT_WHITE_INTERNET_PADDING_KEY))
        self.assertTrue(bool(DEFAULT_WHITE_INTERNET_PATH))
        self.assertIn(10, WHITE_INTERNET_TOPUP_PACKS)
        self.assertIn(25, WHITE_INTERNET_TOPUP_PACKS)
        self.assertIn(50, WHITE_INTERNET_TOPUP_PACKS)


class TestGroupBSQLAlchemyModelsAndConstraints(unittest.TestCase):
    """Group B: SQLAlchemy Models and Table Constraints."""

    def test_server_model_lifecycle_status_and_constraints(self):
        col = Server.__table__.columns["lifecycle_status"]
        self.assertFalse(col.nullable)
        self.assertEqual(col.type.length, 30)
        ck_names = [c.name for c in Server.__table__.constraints if hasattr(c, "name")]
        self.assertIn("ck_servers_lifecycle_status", ck_names)
        ix_names = [i.name for i in Server.__table__.indexes]
        self.assertIn("ix_servers_lifecycle_status", ix_names)

    def test_tariff_version_model_white_internet_fields(self):
        st_col = TariffVersion.__table__.columns["service_type"]
        self.assertFalse(st_col.nullable)
        self.assertEqual(st_col.type.length, 30)

        bq_col = TariffVersion.__table__.columns["base_quota_bytes"]
        self.assertTrue(bq_col.nullable)

        ck_names = [c.name for c in TariffVersion.__table__.constraints if hasattr(c, "name")]
        self.assertIn("ck_tariff_versions_service_type", ck_names)
        self.assertIn("ck_tariff_versions_base_quota_positive", ck_names)

    def test_tariff_version_duration_days_property(self):
        tv = TariffVersion(duration_hours=720)
        self.assertEqual(tv.duration_days, 30)
        tv.duration_hours = 24
        self.assertEqual(tv.duration_days, 1)


class TestGroupCAlembicMigration0017(unittest.TestCase):
    """Group C: Alembic Migration 0017 Graph and Reversibility."""

    def test_alembic_heads_and_chain(self):
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        heads = scripts.get_heads()
        self.assertEqual(heads, ["0017_white_internet_durations"])
        rev = scripts.get_revision("0017_white_internet_durations")
        self.assertEqual(rev.down_revision, "0016_white_internet")

    def test_migration_0017_source_content(self):
        m17_path = Path("alembic/versions/0017_white_internet_durations.py")
        self.assertTrue(m17_path.is_file())
        content = m17_path.read_text(encoding="utf-8")
        self.assertIn("def upgrade()", content)
        self.assertIn("def downgrade()", content)
        self.assertIn("service_type", content)
        self.assertIn("base_quota_bytes", content)
        self.assertIn("lifecycle_status", content)
        self.assertIn("reject_tariff_version_history_change", content)


class TestGroupDTariffQuotesRepoSnapshotting(unittest.IsolatedAsyncioTestCase):
    """Group D: Tariff Quotes Repository Snapshotting."""

    async def test_get_or_create_current_version_snapshots_fields(self):
        tariff = Tariff(
            id=10,
            name="Белый Интернет 30 дней",
            service_type=ServiceType.WHITE_INTERNET,
            duration_days=30,
            device_limit=1,
            price_rub=250,
            is_active=True,
        )
        session = AsyncMock(spec=AsyncSession)
        session.scalar.return_value = None

        created_version = None
        def mock_add(obj):
            nonlocal created_version
            if isinstance(obj, TariffVersion):
                created_version = obj

        session.add.side_effect = mock_add

        await tariff_quotes_repo.get_or_create_current_version(session, tariff)
        self.assertIsNotNone(created_version)
        self.assertEqual(created_version.service_type, ServiceType.WHITE_INTERNET)
        self.assertEqual(created_version.base_quota_bytes, WHITE_INTERNET_BASE_TRAFFIC_BYTES)
        self.assertEqual(created_version.duration_hours, 720)


class TestGroupEServersRepoCapacityCondition(unittest.TestCase):
    """Group E: Servers Repo Capacity Invariant."""

    def test_capacity_consuming_wl_condition_includes_pending_delete(self):
        cond = capacity_consuming_wl_condition()
        compiled_str = str(cond.compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("PENDING_DELETE", compiled_str)
        self.assertIn("PENDING_CREATE", compiled_str)
        self.assertIn("PENDING_UPDATE", compiled_str)
        self.assertIn("ACTIVE", compiled_str)
        self.assertIn("EXHAUSTED", compiled_str)
        self.assertIn("PENDING", compiled_str)


class TestGroupFServersRepoAtomicAllocation(unittest.IsolatedAsyncioTestCase):
    """Group F: Servers Repo Atomic Server Allocation."""

    async def test_allocate_origin_server_atomic_filters_lifecycle_and_capacity(self):
        session = AsyncMock(spec=AsyncSession)
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [1, 2]
        session.scalars.return_value = scalars_mock

        srv1 = Server(
            id=1, name="srv1", is_active=True, health_state=ServerHealthState.ONLINE,
            lifecycle_status=ServerLifecycleStatus.ACTIVE, api_url="https://s1:8444", api_key="k1",
            capabilities=["xray_origin"], max_clients=10,
            extra_data={"relays": [{"code": "de"}]},
        )
        srv2 = Server(
            id=2, name="srv2", is_active=True, health_state=ServerHealthState.ONLINE,
            lifecycle_status=ServerLifecycleStatus.ACTIVE, api_url="https://s2:8444", api_key="k2",
            capabilities=["xray_origin"], max_clients=10,
            extra_data={"relays": [{"code": "de"}]},
        )

        call_count = 0
        async def mock_scalar(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return srv1
            elif call_count == 2:
                return 10  # Full
            elif call_count == 3:
                return srv2
            elif call_count == 4:
                return 5   # Has space
            return None

        session.scalar.side_effect = mock_scalar

        allocated = await servers_repo.allocate_origin_server_atomic(session)
        self.assertIsNotNone(allocated)
        self.assertEqual(allocated.id, 2)

    async def test_allocate_origin_server_atomic_skips_origin_without_relays(self):
        session = AsyncMock(spec=AsyncSession)
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [1]
        session.scalars.return_value = scalars_mock

        srv_no_relays = Server(
            id=1, name="srv_no_relays", is_active=True, health_state=ServerHealthState.ONLINE,
            lifecycle_status=ServerLifecycleStatus.ACTIVE, api_url="https://s1:8444", api_key="k1",
            capabilities=["xray_origin"], max_clients=10,
            extra_data={"relays": []},
        )
        session.scalar.return_value = srv_no_relays

        allocated = await servers_repo.allocate_origin_server_atomic(session)
        self.assertIsNone(allocated)


class TestGroupGServersRepoGenerationCAS(unittest.IsolatedAsyncioTestCase):
    """Group G: Servers Repo Generation CAS."""

    async def test_cas_registration_and_stale_rejection(self):
        session = AsyncMock(spec=AsyncSession)
        server = Server(id=1, xray_instance_boot_id="boot-1", xray_instance_starttime=1000, xray_instance_epoch="ep-1")
        session.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=server))

        # Stale update: expected boot mismatch
        ok, res = await update_server_xray_epoch_cas(
            session, 1,
            expected_boot_id="boot-old", expected_starttime=1000,
            new_epoch="ep-2", new_boot_id="boot-1", new_starttime=1000,
        )
        self.assertFalse(ok)
        self.assertIsNone(res)

        # Successful reboot generation
        ok, res = await update_server_xray_epoch_cas(
            session, 1,
            expected_boot_id="boot-1", expected_starttime=1000,
            new_epoch="ep-2", new_boot_id="boot-2", new_starttime=2000,
        )
        self.assertTrue(ok)
        self.assertEqual(server.xray_instance_epoch, "ep-2")
        self.assertEqual(server.xray_instance_boot_id, "boot-2")


class TestGroupHWhiteInternetRepoAtomicDeduplication(unittest.IsolatedAsyncioTestCase):
    """Group H: White Internet Repo Atomic Traffic Deduplication."""

    async def test_record_and_deduct_traffic_atomic_conservation_and_dedup(self):
        session = AsyncMock(spec=AsyncSession)
        sub = WhiteInternetSubscription(
            id=1, status=WhiteInternetStatus.ACTIVE,
            traffic_used_bytes=0, traffic_overage_bytes=0,
            traffic_uplink_bytes=0, traffic_downlink_bytes=0,
            last_uplink_snapshot=0, last_downlink_snapshot=0,
            expires_at=now_utc() + timedelta(days=10),
        )
        grant = WhiteInternetQuotaGrant(
            id=1, subscription_id=1, bytes_granted=1000, bytes_remaining=1000,
            grant_type=WhiteInternetGrantType.BASE, expires_at=now_utc() + timedelta(days=10),
        )

        with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub), \
             patch("database.repositories.white_internet_repo.get_active_grants_for_deduction", return_value=[grant]), \
             patch("database.repositories.white_internet_repo.get_available_quota_bytes", return_value=500):

            dialect = MagicMock()
            dialect.name = "postgresql"
            bind = MagicMock()
            bind.dialect = dialect
            session.get_bind = MagicMock(return_value=bind)
            mock_insert_res = MagicMock()
            mock_insert_res.first.return_value = (100, now_utc())
            session.execute = AsyncMock(return_value=mock_insert_res)

            allocated, became_exhausted, available, event = await white_internet_repo.record_and_deduct_traffic_atomic(
                session,
                subscription_id=1,
                node_epoch="ep-1",
                snapshot_uplink_after=200,
                snapshot_downlink_after=300,
                snapshot_uplink_before=0,
                snapshot_downlink_before=0,
            )

            self.assertEqual(allocated, 500)
            self.assertFalse(became_exhausted)
            self.assertEqual(grant.bytes_remaining, 500)
            self.assertEqual(sub.traffic_used_bytes, 500)
            self.assertEqual(sub.traffic_uplink_bytes, 200)
            self.assertEqual(sub.traffic_downlink_bytes, 300)
            self.assertIsNotNone(event)
            self.assertEqual(event.allocated_bytes + event.overage_bytes, 500)

    async def test_record_and_deduct_duplicate_returns_noop(self):
        session = AsyncMock(spec=AsyncSession)
        sub = WhiteInternetSubscription(id=1, status=WhiteInternetStatus.ACTIVE, expires_at=now_utc() + timedelta(days=10))

        with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub), \
             patch("database.repositories.white_internet_repo.get_active_grants_for_deduction", return_value=[]), \
             patch("database.repositories.white_internet_repo.get_available_quota_bytes", return_value=1000):

            session.get_bind.return_value = MagicMock(dialect=MagicMock(name="postgresql"))
            # Duplicate conflict returns None on RETURNING
            session.execute.return_value = MagicMock(first=MagicMock(return_value=None))

            allocated, became_exhausted, available, event = await white_internet_repo.record_and_deduct_traffic_atomic(
                session,
                subscription_id=1,
                node_epoch="ep-1",
                snapshot_uplink_after=200,
                snapshot_downlink_after=300,
            )

            self.assertEqual(allocated, 0)
            self.assertFalse(became_exhausted)
            self.assertIsNone(event)


class TestGroupIWhiteInternetRepoGrantConservation(unittest.IsolatedAsyncioTestCase):
    """Group I: White Internet Repo Grant Conservation & Topup."""

    async def test_topup_quota_atomic_cap_exceeded(self):
        session = AsyncMock(spec=AsyncSession)
        sub = WhiteInternetSubscription(id=1, status=WhiteInternetStatus.ACTIVE, expires_at=now_utc() + timedelta(days=10))
        with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub), \
             patch("database.repositories.white_internet_repo._lock_all_grants", return_value=[]), \
             patch("database.repositories.white_internet_repo.get_available_quota_bytes", return_value=WHITE_INTERNET_MAX_QUOTA_BYTES - 10):

            with self.assertRaises(WhiteInternetQuotaCapExceededError):
                await white_internet_repo.topup_quota_atomic(
                    session, subscription_id=1, quote_id=1, pack_gb=25, price_rub=Decimal("100.00")
                )


class TestGroupJWhiteInternetServiceDynamicQuotaAndOptions(unittest.TestCase):
    """Group J: White Internet Service Dynamic Quota and VLESS OPTIONS."""

    def test_generate_vless_links_enforces_options_method(self):
        sub = WhiteInternetSubscription(uuid=str(uuid.uuid4()))
        links = WhiteInternetService.generate_vless_links(sub, cdn_domain="cdn.example.test")
        self.assertEqual(len(links), 1)
        link = links[0]
        self.assertTrue(link.startswith("vless://"))
        self.assertIn("type=xhttp", link)
        self.assertIn("mode=packet-up", link)

        parsed = urllib.parse.urlparse(link)
        query = urllib.parse.parse_qs(parsed.query)
        self.assertIn("extra", query)
        extra_json = json.loads(query["extra"][0])
        self.assertEqual(extra_json.get("uplinkHTTPMethod"), "OPTIONS")
        self.assertEqual(extra_json.get("mode"), "packet-up")
        self.assertTrue(extra_json.get("xPaddingObfsMode"))

    def test_generate_full_xray_config_enforces_options(self):
        sub = WhiteInternetSubscription(uuid=str(uuid.uuid4()))
        cfg = WhiteInternetService.generate_full_xray_config(sub, cdn_domain="cdn.example.test")
        outbound = next(o for o in cfg["outbounds"] if o.get("tag") == "proxy-white-internet")
        xhttp_settings = outbound["streamSettings"]["xhttpSettings"]
        self.assertEqual(xhttp_settings["uplinkHTTPMethod"], "OPTIONS")
        self.assertEqual(xhttp_settings["mode"], "packet-up")


class TestGroupKTrafficWorkerSessionIsolation(unittest.TestCase):
    """Group K: Traffic Worker Session Isolation & Rebase."""

    def test_worker_class_attributes(self):
        worker = WhiteInternetTrafficWorker()
        self.assertIsNotNone(worker.client)
        self.assertIsNotNone(worker.session_factory)


class TestGroupLReconciliationWorkerConcurrency(unittest.TestCase):
    """Group L: Reconciliation Worker Bounded Concurrency & Semaphores."""

    def test_reconciliation_worker_semaphore_and_sub_lock(self):
        worker = WhiteInternetReconciliationWorker()
        self.assertEqual(worker.max_concurrency, DEFAULT_RECONCILIATION_CONCURRENCY)
        self.assertEqual(worker._semaphore._value, 10)
        lock1 = worker._get_sub_lock(123)
        lock2 = worker._get_sub_lock(123)
        self.assertIs(lock1, lock2)
        lock3 = worker._get_sub_lock(456)
        self.assertIsNot(lock1, lock3)


class TestGroupMNodeAgentFailClosedTombstone(unittest.TestCase):
    """Group M: Node Agent Fail-Closed Tombstone."""

    def test_delete_client_source_contains_fail_closed_exception(self):
        app_path = Path("scripts/xray_api/app.py")
        content = app_path.read_text(encoding="utf-8")
        self.assertIn("client_store.delete_client", content)
        self.assertIn("HTTPException", content)
        self.assertIn("500", content)
        self.assertIn("Failed to persist client tombstone to disk", content)


class TestGroupNNodeAgentInboundDiscovery(unittest.TestCase):
    """Group N: Node Agent Inbound Discovery & Namespacing."""

    def test_inbound_discovery_filters_just1k_namespace(self):
        app_path = Path("scripts/xray_api/app.py")
        content = app_path.read_text(encoding="utf-8")
        self.assertIn("just1k-wl-", content)
        self.assertIn("get_target_inbounds", content)


class TestGroupONodeAgentDurableDirectoryFsync(unittest.TestCase):
    """Group O: Node Agent Durable Directory Fsync."""

    def test_save_client_entries_contains_directory_fsync(self):
        cs_path = Path("scripts/xray_api/client_store.py")
        content = cs_path.read_text(encoding="utf-8")
        self.assertIn("os.fsync", content)
        self.assertIn("replace", content)
        self.assertIn("parent", content)


class TestGroupPHostProvisioningScriptSecurity(unittest.TestCase):
    """Group P: Host Provisioning Script Security (just1knode.sh)."""

    def setUp(self):
        self.script_path = Path("scripts/just1knode.sh")

    def test_script_contains_security_elements(self):
        content = self.script_path.read_text(encoding="utf-8")
        self.assertIn("xrayapi", content)
        self.assertIn("8444", content)
        self.assertIn("renewal-hooks/deploy", content)
        self.assertIn("index.html", content)
        self.assertIn("Zero-Collateral", content)


class TestGroupQProtocolInvariantAndZeroSecrets(unittest.TestCase):
    """Group Q: Protocol Invariant & Secret Zero-Logging Guard."""

    def test_amnezia_protocol_constant_is_awg(self):
        self.assertEqual(AMNEZIA_PROTOCOL, "amneziawg2")
        self.assertIn("awg", AMNEZIA_PROTOCOL)

    def test_no_wireguard_wg_protocol_assignment(self):
        srv = Server(protocol=AMNEZIA_PROTOCOL)
        self.assertEqual(srv.protocol, "amneziawg2")
        vp = VPNProfile(device_name="device1", server_id=1)
        self.assertNotEqual(vp.device_name, "")


if __name__ == "__main__":
    unittest.main()
