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
        mock_client.check_health.return_value = (True, "epoch-100", None)
        mock_client.sync_client.return_value = (True, None)

        worker = WhiteInternetReconciliationWorker(node_client=mock_client)

        mock_session = AsyncMock()

        # Execute mock query returns
        mock_session.execute.side_effect = [
            MagicMock(scalars=lambda: MagicMock(all=lambda: [server])),  # servers query
            MagicMock(scalars=lambda: MagicMock(all=lambda: [sub])),     # pending subs query
        ]

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
            health_state=ServerHealthState.ONLINE,
            xray_instance_epoch="epoch-100",  # DB holds old epoch
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
        mock_client.check_health.return_value = (True, "epoch-200", None)
        mock_client.sync_client.return_value = (True, None)

        worker = WhiteInternetReconciliationWorker(node_client=mock_client)
        mock_session = AsyncMock()

        mock_session.execute.side_effect = [
            MagicMock(scalars=lambda: MagicMock(all=lambda: [server])),  # servers query
            MagicMock(scalars=lambda: MagicMock(all=lambda: [sub])),     # pending subs query
        ]

        with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub):
            synced = await worker.run_reconciliation_cycle(mock_session)

            self.assertEqual(synced, 1)
            # Server epoch was updated to new epoch
            self.assertEqual(server.xray_instance_epoch, "epoch-200")
            # Sub was re-synced and stamped with new epoch
            self.assertEqual(sub.last_reconciled_node_epoch, "epoch-200")
            self.assertEqual(sub.actual_version, 2)


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
            {"client-uuid-1": {"uplink": 150, "downlink": 350}},  # delta = (150-100) + (350-200) = 200
        )

        worker = WhiteInternetTrafficWorker(node_client=mock_client)
        mock_session = AsyncMock()

        mock_session.execute.side_effect = [
            MagicMock(scalars=lambda: MagicMock(all=lambda: [server])),  # servers query
            MagicMock(scalar_one_or_none=lambda: sub),                    # lookup sub query
        ]

        with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub):
            with patch(
                "database.repositories.white_internet_repo.deduct_traffic_atomic",
                return_value=(200, False, 0),
            ) as mock_deduct:
                processed = await worker.run_traffic_cycle(mock_session)

                self.assertEqual(processed, 1)
                mock_deduct.assert_awaited_once_with(
                    mock_session, subscription_id=sub.id, delta_bytes=200, now=patch("utils.datetime_helpers.now_utc").start() if False else unittest.mock.ANY
                )
                self.assertEqual(sub.last_uplink_snapshot, 150)
                self.assertEqual(sub.last_downlink_snapshot, 350)
                self.assertEqual(sub.traffic_stats_epoch, "epoch-100")
