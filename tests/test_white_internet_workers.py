import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from config.enums import ServerHealthState, WhiteInternetProvisioningStatus, WhiteInternetStatus
from database.models import Server, WhiteInternetSubscription
from services.workers.white_internet_reconciliation import WhiteInternetReconciliationWorker
from services.workers.white_internet_traffic import WhiteInternetTrafficWorker
from services.xray_node_client import SyncResult, XrayNodeClient


class TestWhiteInternetReconciliationWorker(unittest.IsolatedAsyncioTestCase):
    """Test state reconciliation, stale-write detection, and epoch drift."""

    async def test_reconciliation_cycle_advances_version_and_epoch(self):
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        server = Server(
            id=1,
            name="Origin-MSK",
            protocol="xray",
            capabilities=["xray_origin"],
            api_url="https://origin.just1k.online:8444",
            api_key="secret-key",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            xray_instance_epoch="epoch-100",
        )

        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=server.id,
            token="token123",
            uuid="client-uuid-1",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=50 * 1024**3,
            traffic_used_bytes=0,
            desired_version=2,
            actual_version=1,
            last_reconciled_node_epoch="epoch-99",
            provisioning_status=WhiteInternetProvisioningStatus.PENDING_UPDATE,
        )

        mock_client = AsyncMock(spec=XrayNodeClient)
        mock_client.check_health.return_value = (True, "epoch-100", {"boot_id": "boot-1", "starttime": 12345})
        mock_client.sync_client.return_value = (SyncResult.APPLIED, None)

        worker = WhiteInternetReconciliationWorker(node_client=mock_client)

        mock_session = AsyncMock()
        mock_session.get.return_value = sub

        # Execute mock query returns
        mock_session.execute.side_effect = [
            MagicMock(scalars=lambda: MagicMock(all=lambda: [server])),  # servers query
            MagicMock(scalars=lambda: MagicMock(all=lambda: [sub])),     # pending subs query
        ]

        with patch("database.repositories.servers_repo.update_server_xray_epoch_cas", return_value=(True, server)):
            with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub):
                synced = await worker.run_reconciliation_cycle(mock_session)

                self.assertEqual(synced, 1)
                mock_client.sync_client.assert_awaited_once_with(
                    server.api_url, server.api_key, sub.uuid, is_active=True, version=2, expected_node_epoch="epoch-100"
                )
                self.assertEqual(sub.actual_version, 2)
                self.assertEqual(sub.last_reconciled_node_epoch, "epoch-100")
                self.assertEqual(sub.provisioning_status, WhiteInternetProvisioningStatus.ACTIVE)
                self.assertIsNone(sub.last_sync_error)

    async def test_reconciliation_detects_node_restart_epoch_drift(self):
        """When node restarts, check_health returns a new epoch, triggering sub reconciliation."""
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        server = Server(
            id=1,
            name="Origin-MSK",
            protocol="xray",
            capabilities=["xray_origin"],
            api_url="https://origin.just1k.online:8444",
            api_key="secret-key",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            xray_instance_epoch="epoch-100",  # DB holds old epoch
            xray_instance_boot_id="boot-1",
            xray_instance_starttime=1000,
        )

        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=server.id,
            token="token123",
            uuid="client-uuid-1",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=50 * 1024**3,
            traffic_used_bytes=0,
            desired_version=2,
            actual_version=2,  # actual_version matches desired_version, BUT epoch is stale!
            last_reconciled_node_epoch="epoch-100",
            provisioning_status=WhiteInternetProvisioningStatus.ACTIVE,
        )

        mock_client = AsyncMock(spec=XrayNodeClient)
        # Node returns NEW epoch (Xray restarted!)
        mock_client.check_health.return_value = (True, "epoch-200", {"boot_id": "boot-1", "starttime": 2000})
        mock_client.sync_client.return_value = (SyncResult.APPLIED, None)

        worker = WhiteInternetReconciliationWorker(node_client=mock_client)
        mock_session = AsyncMock()
        mock_session.get.return_value = sub

        mock_session.execute.side_effect = [
            MagicMock(scalars=lambda: MagicMock(all=lambda: [server])),  # servers query
            MagicMock(scalars=lambda: MagicMock(all=lambda: [sub])),     # pending subs query
        ]

        def fake_cas(session, sid, **kwargs):
            server.xray_instance_epoch = kwargs.get("new_epoch")
            server.xray_instance_boot_id = kwargs.get("new_boot_id")
            server.xray_instance_starttime = kwargs.get("new_starttime")
            return True, server

        with patch("database.repositories.servers_repo.update_server_xray_epoch_cas", side_effect=fake_cas):
            with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub):
                synced = await worker.run_reconciliation_cycle(mock_session)

                self.assertEqual(synced, 1)
                # Server epoch was updated to new epoch
                self.assertEqual(server.xray_instance_epoch, "epoch-200")
                # Sub was re-synced and stamped with new epoch
                self.assertEqual(sub.last_reconciled_node_epoch, "epoch-200")
                self.assertEqual(sub.actual_version, 2)

    async def test_reconciliation_inactive_sub_sets_synced_inactive(self):
        """When sub is expired/disabled, sync sets provisioning_status to SYNCED_INACTIVE."""
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        server = Server(
            id=1,
            name="Origin-MSK",
            protocol="xray",
            capabilities=["xray_origin"],
            api_url="https://origin.just1k.online:8444",
            api_key="secret-key",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            xray_instance_epoch="epoch-100",
            xray_instance_boot_id="boot-1",
            xray_instance_starttime=1000,
        )

        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=server.id,
            token="token123",
            uuid="client-uuid-1",
            status=WhiteInternetStatus.EXPIRED,
            started_at=now - timedelta(days=35),
            expires_at=now - timedelta(days=5),
            traffic_limit_bytes=50 * 1024**3,
            traffic_used_bytes=0,
            desired_version=2,
            actual_version=1,
            provisioning_status=WhiteInternetProvisioningStatus.PENDING_DELETE,
            last_reconciled_node_epoch="epoch-100",
        )

        mock_client = AsyncMock()
        mock_client.check_health.return_value = (True, "epoch-100", {"boot_id": "boot-1", "starttime": 1000})
        mock_client.sync_client.return_value = (SyncResult.APPLIED, None)

        worker = WhiteInternetReconciliationWorker(node_client=mock_client)

        mock_session = AsyncMock()
        mock_session.get.return_value = sub
        mock_session.execute.side_effect = [
            MagicMock(scalars=lambda: MagicMock(all=lambda: [server])),  # servers query
            MagicMock(scalars=lambda: MagicMock(all=lambda: [sub])),     # pending subs query
        ]

        with patch("database.repositories.servers_repo.update_server_xray_epoch_cas", return_value=(True, server)):
            with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub):
                synced = await worker.run_reconciliation_cycle(mock_session)

                self.assertEqual(synced, 1)
                mock_client.sync_client.assert_awaited_once_with(
                    server.api_url, server.api_key, sub.uuid, is_active=False, version=2, expected_node_epoch="epoch-100"
                )
                self.assertEqual(sub.actual_version, 2)
                self.assertEqual(sub.provisioning_status, WhiteInternetProvisioningStatus.SYNCED_INACTIVE)

    async def test_reconciliation_expires_overdue_active_subscription(self):
        """Active subscription with expires_at <= now must be atomically expired and synced inactive."""
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        server = Server(
            id=1,
            name="Origin-NL",
            protocol="xray",
            capabilities=["xray_origin"],
            api_url="https://nl.origin.just1k.online:8444",
            api_key="secret-key",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            xray_instance_epoch="epoch-100",
            xray_instance_boot_id="boot-1",
            xray_instance_starttime=12345,
        )

        sub = WhiteInternetSubscription(
            id=10,
            user_id=100,
            origin_node_id=1,
            token="sub-token-xyz",
            uuid="11111111-2222-3333-4444-555555555555",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now - timedelta(days=31),
            expires_at=now - timedelta(days=1),  # Overdue!
            traffic_limit_bytes=50 * 1024**3,
            traffic_used_bytes=10 * 1024**3,
            desired_version=3,
            actual_version=3,
            last_reconciled_node_epoch="epoch-100",
            provisioning_status=WhiteInternetProvisioningStatus.ACTIVE,
        )

        mock_client = AsyncMock(spec=XrayNodeClient)
        mock_client.check_health.return_value = (True, "epoch-100", {"boot_id": "boot-1", "starttime": 12345})
        mock_client.sync_client.return_value = (SyncResult.APPLIED, None)

        worker = WhiteInternetReconciliationWorker(node_client=mock_client)

        mock_session = AsyncMock()
        mock_session.execute.side_effect = [
            MagicMock(scalars=lambda: MagicMock(all=lambda: [server])),  # servers query
            MagicMock(scalars=lambda: MagicMock(all=lambda: [sub])),     # pending subs query
        ]

        async def fake_expire(session, sub_id, *, reason="subscription_expired", now=None):
            sub.status = WhiteInternetStatus.EXPIRED
            sub.desired_version += 1
            sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_DELETE
            return sub

        with patch("database.repositories.white_internet_repo.expire_subscription_atomic", side_effect=fake_expire) as mock_exp:
            with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub):
                synced = await worker.run_reconciliation_cycle(mock_session)

                self.assertEqual(synced, 1)
                mock_exp.assert_awaited_once_with(mock_session, sub.id)
                mock_client.sync_client.assert_awaited_once_with(
                    server.api_url, server.api_key, sub.uuid, is_active=False, version=4, expected_node_epoch="epoch-100"
                )
                self.assertEqual(sub.status, WhiteInternetStatus.EXPIRED)
                self.assertEqual(sub.provisioning_status, WhiteInternetProvisioningStatus.SYNCED_INACTIVE)

    async def test_reconciliation_already_newer_does_not_mutate_actual_version(self):
        """P0: When node returns already_newer, actual_version must NOT be set to older target_version."""
        server = Server(
            id=1,
            name="Origin-MSK",
            protocol="xray",
            capabilities=["xray_origin"],
            api_url="https://origin.just1k.online:8444",
            api_key="secret-key",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            xray_instance_epoch="epoch-100",
        )
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=1,
            token="token",
            uuid="11111111-2222-3333-4444-555555555555",
            status=WhiteInternetStatus.ACTIVE,
            desired_version=3,
            actual_version=1,  # older
            last_reconciled_node_epoch="epoch-90",
            provisioning_status=WhiteInternetProvisioningStatus.PENDING_UPDATE,
        )
        mock_client = AsyncMock(spec=XrayNodeClient)
        mock_client.check_health.return_value = (True, "epoch-100", {"boot_id": "b1", "starttime": 100})
        mock_client.sync_client.return_value = (SyncResult.ALREADY_NEWER, None)

        worker = WhiteInternetReconciliationWorker(node_client=mock_client)
        mock_session = AsyncMock()
        mock_session.execute.side_effect = [
            MagicMock(scalars=lambda: MagicMock(all=lambda: [server])),
            MagicMock(scalars=lambda: MagicMock(all=lambda: [sub])),
        ]
        with patch("database.repositories.servers_repo.update_server_xray_epoch_cas", return_value=(True, server)):
            with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub):
                synced = await worker.run_reconciliation_cycle(mock_session)
                self.assertEqual(synced, 1)
                self.assertEqual(sub.actual_version, 3)
                self.assertEqual(sub.provisioning_status, WhiteInternetProvisioningStatus.ACTIVE)

    async def test_disabled_or_problematic_node_skipped_from_reconciliation(self):
        """Disabled or unhealthy nodes must be excluded from reconciliation cycle."""
        server_disabled = Server(
            id=1,
            name="Origin-Disabled",
            protocol="xray",
            capabilities=["xray_origin"],
            api_url="https://origin.just1k.online:8444",
            api_key="secret-key",
            is_active=False,
            health_state=ServerHealthState.MANUAL_DISABLED,
        )

        mock_client = AsyncMock()
        worker = WhiteInternetReconciliationWorker(node_client=mock_client)

        mock_session = AsyncMock()
        active_servers = [s for s in [server_disabled] if s.is_active and s.health_state == ServerHealthState.ONLINE]
        mock_session.execute.return_value = MagicMock(scalars=lambda: MagicMock(all=lambda: active_servers))

        synced = await worker.run_reconciliation_cycle(mock_session)
        self.assertEqual(synced, 0)
        mock_client.check_health.assert_not_called()
        mock_client.sync_client.assert_not_called()

    async def test_reconciliation_bounded_parallelism(self):
        """Reconciliation worker must process tasks concurrently bounded by Semaphore(10)."""
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        server = Server(
            id=1,
            name="Origin-MSK",
            protocol="xray",
            capabilities=["xray_origin"],
            api_url="https://origin.just1k.online:8444",
            api_key="secret-key",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            xray_instance_epoch="epoch-100",
            xray_instance_boot_id="boot-1",
            xray_instance_starttime=1000,
        )

        subs = [
            WhiteInternetSubscription(
                id=i,
                user_id=100 + i,
                origin_node_id=1,
                token=f"token-{i}",
                uuid=f"00000000-0000-0000-0000-{i:012d}",
                status=WhiteInternetStatus.ACTIVE,
                started_at=now,
                expires_at=now + timedelta(days=30),
                traffic_limit_bytes=50 * 1024**3,
                traffic_used_bytes=0,
                desired_version=2,
                actual_version=1,
                last_reconciled_node_epoch="epoch-100",
                provisioning_status=WhiteInternetProvisioningStatus.PENDING_UPDATE,
            )
            for i in range(1, 16)
        ]

        active_concurrent = 0
        max_concurrent_observed = 0

        async def fake_sync(api_url, api_key, client_uuid, is_active, version, **kwargs):
            nonlocal active_concurrent, max_concurrent_observed
            active_concurrent += 1
            if active_concurrent > max_concurrent_observed:
                max_concurrent_observed = active_concurrent
            await asyncio.sleep(0.01)
            active_concurrent -= 1
            return SyncResult.APPLIED, None

        mock_client = AsyncMock(spec=XrayNodeClient)
        mock_client.check_health.return_value = (True, "epoch-100", {"boot_id": "boot-1", "starttime": 1000})
        mock_client.sync_client.side_effect = fake_sync

        worker = WhiteInternetReconciliationWorker(node_client=mock_client, max_concurrency=10)

        sub_map = {s.id: s for s in subs}
        mock_session = AsyncMock()
        mock_session.execute.side_effect = [
            MagicMock(scalars=lambda: MagicMock(all=lambda: [server])),  # servers query
            MagicMock(scalars=lambda: MagicMock(all=lambda: subs)),      # pending subs query
        ]

        with patch("database.repositories.servers_repo.update_server_xray_epoch_cas", return_value=(True, server)):
            with patch(
                "database.repositories.white_internet_repo.get_subscription_with_lock",
                side_effect=lambda session, sid: sub_map.get(sid),
            ):
                synced = await worker.run_reconciliation_cycle(mock_session)

                self.assertEqual(synced, 15)
                self.assertLessEqual(max_concurrent_observed, 10)
                self.assertGreater(max_concurrent_observed, 1)
                for s in subs:
                    self.assertEqual(s.actual_version, 2)
                    self.assertEqual(s.provisioning_status, WhiteInternetProvisioningStatus.ACTIVE)


