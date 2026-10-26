"""Comprehensive unit tests for traffic accounting, per-device retention, and node metrics."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from config.enums import WhiteInternetStatus
from database.models import Server, User, VPNProfile, WhiteInternetSubscription
from services.device_service import DeviceService
from services.workers.traffic import _process_server_traffic
from services.workers.white_internet_traffic import WhiteInternetTrafficWorker


class TrafficAccountingAndRetentionTests(unittest.IsolatedAsyncioTestCase):
    """Test suite verifying continuous traffic stats, device archiving, and monthly cycles."""

    @classmethod
    def setUpClass(cls):
        cls.env_patcher = patch.dict(
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
                "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/db",
            },
        )
        cls.env_patcher.start()

    @classmethod
    def tearDownClass(cls):
        cls.env_patcher.stop()

    def _make_mock_awg_client(self, down: int, up: int):
        client = MagicMock()
        client.traffics.totalDownload = down
        client.traffics.totalUpload = up
        client.lastHandshake = 1700000000
        client.status = "active"
        return client

    async def test_awg_traffic_updates_user_monthly_and_total(self):
        """AWG traffic delta updates total_traffic_bytes and monthly_awg_bytes atomically."""
        server_info = {"id": 1, "name": "AWG-1"}
        peer_id = "peer-awg-test-1"

        api_clients = {
            peer_id: self._make_mock_awg_client(down=50 * 1024 * 1024, up=10 * 1024 * 1024)
        }

        mock_session = AsyncMock()
        mock_result = MagicMock()
        # Initial row: 0 prior traffic, delta = 60 MB
        mock_row = (
            101, peer_id, 0, 0, 0, 0, None, True, 77, False, 12345,
            datetime(2028, 1, 1, tzinfo=timezone.utc), False,
        )
        mock_result.all.return_value = [mock_row]
        mock_result.scalars.return_value.all.return_value = [101]
        mock_session.execute.return_value = mock_result

        mock_server = Server(
            id=1,
            name="AWG-1",
            extra_data={"traffic_cycle": datetime.now(timezone.utc).strftime("%Y-%m"), "monthly_traffic_bytes": 0},
        )
        mock_session.get.return_value = mock_server

        with patch("services.workers.traffic.session_scope") as mock_scope, \
             patch("services.slots_cache.get_server_generation", return_value=1):
            mock_scope.return_value.__aenter__.return_value = mock_session
            await _process_server_traffic(server_info, api_clients, expected_gen=1)

        # Check executions
        self.assertTrue(mock_session.execute.called)
        calls = mock_session.execute.call_args_list
        # calls[0] is select(VPNProfile), calls[1] is bulk update VPNProfile, calls[2] is update(User)
        self.assertGreaterEqual(len(calls), 3)

        # Verify transaction decoupling: transaction 1 for User/VPNProfile, transaction 2 for Server extra_data
        self.assertEqual(mock_scope.call_count, 2)

        # Verify server monthly traffic updated
        expected_bytes = 60 * 1024 * 1024
        self.assertEqual(mock_server.extra_data["monthly_traffic_bytes"], expected_bytes)

    async def test_delete_device_archives_traffic_to_user(self):
        """When device is deleted, its traffic is archived to User.archived_device_traffic."""
        mock_session = AsyncMock()
        mock_user = User(
            id=42,
            telegram_id=999888,
            archived_device_traffic={"Устройство #1": 1000},
        )
        mock_profile = VPNProfile(
            id=10,
            user_id=42,
            server_id=1,
            peer_id="peer-key-to-delete",
            device_name="Устройство #1",
            traffic_down=5000,
            traffic_up=2000,
            raw_last_down=5000,
            raw_last_up=2000,
            provisioning_status="active",
        )

        mock_profile_res = MagicMock()
        mock_profile_res.scalar_one_or_none.return_value = mock_profile
        mock_session.execute.return_value = mock_profile_res
        mock_session.get.return_value = mock_user

        with patch("services.device_service.resolve_profile_endpoint_snapshot", return_value=(1, "DE-1", "http://node:8080", "secret")), \
             patch("services.device_service.ensure_delete_operation"), \
             patch("services.device_service.DeviceService.has_active_migration", return_value=False):
            result = await DeviceService.delete_device(mock_session, mock_profile)

        self.assertTrue(result)
        # Check that user archived traffic was incremented:
        # Prior archived was 1000, profile had 5000+2000 = 7000
        # Total added from profile: 7000 -> total archived = 1000 + 7000 = 8000
        self.assertIn("slot_1", mock_user.archived_device_traffic)
        self.assertEqual(mock_user.archived_device_traffic["slot_1"], 8000)

    async def test_white_internet_worker_updates_user_and_server_monthly(self):
        """Xray consumption increments User total_wi_traffic_bytes, monthly_wi_bytes and Server extra_data."""
        worker = WhiteInternetTrafficWorker(bot=None)
        worker.client = AsyncMock()
        worker.session_factory = MagicMock()

        cur_cycle = datetime.now(timezone.utc).strftime("%Y-%m")
        mock_server = Server(
            id=2,
            name="Origin-1",
            api_url="http://origin:8080",
            api_key="sec",
            protocol="xray",
            capabilities=["xray_origin"],
            is_active=True,
            xray_instance_epoch="epoch-1",
            xray_instance_boot_id="boot-1",
            xray_instance_starttime=1000,
            extra_data={"traffic_cycle": cur_cycle, "monthly_traffic_bytes": 100},
        )

        # Snapshot returns 50MB downlink, 10MB uplink for client_uuid
        client_uuid = "00000000-0000-0000-0000-000000000001"
        worker.client.get_traffic_snapshot.return_value = (
            "epoch-1", "boot-1", 1000,
            {client_uuid: {"uplink": 10 * 1024 * 1024, "downlink": 50 * 1024 * 1024}},
        )

        mock_sub = WhiteInternetSubscription(
            id=5,
            user_id=12,
            origin_node_id=2,
            uuid=client_uuid,
            status="active",
            traffic_stats_epoch="epoch-1",
            last_uplink_snapshot=0,
            last_downlink_snapshot=0,
            traffic_limit_bytes=100 * 1024 * 1024 * 1024,
            traffic_used_bytes=0,
            traffic_overage_bytes=0,
        )

        mock_session = AsyncMock()
        mock_res = MagicMock()
        mock_res.scalars.return_value.all.return_value = [mock_server]
        mock_session.execute.return_value = mock_res
        worker.session_factory.return_value.__aenter__.return_value = mock_session
        mock_session.scalar.side_effect = [
            mock_sub,  # sub_meta
            None,      # for notification user lookup if needed
        ]
        mock_session.get.return_value = mock_server

        with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=mock_sub), \
             patch("database.repositories.white_internet_repo.record_and_deduct_traffic_atomic", return_value=(60 * 1024 * 1024, False, 1000, None)):
            await worker.run_traffic_cycle()

        # Check server monthly traffic updated
        expected = 100 + (60 * 1024 * 1024)
        self.assertEqual(mock_server.extra_data["monthly_traffic_bytes"], expected)

    async def test_dashboard_stats_monthly_averages(self):
        """get_dashboard_stats returns non-zero monthly traffic and averages for active subscribers."""
        from database.repositories.users_repo import get_dashboard_stats

        mock_session = AsyncMock()
        mock_row = MagicMock(
            total=10,
            active=5,
            new_24h=2,
            total_traffic_bytes=5000000000,
            total_wi_traffic_bytes=2000000000,
            avg_traffic_bytes_active=1000000000,
            avg_monthly_awg_bytes=800000000,
            avg_monthly_vless_bytes=200000000,
            avg_monthly_wi_bytes=300000000,
        )
        mock_res = MagicMock()
        mock_res.one.return_value = mock_row
        mock_session.execute.return_value = mock_res

        stats = await get_dashboard_stats(mock_session)
        self.assertEqual(stats["total"], 10)
        self.assertEqual(stats["active"], 5)
        self.assertEqual(stats["avg_monthly_awg_bytes"], 800000000)
        self.assertEqual(stats["avg_monthly_vless_bytes"], 200000000)
        self.assertEqual(stats["avg_monthly_wi_bytes"], 300000000)
        self.assertEqual(stats["total_wi_traffic_bytes"], 2000000000)

    async def test_admin_dashboard_rendering_monthly_zero_semantic(self):
        """When monthly AWG average is 0, dashboard shows 0/month rather than lifetime fallback."""
        from bot.handlers.admin.dashboard import _show_admin_dashboard

        callback = MagicMock()
        callback.message = AsyncMock()
        mock_session = AsyncMock()

        stats_mock = {
            "total": 50,
            "active": 25,
            "new_24h": 3,
            "total_traffic_bytes": 100 * 1024 * 1024 * 1024,
            "total_wi_traffic_bytes": 50 * 1024 * 1024 * 1024,
            "avg_traffic_bytes_active": 4 * 1024 * 1024 * 1024,
            "avg_monthly_awg_bytes": 0,
            "avg_monthly_wi_bytes": 0,
        }
        wl_stats_mock = {
            "active_count": 10,
            "total_traffic_bytes": 10 * 1024 * 1024 * 1024,
        }

        with patch("bot.handlers.admin.dashboard.get_dashboard_stats", new=AsyncMock(return_value=stats_mock)), \
             patch("bot.handlers.admin.dashboard.get_white_internet_dashboard_stats", new=AsyncMock(return_value=wl_stats_mock)), \
             patch("bot.handlers.admin.dashboard.get_total_free_ips", new=AsyncMock(return_value=100)), \
             patch("bot.handlers.admin.dashboard._get_financial_stats", new=AsyncMock(return_value={"rev_24h": 0, "count_24h": 0, "rev_7d": 0, "rev_30d": 0, "avg_check": 0})), \
             patch("bot.handlers.admin.dashboard._get_servers_capacity_summary", new=AsyncMock(return_value="")), \
             patch("bot.handlers.admin.dashboard.MaintenanceService.is_enabled", new=AsyncMock(return_value=False)):
            await _show_admin_dashboard(callback, mock_session)

        sent_text = callback.message.edit_text.call_args[0][0]
        # Must show 0 Б/мес or 0 B/month, NOT 4 GB
        self.assertNotIn("4 ГБ", sent_text)
        self.assertNotIn("4.0 ГБ", sent_text)
        self.assertIn("/мес", sent_text)
        # WI total must render the 50 GB from total_wi_traffic_bytes
        self.assertIn("50", sent_text)

    async def test_manage_device_view_sums_archived_traffic(self):
        """render_device_screen includes archived traffic for this device slot."""
        from bot.handlers.connection.device_view_routes import render_device_screen

        mock_profile = VPNProfile(
            id=15,
            user_id=88,
            server_id=1,
            device_name="Устройство #1",
            traffic_down=1000,
            traffic_up=500,
            provisioning_status="active",
            is_active=True,
        )
        mock_user = User(
            id=88,
            archived_device_traffic={"Устройство #1": 5000},
            subscription_end=datetime(2028, 1, 1, tzinfo=timezone.utc),
        )
        mock_server = Server(
            id=1,
            name="DE-1",
            country_flag="🇩🇪",
            is_active=True,
        )

        mock_session = AsyncMock()
        mock_bot = AsyncMock()

        with patch("bot.handlers.connection.device_view_routes.get_server_by_id", return_value=mock_server), \
             patch("bot.handlers.connection.device_view_routes.render_hub") as mock_hub:
            await render_device_screen(
                bot=mock_bot,
                chat_id=12345,
                profile=mock_profile,
                user=mock_user,
                session=mock_session,
            )

            self.assertTrue(mock_hub.called)
            rendered_text = mock_hub.call_args[0][2]
            # Total traffic displayed should be 5000 (archived) + 1500 (live) = 6500 B
            # format_traffic(6500) produces "6.3 KiB"
            self.assertIn("6.3 KiB", rendered_text)

    def test_accumulate_host_traffic_cycle_logic(self):
        """Host monthly traffic accumulates deltas, detects reboots, and rolls over cycles."""
        from utils.traffic_helpers import accumulate_host_traffic_cycle

        extra = {}
        # 1. Zero/invalid bytes -> no modification
        self.assertFalse(accumulate_host_traffic_cycle(extra, 0, 0, "2026-10"))
        self.assertEqual(extra, {})

        # 2. First observation in cycle "2026-10" (baseline)
        modified = accumulate_host_traffic_cycle(extra, 1000, 2000, "2026-10")
        self.assertTrue(modified)
        self.assertEqual(extra["host_monthly_traffic_bytes"], 0)
        self.assertEqual(extra["host_last_raw_bytes"], 3000)
        self.assertEqual(extra["host_traffic_cycle"], "2026-10")

        # 3. Normal traffic growth (raw increases by 500)
        modified = accumulate_host_traffic_cycle(extra, 1200, 2300, "2026-10")
        self.assertTrue(modified)
        self.assertEqual(extra["host_monthly_traffic_bytes"], 500)
        self.assertEqual(extra["host_last_raw_bytes"], 3500)

        # 4. Same raw bytes -> no change
        modified = accumulate_host_traffic_cycle(extra, 1200, 2300, "2026-10")
        self.assertFalse(modified)
        self.assertEqual(extra["host_monthly_traffic_bytes"], 500)

        # 5. Node reboot (raw counters drop to 200)
        modified = accumulate_host_traffic_cycle(extra, 100, 100, "2026-10")
        self.assertTrue(modified)
        self.assertEqual(extra["host_monthly_traffic_bytes"], 700)
        self.assertEqual(extra["host_last_raw_bytes"], 200)

        # 6. Cycle rollover to "2026-11" preserves boundary delta (1000 - 200 = 800)
        modified = accumulate_host_traffic_cycle(extra, 500, 500, "2026-11")
        self.assertTrue(modified)
        self.assertEqual(extra["host_monthly_traffic_bytes"], 800)
        self.assertEqual(extra["host_last_raw_bytes"], 1000)
        self.assertEqual(extra["host_traffic_cycle"], "2026-11")
        # Ensure client traffic_cycle is NOT touched by host helper
        self.assertNotIn("traffic_cycle", extra)

    async def test_delete_device_zeroes_live_profile_counters(self):
        """When device is deleted, live counters are set to 0 to prevent double-counting while deleting."""
        mock_session = AsyncMock()
        mock_user = User(
            id=42,
            telegram_id=999888,
            archived_device_traffic={},
        )
        mock_profile = VPNProfile(
            id=10,
            user_id=42,
            server_id=1,
            peer_id="peer-key-to-delete",
            device_name="Phone #1",
            traffic_down=5000,
            traffic_up=2000,
            provisioning_status="active",
        )

        mock_profile_res = MagicMock()
        mock_profile_res.scalar_one_or_none.return_value = mock_profile
        mock_session.execute.return_value = mock_profile_res
        mock_session.get.return_value = mock_user

        with patch("services.device_service.resolve_profile_endpoint_snapshot", return_value=(1, "DE-1", "http://node:8080", "secret")), \
             patch("services.device_service.ensure_delete_operation"), \
             patch("services.device_service.DeviceService.has_active_migration", return_value=False):
            await DeviceService.delete_device(mock_session, mock_profile)

        self.assertEqual(mock_profile.traffic_down, 0)
        self.assertEqual(mock_profile.traffic_up, 0)
        self.assertEqual(mock_user.archived_device_traffic["slot_1"], 7000)

    async def test_migrate_device_retains_traffic_in_user_archived(self):
        """Device migration transfers old_profile traffic to User.archived_device_traffic and zeroes old profile."""
        from datetime import timedelta
        from services.slots_cache import ServerPeerSnapshot

        now = datetime.now(timezone.utc)
        mock_session = AsyncMock()

        user = User(
            id=1,
            telegram_id=12345,
            device_limit=2,
            subscription_end=now + timedelta(days=30),
            is_banned=False,
            device_creations_today=0,
            last_creation_date=now.date(),
            archived_device_traffic={"Laptop #1": 2000},
        )
        old_profile = VPNProfile(
            id=10,
            user_id=1,
            server_id=100,
            device_name="Laptop #1",
            peer_id="peer-old-123",
            provisioning_status="active",
            traffic_down=8000,
            traffic_up=2000,
        )
        target_server = Server(
            id=200,
            name="NL-1",
            protocol="amneziawg2",
            api_url="http://node200:8080",
            api_key="secret200",
            is_active=True,
            max_clients=100,
        )

        snapshot = ServerPeerSnapshot(
            server_id=200,
            peer_ids=frozenset(["peer-1"]),
            captured_at=now,
        )

        mock_user_res = MagicMock()
        mock_user_res.scalar_one.return_value = user

        mock_profile_res = MagicMock()
        mock_profile_res.scalar_one_or_none.return_value = old_profile

        mock_target_res = MagicMock()
        mock_target_res.scalar_one_or_none.return_value = target_server

        mock_count_user = MagicMock()
        mock_count_user.scalar_one.return_value = 1

        mock_count_server = MagicMock()
        mock_count_server.scalar_one.return_value = 1

        mock_bot_peers = MagicMock()
        mock_bot_peers.scalars.return_value.all.return_value = []

        mock_dup_check = MagicMock()
        mock_dup_check.scalar_one_or_none.return_value = None

        mock_session.execute.side_effect = [
            mock_user_res,
            mock_profile_res,
            mock_target_res,
            mock_count_user,
            mock_count_server,
            mock_bot_peers,
            mock_dup_check,
        ]

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock()
        mock_ctx.__aexit__ = AsyncMock()
        mock_session.begin_nested = MagicMock(return_value=mock_ctx)
        mock_session.add = MagicMock()

        with patch("services.device_service.DeviceService.has_active_migration", return_value=False), \
             patch("services.device_service.DeviceService.get_last_migration_time", return_value=None), \
             patch("services.device_service.ensure_server_capacity", new_callable=AsyncMock), \
             patch("services.device_service.enqueue_api_operation", new_callable=AsyncMock), \
             patch("services.device_service.AuditService.log_action", new_callable=AsyncMock):
            new_profile = await DeviceService.migrate_device(
                mock_session,
                user_id=user.id,
                profile_id=old_profile.id,
                target_server_id=target_server.id,
                snapshot=snapshot,
            )

        self.assertIsNotNone(new_profile)
        self.assertEqual(user.archived_device_traffic["slot_1"], 12000)
        self.assertEqual(old_profile.traffic_down, 0)
        self.assertEqual(old_profile.traffic_up, 0)

    async def test_finalize_delete_success_safety_archiving(self):
        """Finalizer deletes profile directly without locking User, preventing lock order deadlocks."""
        from database.models import APIOperation
        from services.api_operations_finalizer import finalize_delete_success

        mock_session = AsyncMock()
        mock_op = APIOperation(
            id=101,
            profile_id=55,
            operation_type="delete_peer",
            status="processing",
            locked_by="worker-1",
            attempts=1,
        )
        mock_profile = VPNProfile(
            id=55,
            user_id=99,
            device_name="Tablet #1",
            traffic_down=3000,
            traffic_up=1500,
        )
        mock_user = User(
            id=99,
            archived_device_traffic={"Tablet #1": 500},
        )

        mock_session.get.return_value = mock_user

        with patch("services.api_operations_finalizer._lock_operation_and_profile", return_value=(mock_op, mock_profile)), \
             patch("services.api_operations_finalizer._scope") as mock_scope:
            mock_scope.return_value.__aenter__.return_value = mock_session
            await finalize_delete_success(
                operation_id=101,
                worker_id="worker-1",
                expected_attempt_number=1,
            )

        # Finalizer deletes profile without locking User (preventing deadlocks)
        mock_session.delete.assert_awaited_once_with(mock_profile)
        mock_session.get.assert_not_called()

    async def test_profile_deletion_service_archives_traffic(self):
        """ProfileDeletionService archives unarchived traffic to User before deleting."""
        from services.profile_deletion_service import ProfileDeletionService

        mock_session = AsyncMock()
        mock_profile = VPNProfile(
            id=55,
            user_id=99,
            server_id=1,
            peer_id="peer-55",
            client_name="c55",
            device_name="Tablet #1",
            traffic_down=3000,
            traffic_up=1500,
            provisioning_status="active",
        )
        mock_user = User(
            id=99,
            archived_device_traffic={"Tablet #1": 500},
        )

        mock_session.get.return_value = mock_user
        mock_res = MagicMock()
        mock_res.scalars.return_value = [mock_profile]
        mock_session.execute.return_value = mock_res

        with patch("services.profile_deletion_service.resolve_profile_endpoint_snapshot", return_value=(1, "srv", "http://url", "key")), \
             patch("services.profile_deletion_service.ensure_delete_operation") as mock_ensure:
            await ProfileDeletionService.delete_profiles_for_user(
                mock_session,
                user_id=99,
                reason="ban_delete",
            )

        self.assertEqual(mock_user.archived_device_traffic["slot_1"], 5000)
        self.assertEqual(mock_profile.traffic_down, 0)
        self.assertEqual(mock_profile.traffic_up, 0)
        mock_ensure.assert_awaited_once()

    def test_xray_traffic_snapshot_tuple_backward_compatibility(self):
        """TrafficSnapshot acts as a 4-tuple while exposing host_tx_bytes and host_rx_bytes."""
        from services.xray_node_client import TrafficSnapshot

        users = {"uuid-1": {"uplink": 100, "downlink": 200}}
        snap = TrafficSnapshot("epoch-x", "boot-y", 12345, users, host_tx_bytes=1000, host_rx_bytes=2000)

        # 1. Unpacking as 4-tuple
        epoch, boot_id, starttime, users_out = snap
        self.assertEqual(epoch, "epoch-x")
        self.assertEqual(boot_id, "boot-y")
        self.assertEqual(starttime, 12345)
        self.assertEqual(users_out, users)
        self.assertEqual(len(snap), 4)

        # 2. Host metrics
        self.assertEqual(snap.host_tx_bytes, 1000)
        self.assertEqual(snap.host_rx_bytes, 2000)

    def test_traffic_query_excludes_deleting_profiles(self):
        """Traffic query explicitly filters out profiles in deleting or delete_failed status."""
        from database.models import VPNProfile
        from sqlalchemy import select

        stmt = select(VPNProfile).where(
            VPNProfile.provisioning_status.notin_(["deleting", "delete_failed"])
        )
        sql = str(stmt)
        self.assertIn("vpn_profiles.provisioning_status NOT IN", sql)

    def test_slot_key_extraction_and_retention_helpers(self):
        """Slot-based retention correctly normalizes device names, handles prod devices, and migrates legacy keys."""
        from utils.traffic_helpers import (
            get_archived_traffic_for_device,
            get_device_slot_key,
            record_device_traffic_archive,
        )

        # 1. Slot key normalization
        self.assertEqual(get_device_slot_key("Устройство #1"), "slot_1")
        self.assertEqual(get_device_slot_key("Устройство #2"), "slot_2")
        self.assertEqual(get_device_slot_key("Пк #2"), "slot_2")  # Real prod renamed device
        self.assertEqual(get_device_slot_key("Хабиби #1"), "slot_1")  # Real prod renamed device

        # 2. Recording archive accumulates under canonical slot
        archived = {}
        record_device_traffic_archive(archived, "Пк #2", 5000)
        self.assertEqual(archived, {"slot_2": 5000})

        record_device_traffic_archive(archived, "Хабиби #1", 3000)
        self.assertEqual(archived, {"slot_2": 5000, "slot_1": 3000})

        # 3. Reading archive matches both recreated slot and legacy names
        self.assertEqual(get_archived_traffic_for_device(archived, "Устройство #2"), 5000)
        self.assertEqual(get_archived_traffic_for_device(archived, "Пк #2"), 5000)
        self.assertEqual(get_archived_traffic_for_device(archived, "Устройство #1"), 3000)
        self.assertEqual(get_archived_traffic_for_device(archived, "Хабиби #1"), 3000)

        # 4. Legacy migration: unmigrated key is merged into slot key
        legacy_archived = {"Пк #2": 2000}
        record_device_traffic_archive(legacy_archived, "Пк #2", 3000)
        self.assertNotIn("Пк #2", legacy_archived)
        self.assertEqual(legacy_archived["slot_2"], 5000)

    async def test_server_card_cycles_decoupled(self):
        """Server card evaluates client and host cycles independently via _show_server_card."""
        from types import SimpleNamespace
        from bot.handlers.admin.servers import common
        from utils.datetime_helpers import now_utc

        now = now_utc()
        current_cycle = now.strftime("%Y-%m")
        old_cycle = "2020-01"

        # Case 1: Host has new cycle, client has old cycle
        server = SimpleNamespace(
            id=1,
            name="Test Node",
            country_flag="🇩🇪",
            is_active=True,
            disabled_reason=None,
            disabled_at=None,
            last_successful_check=None,
            max_clients=240,
            protocol="vless",
            api_url="https://vless.example.com",
            extra_data={
                "traffic_cycle": old_cycle,
                "monthly_traffic_bytes": 1000000,
                "host_traffic_cycle": current_cycle,
                "host_monthly_traffic_bytes": 500000,
            },
        )
        callback = MagicMock()
        callback.message = AsyncMock()
        session = AsyncMock()

        with patch("bot.handlers.admin.servers.common.get_server_peer_counts", new=AsyncMock(return_value={1: 0})), \
             patch("services.slots_cache.get_cached_peer_count", return_value=None), \
             patch("database.repositories.vless_subscription_repo.get_active_vless_counts", new=AsyncMock(return_value=(0, 0))):
            await common._show_server_card(callback, session, server)

        rendered = callback.message.edit_text.call_args[0][0]
        # Client traffic from old cycle evaluates to 0 B, host traffic from current cycle is shown
        self.assertIn("0 B", rendered)
        self.assertIn("хостинг:", rendered)

    def test_traffic_worker_bulk_update_excludes_deleting_profiles(self):
        """Traffic worker bulk update statement contains strict provisioning_status guards."""
        from sqlalchemy import bindparam, update
        from database.models import VPNProfile

        stmt = (
            update(VPNProfile)
            .where(
                VPNProfile.id == bindparam("b_id"),
                VPNProfile.provisioning_status != "deleting",
                VPNProfile.provisioning_status != "delete_failed",
            )
            .values(
                traffic_down=bindparam("traffic_down"),
                traffic_up=bindparam("traffic_up"),
                raw_last_down=bindparam("raw_last_down"),
                raw_last_up=bindparam("raw_last_up"),
                last_connected=bindparam("last_connected"),
            )
        )
        sql = str(stmt)
        self.assertIn("vpn_profiles.provisioning_status !=", sql)
        self.assertIn("vpn_profiles.id = :b_id", sql)

    async def test_archive_failure_raises_and_aborts_deletion_fail_closed(self):
        """Archive failure raises exception to abort deletion, preventing permanent traffic loss (fail-closed)."""
        mock_session = AsyncMock()
        mock_profile = VPNProfile(
            id=77,
            user_id=10,
            server_id=1,
            peer_id="peer-77",
            client_name="c77",
            device_name="Phone #1",
            traffic_down=500,
            traffic_up=500,
            provisioning_status="active",
        )
        mock_user = User(
            id=10,
            archived_device_traffic={"Phone #1": 100},
        )

        mock_session.get.return_value = mock_user
        mock_res = MagicMock()
        mock_res.scalar_one_or_none.return_value = mock_profile
        mock_session.execute.return_value = mock_res

        with patch("utils.traffic_helpers.record_device_traffic_archive", side_effect=ValueError("Simulated archive corrupt error")), \
             patch("services.device_service.resolve_profile_endpoint_snapshot", return_value=(1, "AWG-1", "http://node", "secret")), \
             patch("services.device_service.ensure_delete_operation", new_callable=AsyncMock) as mock_ensure:
            with self.assertRaises(ValueError):
                await DeviceService.delete_device(
                    mock_session,
                    profile=mock_profile,
                )
            # Delete operation was NOT enqueued, transaction aborted
            mock_ensure.assert_not_called()


    async def test_rename_device_canonical_lock_order_and_archive_update(self):
        """Device rename acquires User FOR UPDATE before VPNProfile FOR UPDATE and updates archive."""
        from aiogram.fsm.context import FSMContext
        from bot.handlers.connection.device_rename_routes import rename_device_process

        mock_session = AsyncMock()
        mock_session.begin_nested = None
        mock_user = User(
            id=10,
            telegram_id=123456,
            archived_device_traffic={"Phone #1": 500},
        )
        mock_profile = VPNProfile(
            id=5,
            user_id=10,
            device_name="Phone #1",
            provisioning_status="active",
        )
        mock_session.get.return_value = mock_user

        mock_res = MagicMock()
        mock_res.scalar_one_or_none.return_value = mock_profile
        mock_session.execute.return_value = mock_res

        message = MagicMock()
        message.from_user.id = 123456
        message.chat.id = 123456
        message.text = "Work Phone"
        message.bot = AsyncMock()

        state = AsyncMock(spec=FSMContext)
        state.get_data.return_value = {"profile_id": 5}

        with patch("bot.handlers.connection.device_rename_routes.SubscriptionService.check_access", return_value=True), \
             patch("services.device_service.DeviceService.has_active_migration", return_value=False), \
             patch("bot.handlers.connection.device_rename_routes.get_user_profiles", return_value=[]), \
             patch("bot.handlers.connection.device_rename_routes.update_profile", new_callable=AsyncMock), \
             patch("bot.handlers.connection.device_rename_routes.render_hub", new_callable=AsyncMock), \
             patch("bot.handlers.connection.device_rename_routes.render_device_screen", new_callable=AsyncMock), \
             patch("services.audit_service.AuditService.log_action", new_callable=AsyncMock):
            await rename_device_process(message, state, mock_session, db_user=mock_user)

        # 1. User was locked first via session.get with for_update
        mock_session.get.assert_awaited_once_with(User, 10, with_for_update=True)
        # 2. Archive was updated on user_obj with slot key
        self.assertIsNotNone(mock_user.archived_device_traffic)
        self.assertIn("slot_1", mock_user.archived_device_traffic)
        self.assertEqual(mock_user.archived_device_traffic["slot_1"], 500)

    async def test_white_internet_worker_locks_user_before_deduction(self):
        """White Internet traffic worker acquires User lock before deducting to eliminate deadlock."""
        from config.enums import ServerHealthState, ServerLifecycleStatus

        mock_server = Server(
            id=1,
            name="Origin-1",
            protocol="xray",
            capabilities=["xray_origin"],
            api_url="http://node:8444",
            api_key="secret",
            is_active=True,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            health_state=ServerHealthState.ONLINE,
            xray_instance_epoch="epoch-1",
            xray_instance_boot_id="boot-1",
            xray_instance_starttime=12345,
            extra_data={"traffic_cycle": "2026-10", "monthly_traffic_bytes": 0},
        )
        mock_sub = WhiteInternetSubscription(
            id=1,
            user_id=42,
            origin_node_id=1,
            uuid="uuid-1",
            token="token-1",
            status=WhiteInternetStatus.ACTIVE,
            traffic_stats_epoch="epoch-1",
            last_uplink_snapshot=100,
            last_downlink_snapshot=200,
            base_traffic_bytes=1000000,
            extra_traffic_bytes=0,
            traffic_used_bytes=0,
            traffic_overage_bytes=0,
            notified_90p=False,
            is_trial=False,
            desired_version=1,
        )

        mock_session = AsyncMock()
        mock_session.scalar.return_value = mock_sub
        mock_session.get.return_value = mock_server

        def mock_execute(stmt, *args, **kwargs):
            m = MagicMock()
            m.scalars.return_value.all.return_value = [mock_server]
            return m

        mock_session.execute.side_effect = mock_execute

        mock_client = AsyncMock()
        mock_client.get_traffic_snapshot.return_value = (
            "epoch-1",
            "boot-1",
            12345,
            {"uuid-1": {"uplink": 150, "downlink": 300}},
        )

        worker = WhiteInternetTrafficWorker(node_client=mock_client)

        with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=mock_sub), \
             patch("database.repositories.white_internet_repo.record_and_deduct_traffic_atomic", return_value=(150, False, 1000, None)):
            processed = await worker.run_traffic_cycle(mock_session)

        self.assertEqual(processed, 1)
        # Verify User.id was locked via select with_for_update before deduction
        execute_calls = mock_session.execute.call_args_list
        user_lock_calls = [
            c for c in execute_calls
            if "users" in str(c[0][0]).lower() and "for update" in str(c[0][0]).lower()
        ]
        self.assertGreaterEqual(len(user_lock_calls), 1)

    async def test_white_internet_worker_isolates_server_consumed_on_user_error(self):
        """If user update fails in WI worker, server_consumed is not added to server extra_data."""
        from config.enums import ServerHealthState, ServerLifecycleStatus

        mock_server = Server(
            id=1,
            name="Origin-1",
            protocol="xray",
            capabilities=["xray_origin"],
            api_url="http://node:8444",
            api_key="secret",
            is_active=True,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            health_state=ServerHealthState.ONLINE,
            xray_instance_epoch="epoch-1",
            xray_instance_boot_id="boot-1",
            xray_instance_starttime=12345,
            extra_data={"traffic_cycle": "2026-10", "monthly_traffic_bytes": 0},
        )
        mock_sub = WhiteInternetSubscription(
            id=1,
            user_id=42,
            origin_node_id=1,
            uuid="uuid-1",
            token="token-1",
            status=WhiteInternetStatus.ACTIVE,
            traffic_stats_epoch="epoch-1",
            last_uplink_snapshot=100,
            last_downlink_snapshot=200,
            base_traffic_bytes=1000000,
            extra_traffic_bytes=0,
            traffic_used_bytes=0,
            traffic_overage_bytes=0,
            notified_90p=False,
            is_trial=False,
            desired_version=1,
        )

        mock_session = AsyncMock()
        mock_session.scalar.return_value = mock_sub
        mock_session.get.return_value = mock_server

        def mock_execute(stmt, *args, **kwargs):
            s_str = str(stmt).lower()
            if "update users" in s_str:
                raise RuntimeError("Simulated DB conflict on User update")
            m = MagicMock()
            m.scalars.return_value.all.return_value = [mock_server]
            return m

        mock_session.execute.side_effect = mock_execute

        mock_client = AsyncMock()
        mock_client.get_traffic_snapshot.return_value = (
            "epoch-1",
            "boot-1",
            12345,
            {"uuid-1": {"uplink": 150, "downlink": 300}},
        )

        worker = WhiteInternetTrafficWorker(node_client=mock_client)

        with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=mock_sub), \
             patch("database.repositories.white_internet_repo.record_and_deduct_traffic_atomic", return_value=(150, False, 1000, None)):
            processed = await worker.run_traffic_cycle(mock_session)

        # 0 processed successfully because of user update error
        self.assertEqual(processed, 0)
        # Server monthly_traffic_bytes was NOT incremented with the failed consumption
        self.assertEqual(mock_server.extra_data.get("monthly_traffic_bytes", 0), 0)

    async def test_white_internet_worker_skips_snapshot_if_subscription_migrated(self):
        """WI worker skips deducting snapshot from old server if subscription was migrated concurrently."""
        from config.enums import ServerHealthState, ServerLifecycleStatus

        mock_server = Server(
            id=1,
            name="Origin-1",
            protocol="xray",
            capabilities=["xray_origin"],
            api_url="http://node:8444",
            api_key="secret",
            is_active=True,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            health_state=ServerHealthState.ONLINE,
            xray_instance_epoch="epoch-1",
            xray_instance_boot_id="boot-1",
            xray_instance_starttime=12345,
            extra_data={"traffic_cycle": "2026-10", "monthly_traffic_bytes": 0},
        )
        migrated_sub = WhiteInternetSubscription(
            id=1,
            user_id=42,
            origin_node_id=2,  # Moved to server 2
            uuid="uuid-1",
            token="token-1",
            status=WhiteInternetStatus.ACTIVE,
            traffic_stats_epoch="epoch-1",
            last_uplink_snapshot=100,
            last_downlink_snapshot=200,
            base_traffic_bytes=1000000,
            extra_traffic_bytes=0,
            traffic_used_bytes=0,
            traffic_overage_bytes=0,
            notified_90p=False,
            is_trial=False,
            desired_version=1,
        )

        mock_session = AsyncMock()
        mock_session.scalar.return_value = migrated_sub
        mock_session.get.return_value = mock_server

        def mock_execute(stmt, *args, **kwargs):
            m = MagicMock()
            m.scalars.return_value.all.return_value = [mock_server]
            return m

        mock_session.execute.side_effect = mock_execute

        mock_client = AsyncMock()
        mock_client.get_traffic_snapshot.return_value = (
            "epoch-1",
            "boot-1",
            12345,
            {"uuid-1": {"uplink": 150, "downlink": 300}},
        )

        worker = WhiteInternetTrafficWorker(node_client=mock_client)

        with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=migrated_sub), \
             patch("database.repositories.white_internet_repo.record_and_deduct_traffic_atomic") as mock_deduct:
            processed = await worker.run_traffic_cycle(mock_session)

        # Processed should be 0 because sub.origin_node_id != server_id (2 != 1)
        self.assertEqual(processed, 0)
        mock_deduct.assert_not_called()

    async def test_awg_traffic_worker_skips_concurrently_deleting_profile(self):
        """If profile becomes deleting concurrently, worker skips delta to prevent user/profile desync."""
        from services.workers.traffic import _process_server_traffic

        server_info = {"id": 1, "name": "NL-1"}
        peer_id = "test-peer-uuid-deleting"
        api_clients = {
            peer_id: MagicMock(
                traffics=MagicMock(totalDownload=2000000, totalUpload=1000000),
                lastHandshake=1700000000,
                status="active",
            )
        }

        mock_session = AsyncMock()

        # Step 1 initial candidate check: finds profile
        candidate_row = (10, peer_id, 42)
        # Step 2 locked check: profile is now missing or deleted (empty list)
        call_count = 0

        def mock_execute(stmt, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            m = MagicMock()
            if call_count == 1:
                # Candidate selection
                m.all.return_value = [candidate_row]
            elif call_count == 2:
                # User lock
                m.all.return_value = []
            elif call_count == 3:
                # Locked profiles query: returns empty because profile was deleted
                m.all.return_value = []
            return m

        mock_session.execute.side_effect = mock_execute
        mock_server = Server(
            id=1,
            name="NL-1",
            extra_data={"traffic_cycle": "2026-10", "monthly_traffic_bytes": 0},
        )
        mock_session.get.return_value = mock_server

        with patch("services.workers.traffic.session_scope") as mock_scope, \
             patch("services.slots_cache.get_server_generation", return_value=1):
            mock_scope.return_value.__aenter__.return_value = mock_session
            await _process_server_traffic(server_info, api_clients, expected_gen=1)

        # Confirm no UPDATE statements were executed on User or VPNProfile
        exec_calls = mock_session.execute.call_args_list
        # call 1: select candidates
        # call 2: select users with_for_update
        # call 3: select locked profiles with_for_update
        self.assertEqual(len(exec_calls), 3)

    async def test_migration_grace_deletion_archives_residual_traffic(self):
        """_schedule_migration_grace_deletion archives any residual traffic on old_profile into user archive."""
        from services.api_operations_finalizer import _schedule_migration_grace_deletion

        operation = MagicMock()
        operation.payload = {"migrating_from_id": 99}
        new_profile = MagicMock(provisioning_status="active")

        mock_user = User(
            id=10,
            telegram_id=123456,
            archived_device_traffic={"slot_1": 5000},
        )
        old_profile = VPNProfile(
            id=99,
            user_id=10,
            device_name="Устройство #1",
            provisioning_status="active",
            traffic_down=3000,
            traffic_up=2000,
            peer_id="old-peer-1",
            server=MagicMock(protocol="amneziawg2"),
        )

        mock_session = AsyncMock()
        mock_res = MagicMock()
        mock_res.scalar_one_or_none.return_value = old_profile
        mock_session.execute.return_value = mock_res
        mock_session.get.return_value = mock_user

        with patch("services.api_operations_queue.resolve_profile_endpoint_snapshot", return_value=(1, "S1", "http://url", "key")), \
             patch("services.api_operations_queue.ensure_delete_operation", new_callable=AsyncMock) as mock_ensure:
            await _schedule_migration_grace_deletion(mock_session, operation, new_profile)

        # Traffic must be transferred to user archive: 5000 + 3000 + 2000 = 10000
        self.assertEqual(mock_user.archived_device_traffic["slot_1"], 10000)
        self.assertEqual(old_profile.traffic_down, 0)
        self.assertEqual(old_profile.traffic_up, 0)
        self.assertEqual(old_profile.provisioning_status, "deleting")
        mock_ensure.assert_awaited_once()

    async def test_lock_operation_and_profile_canonical_lock_order(self):
        """_lock_operation_and_profile acquires User FOR UPDATE before VPNProfile and APIOperation."""
        from services.api_operations_finalizer import _lock_operation_and_profile

        mock_session = AsyncMock()
        executed_stmts = []

        async def fake_execute(stmt, *args, **kwargs):
            executed_stmts.append(str(stmt))
            mock_res = MagicMock()
            sql = str(stmt).lower()
            if "for update" in sql and "users" in sql:
                mock_res.scalar_one_or_none.return_value = 10
            elif "for update" in sql and "vpn_profiles" in sql:
                mock_res.scalar_one_or_none.return_value = MagicMock(id=42, user_id=10)
            elif "for update" in sql and "api_operations" in sql:
                mock_op = MagicMock(id=1, status="processing", locked_by="w1", attempts=1, profile_id=42)
                mock_res.scalar_one_or_none.return_value = mock_op
            elif "api_operations.profile_id" in sql:
                mock_res.scalar_one_or_none.return_value = 42
            elif "vpn_profiles.user_id" in sql:
                mock_res.scalar_one_or_none.return_value = 10
            return mock_res

        mock_session.execute = AsyncMock(side_effect=fake_execute)

        op, prof = await _lock_operation_and_profile(mock_session, operation_id=1, worker_id="w1", attempt=1)

        self.assertIsNotNone(op)
        self.assertIsNotNone(prof)
        user_lock_idx = next(i for i, s in enumerate(executed_stmts) if "users" in s.lower() and "for update" in s.lower())
        prof_lock_idx = next(i for i, s in enumerate(executed_stmts) if "vpn_profiles" in s.lower() and "for update" in s.lower())
        op_lock_idx = next(i for i, s in enumerate(executed_stmts) if "api_operations" in s.lower() and "for update" in s.lower())

        self.assertLess(user_lock_idx, prof_lock_idx)
        self.assertLess(prof_lock_idx, op_lock_idx)

    def test_format_user_card_renders_separate_monthly_traffic(self):
        """User card renders total traffic and separate AWG, VLESS and White Internet monthly lines."""
        from bot.handlers.admin.users.common import format_user_card_text

        now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
        user = User(
            id=1,
            telegram_id=987654321,
            username="monthly_tester",
            first_name="Tester",
            total_traffic_bytes=15 * 1024 * 1024 * 1024,
            total_wi_traffic_bytes=5 * 1024 * 1024 * 1024,
            monthly_awg_bytes=10 * 1024 * 1024 * 1024,
            monthly_vless_bytes=3 * 1024 * 1024 * 1024,
            monthly_wi_bytes=2 * 1024 * 1024 * 1024,
            traffic_cycle="2026-10",
        )

        card_text = format_user_card_text(
            user,
            profiles=[],
            referrals=[],
            now=now,
        )

        self.assertIn("<b>Трафик туннелей:</b> 20.0 GiB", card_text)
        self.assertIn("• <b>AWG (за месяц):</b> 10.0 GiB", card_text)
        self.assertIn("• <b>VLESS (за месяц):</b> 3.0 GiB", card_text)
        self.assertIn("• <b>Белый Интернет (за месяц):</b> 2.0 GiB", card_text)

    def test_format_user_card_resets_monthly_traffic_on_cycle_rotation(self):
        """When user traffic_cycle does not match current month, monthly counters show 0 B."""
        from bot.handlers.admin.users.common import format_user_card_text

        now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
        user = User(
            id=1,
            telegram_id=987654321,
            username="monthly_tester",
            first_name="Tester",
            total_traffic_bytes=10 * 1024 * 1024 * 1024,
            total_wi_traffic_bytes=3 * 1024 * 1024 * 1024,
            monthly_awg_bytes=10 * 1024 * 1024 * 1024,
            monthly_vless_bytes=3 * 1024 * 1024 * 1024,
            monthly_wi_bytes=3 * 1024 * 1024 * 1024,
            traffic_cycle="2026-09",
        )

        card_text = format_user_card_text(
            user,
            profiles=[],
            referrals=[],
            now=now,
        )

        self.assertIn("<b>Трафик туннелей:</b> 13.0 GiB", card_text)
        self.assertIn("• <b>AWG (за месяц):</b> 0 B", card_text)
        self.assertIn("• <b>VLESS (за месяц):</b> 0 B", card_text)
        self.assertIn("• <b>Белый Интернет (за месяц):</b> 0 B", card_text)

    async def test_record_vless_traffic_atomic_multi_server_isolation(self):
        """Monotonic VLESS traffic accounting establishes baseline on first poll and maintains isolated baselines per server."""
        from database.models import VlessSubscription
        from database.repositories.vless_subscription_repo import record_vless_traffic_atomic

        sub = VlessSubscription(
            id=100,
            user_id=10,
            token="vless-token-1",
            uuid="vless-uuid-1",
            traffic_down=0,
            traffic_up=0,
            node_traffic_snapshots={},
        )

        now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)

        mock_session = AsyncMock()
        mock_session.scalar.side_effect = [sub, sub]

        # 1. First snapshot from Server 1 (epoch 1, up 100, down 400) establishes baseline without inflating monthly traffic
        delta_total, delta_down, delta_up = await record_vless_traffic_atomic(
            session=mock_session,
            subscription_id=100,
            server_id=1,
            node_epoch="epoch-srv-1",
            raw_uplink=100,
            raw_downlink=400,
            expected_uuid="vless-uuid-1",
            now=now,
        )
        self.assertEqual(delta_total, 0)
        self.assertEqual(delta_down, 0)
        self.assertEqual(delta_up, 0)
        self.assertEqual(sub.traffic_down, 0)
        self.assertEqual(sub.traffic_up, 0)
        self.assertIn("1", sub.node_traffic_snapshots)
        self.assertEqual(sub.node_traffic_snapshots["1"]["up"], 100)
        self.assertEqual(sub.node_traffic_snapshots["1"]["down"], 400)

        # 2. First snapshot from Server 2 (epoch 2, up 50, down 150) establishes baseline for Server 2
        mock_session.scalar.side_effect = [sub, sub]
        delta_total, delta_down, delta_up = await record_vless_traffic_atomic(
            session=mock_session,
            subscription_id=100,
            server_id=2,
            node_epoch="epoch-srv-2",
            raw_uplink=50,
            raw_downlink=150,
            expected_uuid="vless-uuid-1",
            now=now,
        )
        self.assertEqual(delta_total, 0)
        self.assertEqual(delta_down, 0)
        self.assertEqual(delta_up, 0)
        self.assertEqual(sub.traffic_down, 0)
        self.assertEqual(sub.traffic_up, 0)
        self.assertIn("2", sub.node_traffic_snapshots)

        # 3. Subsequent snapshot from Server 1 (still epoch 1, up 120, down 500)
        # Deltas must be delta_up=20, delta_down=100 (relative to Server 1's previous snapshot, NOT Server 2)
        mock_session.scalar.side_effect = [sub, sub]
        delta_total, delta_down, delta_up = await record_vless_traffic_atomic(
            session=mock_session,
            subscription_id=100,
            server_id=1,
            node_epoch="epoch-srv-1",
            raw_uplink=120,
            raw_downlink=500,
            expected_uuid="vless-uuid-1",
            now=now,
        )
        self.assertEqual(delta_total, 120)
        self.assertEqual(delta_down, 100)
        self.assertEqual(delta_up, 20)
        self.assertEqual(sub.traffic_down, 100)
        self.assertEqual(sub.traffic_up, 20)

    async def test_record_vless_traffic_atomic_epoch_drift_rebase(self):
        """When node epoch changes (e.g. Xray restart), baseline is rebased without negative deltas."""
        from database.models import VlessSubscription
        from database.repositories.vless_subscription_repo import record_vless_traffic_atomic

        sub = VlessSubscription(
            id=100,
            user_id=10,
            token="vless-token-1",
            uuid="vless-uuid-1",
            traffic_down=1000,
            traffic_up=200,
            node_traffic_snapshots={
                "1": {"epoch": "old-epoch", "up": 200, "down": 1000, "updated_at": 1729000000}
            },
        )

        now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
        mock_session = AsyncMock()
        mock_session.scalar.side_effect = [sub, sub]

        # New epoch on server 1, counters start fresh at 30 and 70
        delta_total, delta_down, delta_up = await record_vless_traffic_atomic(
            session=mock_session,
            subscription_id=100,
            server_id=1,
            node_epoch="new-epoch",
            raw_uplink=30,
            raw_downlink=70,
            now=now,
        )
        self.assertEqual(delta_total, 100)
        self.assertEqual(delta_down, 70)
        self.assertEqual(delta_up, 30)
        self.assertEqual(sub.traffic_down, 1070)
        self.assertEqual(sub.traffic_up, 230)
        self.assertEqual(sub.node_traffic_snapshots["1"]["epoch"], "new-epoch")

    async def test_render_vless_sub_screen_renders_traffic_total(self):
        """render_vless_sub_screen formats and displays accumulated VLESS traffic."""
        from bot.handlers.connection.device_view_routes import render_vless_sub_screen
        from database.models import User, VlessSubscription

        user = User(id=1, telegram_id=987654321, device_limit=2, subscription_end=datetime.now(timezone.utc))
        sub = VlessSubscription(
            id=10,
            user_id=1,
            token="vless-token-xyz",
            uuid="vless-uuid-xyz",
            traffic_down=5 * 1024 * 1024 * 1024,
            traffic_up=1 * 1024 * 1024 * 1024,
        )

        mock_bot = AsyncMock()
        mock_session = AsyncMock()

        with patch("database.repositories.vless_subscription_repo.get_or_create_subscription", AsyncMock(return_value=sub)), \
             patch("services.vless_subscription_service.VlessSubscriptionService.build_subscription_url", return_value="https://test.domain/sub/vless-token-xyz"), \
             patch("services.subscription.SubscriptionService.get_effective_device_limit", AsyncMock(return_value=2)), \
             patch("services.vless_subscription_service.VlessSubscriptionService.get_combined_quota", AsyncMock(return_value=(1, 0, 1))), \
             patch("bot.handlers.connection.device_view_routes.render_hub", AsyncMock()) as mock_render:

            await render_vless_sub_screen(mock_bot, 987654321, user, mock_session)
            mock_render.assert_called_once()
            called_text = mock_render.call_args[0][2]
            self.assertIn("📈 <b>Использовано трафика:</b> 6.0 GiB", called_text)
            self.assertIn("📊 <b>Занято устройств:</b> 1 из 2", called_text)

    async def test_record_vless_traffic_atomic_uuid_mismatch_fencing(self):
        """When subscription UUID rotated concurrently, atomic recording aborts without mutating state."""
        from database.models import VlessSubscription
        from database.repositories.vless_subscription_repo import record_vless_traffic_atomic

        sub_initial = VlessSubscription(
            id=100,
            user_id=10,
            token="vless-token-1",
            uuid="old-uuid-before-rotation",
            traffic_down=100,
            traffic_up=50,
            node_traffic_snapshots={},
        )
        sub_locked = VlessSubscription(
            id=100,
            user_id=10,
            token="vless-token-1",
            uuid="new-rotated-uuid",
            traffic_down=100,
            traffic_up=50,
            node_traffic_snapshots={},
        )
        mock_session = AsyncMock()
        mock_session.scalar.side_effect = [sub_initial, sub_locked]

        delta_total, delta_down, delta_up = await record_vless_traffic_atomic(
            session=mock_session,
            subscription_id=100,
            server_id=1,
            node_epoch="epoch-srv-1",
            raw_uplink=500,
            raw_downlink=1000,
            expected_uuid="old-uuid-before-rotation",
            now=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(delta_total, 0)
        self.assertEqual(delta_down, 0)
        self.assertEqual(delta_up, 0)
        self.assertEqual(sub_locked.traffic_down, 100)
        self.assertEqual(sub_locked.traffic_up, 50)
        self.assertEqual(sub_locked.node_traffic_snapshots, {})

    async def test_record_vless_traffic_atomic_fresh_subscription_credits_initial_delta(self):
        """Fresh subscription with _fresh marker credits initial non-zero traffic rather than discarding as baseline."""
        from database.models import VlessSubscription
        from database.repositories.vless_subscription_repo import record_vless_traffic_atomic

        sub = VlessSubscription(
            id=101,
            user_id=11,
            token="vless-fresh-tok",
            uuid="vless-fresh-uuid",
            traffic_down=0,
            traffic_up=0,
            node_traffic_snapshots={"_fresh": True},
        )
        mock_session = AsyncMock()
        mock_session.scalar.side_effect = [sub, sub]

        delta_total, delta_down, delta_up = await record_vless_traffic_atomic(
            session=mock_session,
            subscription_id=101,
            server_id=1,
            node_epoch="epoch-srv-1",
            raw_uplink=2000,
            raw_downlink=8000,
            expected_uuid="vless-fresh-uuid",
            now=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(delta_total, 10000)
        self.assertEqual(delta_down, 8000)
        self.assertEqual(delta_up, 2000)
        self.assertEqual(sub.traffic_down, 8000)
        self.assertEqual(sub.traffic_up, 2000)
        self.assertEqual(sub.node_traffic_snapshots["1"]["up"], 2000)
        self.assertEqual(sub.node_traffic_snapshots["1"]["down"], 8000)

    async def test_traffic_sync_loop_awg_error_does_not_skip_vless(self):
        """Errors in _traffic_sync_once do not skip _vless_traffic_sync_once in the current pass."""
        from services.workers.traffic import traffic_sync_loop

        stop_event = asyncio.Event()

        awg_call_count = 0
        vless_call_count = 0

        async def fake_failing_awg_sync(bot):
            nonlocal awg_call_count
            awg_call_count += 1
            raise RuntimeError("AWG transient DB timeout")

        async def fake_vless_sync(bot):
            nonlocal vless_call_count
            vless_call_count += 1
            stop_event.set()

        with patch("services.workers.traffic._traffic_sync_once", side_effect=fake_failing_awg_sync), \
             patch("services.workers.traffic._vless_traffic_sync_once", side_effect=fake_vless_sync), \
             patch("services.workers.traffic.WORKER_START_DELAY", 0.0), \
             patch("services.workers.traffic.WORKER_ERROR_SLEEP_INTERVAL", 0.01), \
             patch("services.workers.traffic.TRAFFIC_SYNC_INTERVAL", 0.01):
            task = asyncio.create_task(traffic_sync_loop(None, stop_event))
            await asyncio.wait_for(task, timeout=2.0)

        self.assertEqual(awg_call_count, 1)
        self.assertEqual(vless_call_count, 1)

    async def test_traffic_sync_loop_vless_error_isolation(self):
        """Errors in _vless_traffic_sync_once are logged and do not crash or back off traffic_sync_loop."""
        from services.workers.traffic import traffic_sync_loop

        stop_event = asyncio.Event()

        call_count = 0
        async def fake_vless_sync(bot):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated transient VLESS node failure")
            stop_event.set()

        async def fake_traffic_sync(bot):
            pass

        with patch("services.workers.traffic._traffic_sync_once", side_effect=fake_traffic_sync), \
             patch("services.workers.traffic._vless_traffic_sync_once", side_effect=fake_vless_sync), \
             patch("services.workers.traffic.WORKER_START_DELAY", 0.0), \
             patch("services.workers.traffic.TRAFFIC_SYNC_INTERVAL", 0.01):
            task = asyncio.create_task(traffic_sync_loop(None, stop_event))
            await asyncio.wait_for(task, timeout=2.0)

        self.assertEqual(call_count, 2)

    async def test_vless_sync_node_error_isolation_continues_next_server(self):
        """Failure processing one server's DB transaction does not abort remaining servers in the pass."""
        from config.enums import ServerHealthState, ServerLifecycleStatus
        from database.models import Server
        from services.workers.traffic import _vless_traffic_sync_once

        srv_fail = Server(
            id=1,
            name="node-fail",
            api_url="http://1.1.1.1:8080",
            api_key="key1",
            protocol="vless",
            is_active=True,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            health_state=ServerHealthState.ONLINE,
            extra_data={"xray_api_url": "http://1.1.1.1:8080", "xray_api_key": "key1"},
        )
        srv_ok = Server(
            id=2,
            name="node-ok",
            api_url="http://2.2.2.2:8080",
            api_key="key2",
            protocol="vless",
            is_active=True,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            health_state=ServerHealthState.ONLINE,
            extra_data={"xray_api_url": "http://2.2.2.2:8080", "xray_api_key": "key2"},
        )

        class DummySnapshot:
            host_tx_bytes = 1000
            host_rx_bytes = 2000
            def __getitem__(self, idx):
                items = ("epoch-node", "boot-1", 1700000000, {})
                return items[idx]

        call_count = 0
        def session_scope_side_effect():
            nonlocal call_count
            call_count += 1
            mock_sess = AsyncMock()
            if call_count == 1:
                # server listing
                mock_sess.execute.return_value = MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[srv_fail, srv_ok]))))
            elif call_count == 2:
                # srv_fail extra_data update fails!
                mock_sess.get.side_effect = RuntimeError("Simulated DB lock conflict on srv 1")
            elif call_count == 3:
                # srv_ok extra_data update succeeds!
                mock_sess.get.return_value = srv_ok
            @asynccontextmanager
            async def _scope():
                yield mock_sess
            return _scope()

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get_traffic_snapshot.return_value = DummySnapshot()

        with patch("services.workers.traffic.session_scope", side_effect=session_scope_side_effect), \
             patch("services.xray_node_client.XrayNodeClient", return_value=mock_client):
            await _vless_traffic_sync_once(None)

        # srv_ok was processed despite srv_fail error!
        self.assertEqual(srv_ok.extra_data["host_monthly_traffic_bytes"], 0)
        self.assertEqual(srv_ok.extra_data["host_last_raw_bytes"], 3000)

    async def test_vless_sync_updates_host_traffic_without_active_users(self):
        """Host network interface bytes update server extra_data even when no users exist on node."""
        from config.enums import ServerHealthState, ServerLifecycleStatus
        from database.models import Server
        from services.workers.traffic import _vless_traffic_sync_once

        mock_server = Server(
            id=42,
            name="vless-node-1",
            api_url="http://1.2.3.4:8080",
            api_key="secret",
            protocol="vless",
            is_active=True,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            health_state=ServerHealthState.ONLINE,
            extra_data={"xray_api_url": "http://1.2.3.4:8080", "xray_api_key": "secret"},
        )

        class DummySnapshot:
            host_tx_bytes = 7000
            host_rx_bytes = 15000
            def __getitem__(self, idx):
                items = ("epoch-node-42", "boot-1", 1700000000, {})
                return items[idx]

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_server]))))
        mock_session.get.return_value = mock_server

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get_traffic_snapshot.return_value = DummySnapshot()

        with patch("services.workers.traffic.session_scope", side_effect=fake_session_scope), \
             patch("services.xray_node_client.XrayNodeClient", return_value=mock_client):
            await _vless_traffic_sync_once(None)

        self.assertEqual(mock_server.extra_data["host_monthly_traffic_bytes"], 0)
        self.assertEqual(mock_server.extra_data["host_last_raw_bytes"], 22000)
        self.assertIn("host_traffic_cycle", mock_server.extra_data)

    async def test_admin_dashboard_rendering_combined_monthly_tunnel_traffic(self):
        """Dashboard sums avg_monthly_awg_bytes and avg_monthly_vless_bytes for tunnel average."""
        from bot.handlers.admin.dashboard import _show_admin_dashboard

        callback = MagicMock()
        callback.message = AsyncMock()
        mock_session = AsyncMock()

        stats_mock = {
            "total": 50,
            "active": 25,
            "new_24h": 3,
            "total_traffic_bytes": 100 * 1024 * 1024 * 1024,
            "total_wi_traffic_bytes": 50 * 1024 * 1024 * 1024,
            "avg_traffic_bytes_active": 4 * 1024 * 1024 * 1024,
            "avg_monthly_awg_bytes": 1024 * 1024 * 1024,
            "avg_monthly_vless_bytes": 2 * 1024 * 1024 * 1024,
            "avg_monthly_wi_bytes": 0,
        }
        wl_stats_mock = {
            "active_count": 10,
            "total_traffic_bytes": 10 * 1024 * 1024 * 1024,
        }

        with patch("bot.handlers.admin.dashboard.get_dashboard_stats", new=AsyncMock(return_value=stats_mock)), \
             patch("bot.handlers.admin.dashboard.get_white_internet_dashboard_stats", new=AsyncMock(return_value=wl_stats_mock)), \
             patch("bot.handlers.admin.dashboard.get_total_free_ips", new=AsyncMock(return_value=100)), \
             patch("bot.handlers.admin.dashboard._get_financial_stats", new=AsyncMock(return_value={"rev_24h": 0, "count_24h": 0, "rev_7d": 0, "rev_30d": 0, "avg_check": 0})), \
             patch("bot.handlers.admin.dashboard._get_servers_capacity_summary", new=AsyncMock(return_value="")), \
             patch("bot.handlers.admin.dashboard.MaintenanceService.is_enabled", new=AsyncMock(return_value=False)):
            await _show_admin_dashboard(callback, mock_session)

        sent_text = callback.message.edit_text.call_args[0][0]
        self.assertIn("3.0 GiB/мес", sent_text)

    async def test_record_vless_traffic_atomic_monthly_cycle_rollover(self):
        """Monthly rollover updates User metrics with cycle-aware case expression."""
        from database.models import VlessSubscription
        from database.repositories.vless_subscription_repo import record_vless_traffic_atomic

        sub = VlessSubscription(
            id=100,
            user_id=10,
            token="vless-token-1",
            uuid="vless-uuid-1",
            traffic_down=1000,
            traffic_up=200,
            node_traffic_snapshots={
                "1": {"epoch": "epoch-srv-1", "up": 200, "down": 1000, "updated_at": 1729000000}
            },
        )

        now = datetime(2026, 11, 1, 0, 5, tzinfo=timezone.utc)
        mock_session = AsyncMock()
        mock_session.scalar.side_effect = [sub, sub]

        delta_total, delta_down, delta_up = await record_vless_traffic_atomic(
            session=mock_session,
            subscription_id=100,
            server_id=1,
            node_epoch="epoch-srv-1",
            raw_uplink=300,
            raw_downlink=1500,
            expected_uuid="vless-uuid-1",
            now=now,
        )

        self.assertEqual(delta_total, 600)
        self.assertEqual(delta_down, 500)
        self.assertEqual(delta_up, 100)
        self.assertEqual(sub.traffic_down, 1500)
        self.assertEqual(sub.traffic_up, 300)

        # Ensure update(User) was executed on session
        self.assertTrue(mock_session.execute.called)
        update_stmt = mock_session.execute.call_args[0][0]
        self.assertEqual(update_stmt.table.name, "users")


if __name__ == "__main__":
    unittest.main()



