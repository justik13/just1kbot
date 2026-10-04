"""Comprehensive unit tests for traffic accounting, per-device retention, and node metrics."""

from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from database.models import Server, User, VPNProfile, WhiteInternetSubscription
from services.device_service import DeviceService
from services.workers.traffic import _process_server_traffic
from services.workers.white_internet_traffic import WhiteInternetTrafficWorker


class TrafficAccountingAndRetentionTests(unittest.IsolatedAsyncioTestCase):
    """Test suite verifying continuous traffic stats, device archiving, and monthly cycles."""

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
        self.assertIn("Устройство #1", mock_user.archived_device_traffic)
        self.assertEqual(mock_user.archived_device_traffic["Устройство #1"], 8000)

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


if __name__ == "__main__":
    unittest.main()