class TestWhiteInternetTrafficWorker(unittest.IsolatedAsyncioTestCase):
    """Test monotonic traffic delta computation and grant ledger deduction."""

    async def test_traffic_sync_monotonic_delta_and_epoch_reset(self):
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        server = Server(
            id=1,
            name="Origin-MSK",
            protocol="xray",
            capabilities=["xray_origin"],
            api_url="https://origin.just1k.online:8444",
            api_key="secret-key",
            health_state=ServerHealthState.ONLINE,
            xray_instance_epoch="epoch-100",
            xray_instance_boot_id="boot-1",
            xray_instance_starttime=1000,
        )

        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=server.id,
            token="token123",
            uuid="client-uuid-1",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=50 * 1024**3,
            traffic_used_bytes=1000,
            last_uplink_snapshot=100,
            last_downlink_snapshot=200,
            traffic_stats_epoch="epoch-100",
        )

        mock_client = AsyncMock(spec=XrayNodeClient)
        # Snapshot reports increased counters within same epoch
        mock_client.get_traffic_snapshot.return_value = (
            "epoch-100",
            "boot-1",
            1000,
            {"client-uuid-1": {"uplink": 150, "downlink": 350}},  # delta = (150-100) + (350-200) = 200
        )

        worker = WhiteInternetTrafficWorker(node_client=mock_client)
        mock_session = AsyncMock()
        mock_session.scalar.return_value = sub

        mock_session.execute.side_effect = [
            MagicMock(scalars=lambda: MagicMock(all=lambda: [server])),  # servers query
        ]

        with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub):
            with patch(
                "database.repositories.white_internet_repo.record_and_deduct_traffic_atomic",
                return_value=(200, False, 1000, MagicMock()),
            ) as mock_record_and_deduct:
                processed = await worker.run_traffic_cycle(mock_session)

                self.assertEqual(processed, 1)
                mock_record_and_deduct.assert_awaited_once_with(
                    mock_session,
                    subscription_id=sub.id,
                    node_epoch="epoch-100",
                    snapshot_uplink_after=150,
                    snapshot_downlink_after=350,
                    snapshot_uplink_before=100,
                    snapshot_downlink_before=200,
                    node_boot_id="boot-1",
                    node_starttime=1000,
                    now=unittest.mock.ANY,
                )

    async def test_traffic_worker_handles_node_restart_epoch_reset(self):
        """When node restarts, epoch resets baseline to 0 and computes delta from new counters."""
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        server = Server(
            id=1,
            name="Origin-MSK",
            protocol="xray",
            capabilities=["xray_origin"],
            api_url="https://origin.just1k.online:8444",
            api_key="secret-key",
            health_state=ServerHealthState.ONLINE,
            xray_instance_epoch="epoch-200",  # New epoch
            xray_instance_boot_id="boot-1",
            xray_instance_starttime=2000,
        )

        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=server.id,
            token="token123",
            uuid="client-uuid-1",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=50 * 1024**3,
            traffic_used_bytes=1000,
            last_uplink_snapshot=500,
            last_downlink_snapshot=1500,
            traffic_stats_epoch="epoch-100",  # Old epoch
        )

        mock_client = AsyncMock(spec=XrayNodeClient)
        # Snapshot reports new epoch with fresh counters 50 and 150
        mock_client.get_traffic_snapshot.return_value = (
            "epoch-200",
            "boot-1",
            2000,
            {"client-uuid-1": {"uplink": 50, "downlink": 150}},
        )

        worker = WhiteInternetTrafficWorker(node_client=mock_client)
        mock_session = AsyncMock()
        mock_session.scalar.return_value = sub

        mock_session.execute.side_effect = [
            MagicMock(scalars=lambda: MagicMock(all=lambda: [server])),
        ]

        with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub):
            with patch(
                "database.repositories.white_internet_repo.record_and_deduct_traffic_atomic",
                return_value=(200, False, 1000, MagicMock()),
            ) as mock_record_and_deduct:
                processed = await worker.run_traffic_cycle(mock_session)

                self.assertEqual(processed, 1)
                # Baseline was 0, so before_up=0, before_down=0
                mock_record_and_deduct.assert_awaited_once_with(
                    mock_session,
                    subscription_id=sub.id,
                    node_epoch="epoch-200",
                    snapshot_uplink_after=50,
                    snapshot_downlink_after=150,
                    snapshot_uplink_before=0,
                    snapshot_downlink_before=0,
                    node_boot_id="boot-1",
                    node_starttime=2000,
                    now=unittest.mock.ANY,
                )

    async def test_traffic_worker_handles_stats_reset_within_same_epoch(self):
        """When stats reset within same epoch (uplink < snapshot), worker rebases baseline cleanly."""
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        server = Server(
            id=1,
            name="Origin-MSK",
            protocol="xray",
            capabilities=["xray_origin"],
            api_url="https://origin.just1k.online:8444",
            api_key="secret-key",
            health_state=ServerHealthState.ONLINE,
            xray_instance_epoch="epoch-100",
            xray_instance_boot_id="boot-1",
            xray_instance_starttime=1000,
        )

        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=server.id,
            token="token123",
            uuid="client-uuid-1",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=50 * 1024**3,
            traffic_used_bytes=1000,
            last_uplink_snapshot=500,  # Old higher baseline
            last_downlink_snapshot=1500,
            traffic_stats_epoch="epoch-100",  # Same epoch
        )

        mock_client = AsyncMock(spec=XrayNodeClient)
        # Snapshot reports smaller counters 50 and 150 (stats reset happened)
        mock_client.get_traffic_snapshot.return_value = (
            "epoch-100",
            "boot-1",
            1000,
            {"client-uuid-1": {"uplink": 50, "downlink": 150}},
        )

        worker = WhiteInternetTrafficWorker(node_client=mock_client)
        mock_session = AsyncMock()
        mock_session.scalar.return_value = sub

        mock_session.execute.side_effect = [
            MagicMock(scalars=lambda: MagicMock(all=lambda: [server])),
        ]

        with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub):
            with patch(
                "database.repositories.white_internet_repo.record_and_deduct_traffic_atomic",
                return_value=(200, False, 1000, MagicMock()),
            ) as mock_record_and_deduct:
                processed = await worker.run_traffic_cycle(mock_session)

                self.assertEqual(processed, 1)
                # Rebase treated baseline as 0 -> before_up=0, before_down=0
                mock_record_and_deduct.assert_awaited_once_with(
                    mock_session,
                    subscription_id=sub.id,
                    node_epoch="epoch-100",
                    snapshot_uplink_after=50,
                    snapshot_downlink_after=150,
                    snapshot_uplink_before=0,
                    snapshot_downlink_before=0,
                    node_boot_id="boot-1",
                    node_starttime=1000,
                    now=unittest.mock.ANY,
                )

    async def test_traffic_worker_poison_record_isolation(self):
        """Poison client records (corrupted stats, bad UUID, DB exceptions) must not abort the batch."""
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        server = Server(
            id=1,
            name="Origin-MSK",
            protocol="xray",
            capabilities=["xray_origin"],
            api_url="https://origin.just1k.online:8444",
            api_key="secret-key",
            health_state=ServerHealthState.ONLINE,
            is_active=True,
            xray_instance_epoch="epoch-100",
            xray_instance_boot_id="boot-1",
            xray_instance_starttime=1000,
        )

        valid_sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=1,
            token="token-valid",
            uuid="client-uuid-valid",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=50 * 1024**3,
            traffic_used_bytes=1000,
            last_uplink_snapshot=100,
            last_downlink_snapshot=200,
            traffic_stats_epoch="epoch-100",
        )

        error_sub = WhiteInternetSubscription(
            id=2,
            user_id=20,
            origin_node_id=1,
            token="token-error",
            uuid="client-uuid-error",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=50 * 1024**3,
            traffic_used_bytes=0,
            last_uplink_snapshot=0,
            last_downlink_snapshot=0,
            traffic_stats_epoch="epoch-100",
        )

        # Diverse poison records + 1 error-raising client + 1 valid client
        poison_users_stats = {
            "": {"uplink": 100, "downlink": 100},                             # Poison 1: empty UUID
            None: {"uplink": 100, "downlink": 100},                           # Poison 2: None UUID
            12345: {"uplink": 100, "downlink": 100},                          # Poison 3: non-string UUID
            "client-uuid-bad-stats": "not-a-dict",                            # Poison 4: non-dict stats
            "client-uuid-bad-counters": {"uplink": "corrupted", "downlink": None}, # Poison 5: malformed counters
            "client-uuid-not-found": {"uplink": 500, "downlink": 500},        # Poison 6: sub not in DB
            "client-uuid-error": {"uplink": 500, "downlink": 500},            # Poison 7: triggers DB error
            "client-uuid-valid": {"uplink": 150, "downlink": 350},            # Valid client! delta = 50 + 150 = 200
        }

        mock_client = AsyncMock(spec=XrayNodeClient)
        mock_client.get_traffic_snapshot.return_value = (
            "epoch-100",
            "boot-1",
            1000,
            poison_users_stats,
        )

        worker = WhiteInternetTrafficWorker(node_client=mock_client)
        mock_session = AsyncMock()

        async def fake_scalar(stmt):
            # Check params or query representation
            params = stmt.compile().params
            for v in params.values():
                if v == "client-uuid-valid":
                    return valid_sub
                if v == "client-uuid-error":
                    return error_sub
            return None

        mock_session.scalar.side_effect = fake_scalar
        mock_session.execute.side_effect = [
            MagicMock(scalars=lambda: MagicMock(all=lambda: [server])),  # servers query
        ]

        async def fake_record_and_deduct(session, subscription_id, **kwargs):
            if subscription_id == error_sub.id:
                raise RuntimeError("Simulated transient DB error on client 2")
            return 200, False, 1000, MagicMock()

        with patch("database.repositories.white_internet_repo.get_subscription_with_lock", side_effect=lambda sess, sid: valid_sub if sid == 1 else error_sub):
            with patch("database.repositories.white_internet_repo.record_and_deduct_traffic_atomic", side_effect=fake_record_and_deduct) as mock_record_and_deduct:
                processed = await worker.run_traffic_cycle(mock_session)

                # Exactly 1 valid client was processed successfully
                self.assertEqual(processed, 1)
                mock_record_and_deduct.assert_awaited()

    async def test_traffic_worker_status_invariants(self):
        """Traffic worker must only poll ONLINE and WAITING_CONFIRMATION servers with is_active=True."""
        s_online = Server(
            id=1, name="S-Online", protocol="xray", capabilities=["xray_origin"],
            api_url="https://s1:8444", api_key="k1", is_active=True,
            health_state=ServerHealthState.ONLINE, xray_instance_epoch="epoch-1",
            xray_instance_boot_id="boot-1", xray_instance_starttime=1000,
        )
        s_waiting = Server(
            id=2, name="S-Waiting", protocol="xray", capabilities=["xray_origin"],
            api_url="https://s2:8444", api_key="k2", is_active=True,
            health_state=ServerHealthState.WAITING_CONFIRMATION, xray_instance_epoch="epoch-2",
            xray_instance_boot_id="boot-2", xray_instance_starttime=1000,
        )
        s_manual_disabled = Server(
            id=3, name="S-ManualDisabled", protocol="xray", capabilities=["xray_origin"],
            api_url="https://s3:8444", api_key="k3", is_active=True,
            health_state=ServerHealthState.MANUAL_DISABLED, xray_instance_epoch="epoch-3",
        )
        s_auto_disabled = Server(
            id=4, name="S-AutoDisabled", protocol="xray", capabilities=["xray_origin"],
            api_url="https://s4:8444", api_key="k4", is_active=True,
            health_state=ServerHealthState.AUTO_DISABLED, xray_instance_epoch="epoch-4",
        )
        s_inactive = Server(
            id=5, name="S-Inactive", protocol="xray", capabilities=["xray_origin"],
            api_url="https://s5:8444", api_key="k5", is_active=False,
            health_state=ServerHealthState.ONLINE, xray_instance_epoch="epoch-5",
        )

        all_servers = [s_online, s_waiting, s_manual_disabled, s_auto_disabled, s_inactive]
        # Real DB query filters is_active=True and health_state IN (ONLINE, WAITING_CONFIRMATION)
        queried_servers = [
            s for s in all_servers
            if s.is_active and s.health_state in (ServerHealthState.ONLINE, ServerHealthState.WAITING_CONFIRMATION)
        ]

        mock_client = AsyncMock(spec=XrayNodeClient)
        mock_client.get_traffic_snapshot.side_effect = [
            ("epoch-1", "boot-1", 1000, {}),
            ("epoch-2", "boot-2", 1000, {}),
        ]

        worker = WhiteInternetTrafficWorker(node_client=mock_client)
        mock_session = AsyncMock()
        mock_session.execute.side_effect = [
            MagicMock(scalars=lambda: MagicMock(all=lambda: queried_servers)),
        ]

        await worker.run_traffic_cycle(mock_session)

        # Only s_online (https://s1:8444) and s_waiting (https://s2:8444) must be polled
        polled_urls = [call.args[0] for call in mock_client.get_traffic_snapshot.call_args_list]
        self.assertEqual(len(polled_urls), 2)
        self.assertIn("https://s1:8444", polled_urls)
        self.assertIn("https://s2:8444", polled_urls)
        self.assertNotIn("https://s3:8444", polled_urls)
        self.assertNotIn("https://s4:8444", polled_urls)
        self.assertNotIn("https://s5:8444", polled_urls)
