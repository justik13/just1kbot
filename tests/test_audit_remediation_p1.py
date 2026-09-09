"""Regression tests for audit remediation fixes (P1: orphan cleanup, DB commit before sync, idempotency locks)."""

import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from config.enums import (
    ServerHealthState,
    ServerLifecycleStatus,
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from database.models import Server, User, WhiteInternetOrphanCleanup, WhiteInternetSubscription
from database.repositories import white_internet_repo
from services.white_internet_service import WhiteInternetService
from services.workers.white_internet_reconciliation import WhiteInternetReconciliationWorker
from services.xray_node_client import SyncResult, XrayNodeClient


class AuditRemediationP1Tests(unittest.IsolatedAsyncioTestCase):
    """Test suite verifying orphan cleanup cancellation, commit before sync, and idempotency locks."""

    async def test_orphan_cleanup_cancelled_when_client_reassigned_to_same_origin(self):
        """Orphan cleanup must NOT deprovision client if client was re-placed on that origin node (A -> B -> A)."""
        mock_client = AsyncMock(spec=XrayNodeClient)
        mock_client.sync_client.return_value = (SyncResult.APPLIED, None)

        server = Server(
            id=1,
            name="Origin-A",
            protocol="xray",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            api_url="https://origin-a.just1k.best:8444",
            api_key="secret-key",
            xray_instance_epoch=1,
            capabilities=["xray_origin"],
        )

        cleanup_row = WhiteInternetOrphanCleanup(
            id=42,
            server_id=1,
            client_uuid="uuid-pingpong",
            desired_version=3,
            status="pending",
        )

        active_sub_on_a = WhiteInternetSubscription(
            id=10,
            user_id=5,
            origin_node_id=1,
            uuid="uuid-pingpong",
            status=WhiteInternetStatus.ACTIVE,
        )

        mock_session = AsyncMock()
        # 1st query: get cleanup_ids
        mock_result_ids = MagicMock()
        mock_result_ids.scalars.return_value.all.return_value = [42]
        # 2nd query (sess.get row): returns cleanup_row
        # 3rd query (sess.get server): returns server
        mock_session.get.side_effect = lambda model, pk, **kwargs: (
            cleanup_row if pk == 42 else (server if pk == 1 else None)
        )
        # 4th query (sess.scalar for active sub check): returns active_sub_on_a.id
        mock_session.scalar.return_value = active_sub_on_a.id
        mock_session.execute.return_value = mock_result_ids

        worker = WhiteInternetReconciliationWorker(node_client=mock_client)

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def fake_session_factory():
            yield mock_session

        with patch("database.repositories.white_internet_repo.mark_orphan_cleanup_done", new=AsyncMock()) as mock_done:
            swept = await worker._sweep_orphan_cleanups(fake_session_factory)
            self.assertEqual(swept, 1)
            # Crucial verification: node sync_client must NOT have been called to deprovision the active client!
            mock_client.sync_client.assert_not_called()
            mock_done.assert_awaited_once_with(mock_session, 42)

    async def test_cancel_pending_orphan_cleanups_for_client_repo(self):
        """cancel_pending_orphan_cleanups_for_client cancels pending orphan cleanups for server_id and client_uuid."""
        session = AsyncMock()
        mock_res = MagicMock()
        mock_res.rowcount = 2
        session.execute.return_value = mock_res

        cancelled = await white_internet_repo.cancel_pending_orphan_cleanups_for_client(
            session, server_id=1, client_uuid="test-uuid"
        )
        self.assertEqual(cancelled, 2)
        session.execute.assert_awaited_once()
        stmt = session.execute.call_args[0][0]
        compiled = str(stmt)
        self.assertIn("white_internet_orphan_cleanups", compiled)
        self.assertIn("status =", compiled)

    async def test_purchase_subscription_commits_before_inline_sync(self):
        """purchase_subscription must commit DB state BEFORE calling _try_inline_sync."""
        user = User(id=1, telegram_id=100)
        origin_server = Server(
            id=1,
            name="Origin-1",
            protocol="xray",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            api_url="https://origin.just1k.best:8444",
            api_key="secret",
            xray_instance_epoch=1,
            capabilities=["xray_origin"],
            extra_data={"relays": [{"code": "de"}]},
        )
        tariff = MagicMock(id=1, duration_days=30)
        tariff_version = MagicMock(id=2, price_rub=Decimal("300.00"), base_quota_bytes=50 * 1024**3, duration_hours=720)

        created_sub = WhiteInternetSubscription(
            id=10,
            user_id=1,
            origin_node_id=1,
            uuid="uuid-buy",
            token="tok",
            status=WhiteInternetStatus.PENDING,
            provisioning_status=WhiteInternetProvisioningStatus.PENDING_CREATE,
            desired_version=1,
            actual_version=0,
        )

        session = AsyncMock()
        session.add = MagicMock()
        session.scalar.return_value = origin_server

        commit_order = []

        async def tracked_commit():
            commit_order.append("commit")

        session.commit.side_effect = tracked_commit

        async def fake_inline_sync(*args, **kwargs):
            commit_order.append("inline_sync")
            return True

        with patch("services.white_internet_service.lock_checkout_user", return_value=user), \
             patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=None), \
             patch.object(WhiteInternetService, "get_or_create_white_internet_tariff", return_value=tariff), \
             patch("services.white_internet_service.get_or_create_current_version", return_value=tariff_version), \
             patch.object(WhiteInternetService, "select_origin_node", return_value=origin_server), \
             patch("services.white_internet_service.create_purchase_debit", new=AsyncMock()), \
             patch("database.repositories.white_internet_repo.create_white_internet_subscription", return_value=created_sub), \
             patch.object(WhiteInternetService, "_try_inline_sync", side_effect=fake_inline_sync):

            ok, msg, sub = await WhiteInternetService.purchase_subscription(session, user_id=1)
            self.assertTrue(ok)
            # Assert commit happened BEFORE inline_sync
            self.assertEqual(commit_order, ["commit", "inline_sync"])

    async def test_create_trial_subscription_commits_before_inline_sync(self):
        """create_trial_subscription must commit DB state BEFORE calling _try_inline_sync."""
        user = User(id=2, telegram_id=200)
        origin_server = Server(
            id=1,
            name="Origin-1",
            protocol="xray",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            api_url="https://origin.just1kbest:8444",
            api_key="secret",
            xray_instance_epoch=1,
            capabilities=["xray_origin"],
            extra_data={"relays": [{"code": "nl"}]},
        )
        tariff = MagicMock(id=1, duration_days=3)
        tariff_version = MagicMock(id=2, price_rub=Decimal("0.00"), base_quota_bytes=10 * 1024**3, duration_hours=72)

        created_sub = WhiteInternetSubscription(
            id=11,
            user_id=2,
            origin_node_id=1,
            uuid="uuid-trial",
            token="tok",
            status=WhiteInternetStatus.PENDING,
            provisioning_status=WhiteInternetProvisioningStatus.PENDING_CREATE,
            desired_version=1,
            actual_version=0,
        )

        session = AsyncMock()
        session.add = MagicMock()
        commit_order = []

        async def tracked_commit():
            commit_order.append("commit")

        session.commit.side_effect = tracked_commit

        async def fake_inline_sync(*args, **kwargs):
            commit_order.append("inline_sync")
            return True

        with patch("services.white_internet_service.lock_checkout_user", return_value=user), \
             patch("database.repositories.white_internet_repo.has_ever_activated_trial", return_value=False), \
             patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=None), \
             patch.object(WhiteInternetService, "get_or_create_white_internet_tariff", return_value=tariff), \
             patch("services.white_internet_service.get_or_create_current_version", return_value=tariff_version), \
             patch.object(WhiteInternetService, "select_origin_node", return_value=origin_server), \
             patch("database.repositories.white_internet_repo.create_white_internet_subscription", return_value=created_sub), \
             patch.object(WhiteInternetService, "_try_inline_sync", side_effect=fake_inline_sync):

            ok, msg, sub = await WhiteInternetService.create_trial_subscription(session, user_id=2)
            self.assertTrue(ok)
            # Assert commit happened BEFORE inline_sync
            self.assertEqual(commit_order, ["commit", "inline_sync"])

