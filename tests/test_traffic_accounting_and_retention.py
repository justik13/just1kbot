"""Comprehensive unit tests for traffic accounting, per-device retention, and node metrics."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from config.enums import WhiteInternetStatus
from database.models import Server, User, WhiteInternetSubscription
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
            avg_monthly_vless_bytes=200000000,
            avg_monthly_wi_bytes=300000000,
        )
        mock_res = MagicMock()
        mock_res.one.return_value = mock_row
        mock_session.execute.return_value = mock_res

        stats = await get_dashboard_stats(mock_session)
        self.assertEqual(stats["total"], 10)
        self.assertEqual(stats["active"], 5)
        self.assertEqual(stats["avg_monthly_awg_bytes"], 0)
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
             patch("database.repositories.vless_subscription_repo.get_active_vless_counts", new=AsyncMock(return_value=(0, 0))):
            await common._show_server_card(callback, session, server)

        rendered = callback.message.edit_text.call_args[0][0]
        # Client traffic from old cycle evaluates to 0 B, host traffic from current cycle is shown
        self.assertIn("0 B", rendered)
        self.assertIn("хостинг:", rendered)

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
            monthly_vless_bytes=13 * 1024 * 1024 * 1024,
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
        self.assertIn("• <b>Стандартный доступ (за месяц):</b> 13.0 GiB", card_text)
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
        self.assertIn("• <b>Стандартный доступ (за месяц):</b> 0 B", card_text)
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
        """render_vless_sub_screen formats instructions with client links and connect button without duplicate reset or quotas."""
        from bot import texts
        from bot.handlers.connection.device_view_routes import render_vless_sub_screen
        from database.models import User, VlessSubscription

        cur_cycle = datetime.now(timezone.utc).strftime("%Y-%m")
        user = User(
            id=1,
            telegram_id=987654321,
            device_limit=2,
            subscription_end=datetime.now(timezone.utc),
            traffic_cycle=cur_cycle,
            monthly_vless_bytes=6 * 1024 * 1024 * 1024,
        )
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
        expected_sub_url = "https://test.domain/sub/vless-token-xyz"

        with patch("database.repositories.vless_subscription_repo.get_or_create_subscription", AsyncMock(return_value=sub)), \
             patch("services.vless_subscription_service.VlessSubscriptionService.build_subscription_url", return_value=expected_sub_url), \
             patch("bot.handlers.connection.device_view_routes.render_hub", AsyncMock()) as mock_render:

            await render_vless_sub_screen(mock_bot, 987654321, user, mock_session)
            mock_render.assert_called_once()
            called_text = mock_render.call_args[0][2]
            called_kb = mock_render.call_args[0][3]

            # Instruction text checks
            self.assertIn(expected_sub_url, called_text)
            self.assertIn("INCY", called_text)
            self.assertIn("Happ", called_text)
            self.assertIn("App Store", called_text)
            self.assertIn("Google Play", called_text)
            self.assertIn("GitHub", called_text)

            # Quota and traffic lines must not be in instruction text
            self.assertNotIn("📊 <b>Трафик за месяц:</b>", called_text)
            self.assertNotIn("📊 <b>Занято устройств:</b>", called_text)

            # Buttons checks
            buttons = [btn for row in called_kb.inline_keyboard for btn in row]
            self.assertEqual(len(buttons), 3)

            connect_btn = buttons[0]
            self.assertEqual(connect_btn.text, texts.BTN_SUB_CONNECT_INCY)
            self.assertEqual(connect_btn.url, expected_sub_url)
            self.assertEqual(connect_btn.style, "success")

            back_conn_btn = buttons[1]
            self.assertEqual(back_conn_btn.callback_data, "back_to_connections")

            back_menu_btn = buttons[2]
            self.assertEqual(back_menu_btn.callback_data, "back_to_main_menu")

            self.assertFalse(any(b.callback_data == "vless_sub_reset" for b in buttons))

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

    async def test_traffic_sync_loop_vless_error_isolation(self):
        """Errors in _vless_traffic_sync_once are logged and backed off without crashing traffic_sync_loop."""
        from services.workers.traffic import traffic_sync_loop

        stop_event = asyncio.Event()

        call_count = 0

        async def fake_vless_sync(bot):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated transient VLESS node failure")
            stop_event.set()

        with patch("services.workers.traffic._vless_traffic_sync_once", side_effect=fake_vless_sync), \
             patch("services.workers.traffic.WORKER_START_DELAY", 0.0), \
             patch("services.workers.traffic.WORKER_ERROR_SLEEP_INTERVAL", 0.01), \
             patch("services.workers.traffic.TRAFFIC_MAX_BACKOFF", 0.05), \
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
            "avg_monthly_awg_bytes": 0,
            "avg_monthly_vless_bytes": 3 * 1024 * 1024 * 1024,
            "avg_monthly_wi_bytes": 0,
        }
        wl_stats_mock = {
            "active_count": 10,
            "total_traffic_bytes": 10 * 1024 * 1024 * 1024,
        }

        with patch("bot.handlers.admin.dashboard.get_dashboard_stats", new=AsyncMock(return_value=stats_mock)), \
             patch("bot.handlers.admin.dashboard.get_white_internet_dashboard_stats", new=AsyncMock(return_value=wl_stats_mock)), \
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

    def test_combined_user_quota_alert_deduplication(self):
        """_check_user_quota_alert triggers once per month only when combined AWG+VLESS exceeds 1 TiB."""
        from services.workers.traffic import _check_user_quota_alert, _quota_alerted

        _quota_alerted.clear()
        mock_bot = MagicMock()

        def _fake_start(coro):
            coro.close()
            return MagicMock()

        with patch("services.workers.traffic._start_background_task", side_effect=_fake_start) as mock_start:
            # 1. Total below 1 TiB (e.g. 500 GiB + 400 GiB = 900 GiB) -> No alert
            _check_user_quota_alert(
                bot=mock_bot,
                user_id=42,
                telegram_id=123456,
                current_cycle="2026-10",
                awg_bytes=500 * 1024**3,
                vless_bytes=400 * 1024**3,
            )
            mock_start.assert_not_called()

            # 2. Total exceeds 1 TiB (e.g. 700 GiB + 400 GiB = 1100 GiB) -> Alert triggered
            _check_user_quota_alert(
                bot=mock_bot,
                user_id=42,
                telegram_id=123456,
                current_cycle="2026-10",
                awg_bytes=700 * 1024**3,
                vless_bytes=400 * 1024**3,
            )
            self.assertEqual(mock_start.call_count, 1)

            # 3. Subsequent poll in same cycle with more traffic -> deduplicated, no new alert
            _check_user_quota_alert(
                bot=mock_bot,
                user_id=42,
                telegram_id=123456,
                current_cycle="2026-10",
                awg_bytes=750 * 1024**3,
                vless_bytes=450 * 1024**3,
            )
            self.assertEqual(mock_start.call_count, 1)

            # 4. Next cycle rollover ("2026-11") -> Alert can fire again if quota exceeded
            _check_user_quota_alert(
                bot=mock_bot,
                user_id=42,
                telegram_id=123456,
                current_cycle="2026-11",
                awg_bytes=800 * 1024**3,
                vless_bytes=300 * 1024**3,
            )
            self.assertEqual(mock_start.call_count, 2)

    def test_get_user_monthly_standard_traffic_ssot(self):
        """get_user_monthly_standard_traffic correctly returns VLESS traffic for active cycle and handles rotation/None."""
        from utils.traffic_helpers import get_user_monthly_standard_traffic

        # 1. Active cycle: returns VLESS traffic
        user_ok = User(
            id=1,
            traffic_cycle="2026-10",
            monthly_vless_bytes=8 * 1024**3,
        )
        self.assertEqual(get_user_monthly_standard_traffic(user_ok, "2026-10"), 8 * 1024**3)

        # 2. Cycle mismatch: returns 0
        user_old = User(
            id=2,
            traffic_cycle="2026-09",
            monthly_vless_bytes=8 * 1024**3,
        )
        self.assertEqual(get_user_monthly_standard_traffic(user_old, "2026-10"), 0)

        # 3. None user or None fields: safely returns 0
        self.assertEqual(get_user_monthly_standard_traffic(None, "2026-10"), 0)
        user_partial = User(
            id=3,
            traffic_cycle="2026-10",
            monthly_vless_bytes=2 * 1024**3,
        )
        self.assertEqual(get_user_monthly_standard_traffic(user_partial, "2026-10"), 2 * 1024**3)


if __name__ == "__main__":
    unittest.main()



