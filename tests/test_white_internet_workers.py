"""Unit and integration tests for White Internet reconciliation and traffic sync workers."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from config.enums import ServerHealthState, WhiteInternetProvisioningStatus, WhiteInternetStatus
from database.models import Server, WhiteInternetSubscription
from services.workers.white_internet_reconciliation import WhiteInternetReconciliationWorker
from services.workers.white_internet_traffic import WhiteInternetTrafficWorker
from services.xray_node_client import XrayNodeClient


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
        mock_client.sync_client.return_value = (True, None)

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
                    server.api_url, server.api_key, sub.uuid, is_active=True
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
        mock_client.sync_client.return_value = (True, None)

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
        mock_client.sync_client.return_value = (True, None)

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
                    server.api_url, server.api_key, sub.uuid, is_active=False
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
        mock_client.sync_client.return_value = (True, None)

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
                    server.api_url, server.api_key, sub.uuid, is_active=False
                )
                self.assertEqual(sub.status, WhiteInternetStatus.EXPIRED)
                self.assertEqual(sub.provisioning_status, WhiteInternetProvisioningStatus.SYNCED_INACTIVE)

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
        # When DB query filters is_active=True and health_state IN (ONLINE, WAITING_CONFIRMATION),
        # disabled server is not in the active server list
        active_servers = [s for s in [server_disabled] if s.is_active and s.health_state == ServerHealthState.ONLINE]
        mock_session.execute.return_value = MagicMock(scalars=lambda: MagicMock(all=lambda: active_servers))

        synced = await worker.run_reconciliation_cycle(mock_session)
        self.assertEqual(synced, 0)
        mock_client.check_health.assert_not_called()
        mock_client.sync_client.assert_not_called()


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
                "database.repositories.white_internet_repo.deduct_traffic_atomic",
                return_value=(200, False, 0),
            ) as mock_deduct:
                with patch("database.repositories.white_internet_repo.record_traffic_event_atomic") as mock_event:
                    processed = await worker.run_traffic_cycle(mock_session)

                    self.assertEqual(processed, 1)
                    mock_deduct.assert_awaited_once_with(
                        mock_session,
                        subscription_id=sub.id,
                        delta_bytes=200,
                        delta_uplink=50,
                        delta_downlink=150,
                        now=unittest.mock.ANY,
                    )
                    mock_event.assert_awaited_once_with(
                        mock_session,
                        subscription_id=sub.id,
                        node_epoch="epoch-100",
                        node_boot_id="boot-1",
                        node_starttime=1000,
                        snapshot_uplink_before=100,
                        snapshot_uplink_after=150,
                        snapshot_downlink_before=200,
                        snapshot_downlink_after=350,
                        delta_uplink=50,
                        delta_downlink=150,
                        allocated_bytes=200,
                        overage_bytes=0,
                        now=unittest.mock.ANY,
                    )
                    self.assertEqual(sub.last_uplink_snapshot, 150)
                    self.assertEqual(sub.last_downlink_snapshot, 350)
                    self.assertEqual(sub.traffic_stats_epoch, "epoch-100")

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
                "database.repositories.white_internet_repo.deduct_traffic_atomic",
                return_value=(200, False, 0),
            ) as mock_deduct:
                with patch("database.repositories.white_internet_repo.record_traffic_event_atomic") as mock_event:
                    processed = await worker.run_traffic_cycle(mock_session)

                    self.assertEqual(processed, 1)
                    # Baseline was 0, so delta is 50 + 150 = 200
                    mock_deduct.assert_awaited_once_with(
                        mock_session,
                        subscription_id=sub.id,
                        delta_bytes=200,
                        delta_uplink=50,
                        delta_downlink=150,
                        now=unittest.mock.ANY,
                    )
                    mock_event.assert_awaited_once_with(
                        mock_session,
                        subscription_id=sub.id,
                        node_epoch="epoch-200",
                        node_boot_id="boot-1",
                        node_starttime=2000,
                        snapshot_uplink_before=0,
                        snapshot_uplink_after=50,
                        snapshot_downlink_before=0,
                        snapshot_downlink_after=150,
                        delta_uplink=50,
                        delta_downlink=150,
                        allocated_bytes=200,
                        overage_bytes=0,
                        now=unittest.mock.ANY,
                    )
                    self.assertEqual(sub.last_uplink_snapshot, 50)
                    self.assertEqual(sub.last_downlink_snapshot, 150)
                    self.assertEqual(sub.traffic_stats_epoch, "epoch-200")

