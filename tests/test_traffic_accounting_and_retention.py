"""Comprehensive unit tests for traffic accounting, per-device retention, and node metrics."""

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
            avg_monthly_wi_bytes=300000000,
        )
        mock_res = MagicMock()
        mock_res.one.return_value = mock_row
        mock_session.execute.return_value = mock_res

        stats = await get_dashboard_stats(mock_session)
        self.assertEqual(stats["total"], 10)
        self.assertEqual(stats["active"], 5)
        self.assertEqual(stats["avg_monthly_awg_bytes"], 800000000)
        self.assertEqual(stats["avg_monthly_wi_bytes"], 300000000)
        self.assertEqual(stats["total_wi_traffic_bytes"], 2000000000)

    def test_proc_net_dev_parsing(self):
        """Host network interface bytes correctly parses /proc/net/dev excluding virtual ifaces."""
        proc_dev_content = (
            "Inter-|   Receive                                                |  Transmit\n"
            " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
            "    lo: 1000       10    0    0    0     0          0         0     1000       10    0    0    0     0       0          0\n"
            "docker0: 5000       20    0    0    0     0          0         0     5000       20    0    0    0     0       0          0\n"
            "  eth0: 200000     100    0    0    0     0          0         0   400000      150    0    0    0     0       0          0\n"
        )
        tx_raw = 0
        rx_raw = 0
        for line in proc_dev_content.splitlines():
            if ":" not in line:
                continue
            name, stats = line.split(":", 1)
            name = name.strip()
            if name == "lo" or name.startswith(("docker", "veth", "br-", "wg", "awg", "tun", "tap")):
                continue
            cols = stats.split()
            if len(cols) >= 9:
                rx_raw += int(cols[0])
                tx_raw += int(cols[8])
        self.assertEqual(tx_raw, 400000)
        self.assertEqual(rx_raw, 200000)

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

    def test_server_card_cycles_decoupled(self):
        """Server card evaluates client and host cycles independently."""
        from utils.datetime_helpers import now_utc

        now = now_utc()
        current_cycle = now.strftime("%Y-%m")
        old_cycle = "2020-01"

        # Case 1: Host has new cycle, client has old cycle
        server_extra = {
            "traffic_cycle": old_cycle,
            "monthly_traffic_bytes": 1000000,
            "host_traffic_cycle": current_cycle,
            "host_monthly_traffic_bytes": 500000,
        }
        client_cycle = server_extra.get("traffic_cycle")
        host_cycle = server_extra.get("host_traffic_cycle")
        m_bytes = int(server_extra.get("monthly_traffic_bytes", 0) or 0) if client_cycle == current_cycle else 0
        h_bytes = int(server_extra.get("host_monthly_traffic_bytes", 0) or 0) if host_cycle == current_cycle else 0

        self.assertEqual(m_bytes, 0)
        self.assertEqual(h_bytes, 500000)

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


if __name__ == "__main__":
    unittest.main()


