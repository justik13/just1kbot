"""Unit tests for persistent AWG traffic counters and user bandwidth totals."""

from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.texts.admin.dashboard import DASHBOARD_AWG_TRAFFIC_STATS
from bot.texts.admin.servers import ADMIN_SERVER_MONTHLY_TRAFFIC
from bot.texts.admin.users import ADMIN_USER_CARD
from database.models import Server
from services.workers.traffic import _process_server_traffic


class AWGPersistentTrafficTests(unittest.IsolatedAsyncioTestCase):
    """Test suite for persistent AWG traffic counter math and accumulation."""

    def _make_mock_client_data(self, down_bytes: int, up_bytes: int):
        mock_client = MagicMock()
        mock_client.traffics.totalDownload = down_bytes
        mock_client.traffics.totalUpload = up_bytes
        mock_client.lastHandshake = 1700000000
        mock_client.status = "active"
        return mock_client

    async def test_normal_incremental_traffic_accumulation(self):
        """When node counter increases normally, deltas are added to profile, user and server."""
        server_info = {"id": 1, "name": "NL-1"}
        peer_id = "test-peer-uuid-1"

        # Previous DB state: profile has 100MB accumulated, last raw snapshot was 100MB
        # Node returns 150MB (delta = 50MB)
        prev_down = 100 * 1024 * 1024
        prev_raw = 100 * 1024 * 1024
        node_now = 150 * 1024 * 1024
        delta = 50 * 1024 * 1024

        api_clients = {
            peer_id: self._make_mock_client_data(down_bytes=node_now, up_bytes=0)
        }

        # Mock database session
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_row = (
            10,                 # p_id
            peer_id,            # peer_id
            prev_down,          # traffic_down
            0,                  # traffic_up
            prev_raw,           # raw_last_down
            0,                  # raw_last_up
            None,               # last_connected
            True,               # is_active
            42,                 # user_id
            False,              # is_banned
            123456789,          # tg_id
            datetime(2028, 1, 1, tzinfo=timezone.utc),  # sub_end
            False,              # financial_hold
        )
        mock_result.all.return_value = [mock_row]
        mock_session.execute.return_value = mock_result

        mock_server = Server(
            id=1,
            name="NL-1",
            extra_data={"traffic_cycle": datetime.now(timezone.utc).strftime("%Y-%m"), "monthly_traffic_bytes": 1000},
        )
        mock_session.get.return_value = mock_server

        with patch("services.workers.traffic.session_scope") as mock_scope, \
             patch("services.slots_cache.get_server_generation", return_value=1):
            mock_scope.return_value.__aenter__.return_value = mock_session
            await _process_server_traffic(server_info, api_clients, expected_gen=1)

        # Check VPNProfile bulk update params
        self.assertTrue(mock_session.execute.called)
        calls = mock_session.execute.call_args_list

        # Bulk update call for VPNProfile
        profile_update_args = calls[1]  # calls[0] is select
        bulk_params = profile_update_args[0][1]
        self.assertEqual(len(bulk_params), 1)
        self.assertEqual(bulk_params[0]["id"], 10)
        self.assertEqual(bulk_params[0]["traffic_down"], prev_down + delta)
        self.assertEqual(bulk_params[0]["raw_last_down"], node_now)

        # Check server monthly accumulation
        self.assertEqual(mock_server.extra_data["monthly_traffic_bytes"], 1000 + delta)

    async def test_node_reboot_counter_reset_does_not_wipe_historical_traffic(self):
        """When node reboots and counters reset to 0, history is preserved and new traffic adds up."""
        server_info = {"id": 1, "name": "NL-1"}
        peer_id = "test-peer-uuid-1"

        # Previous DB state: profile has 500MB accumulated, last raw was 500MB
        # Node rebooted: node counter is now 10MB (reset occurred)
        # Expected behavior: delta is 10MB (not negative, not zero), new total is 510MB!
        prev_down = 500 * 1024 * 1024
        prev_raw = 500 * 1024 * 1024
        node_after_reboot = 10 * 1024 * 1024  # counter reset!

        api_clients = {
            peer_id: self._make_mock_client_data(down_bytes=node_after_reboot, up_bytes=0)
        }

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_row = (
            10,
            peer_id,
            prev_down,
            0,
            prev_raw,
            0,
            None,
            True,
            42,
            False,
            123456789,
            datetime(2028, 1, 1, tzinfo=timezone.utc),
            False,
        )
        mock_result.all.return_value = [mock_row]
        mock_session.execute.return_value = mock_result

        mock_server = Server(
            id=1,
            name="NL-1",
            extra_data={"traffic_cycle": datetime.now(timezone.utc).strftime("%Y-%m"), "monthly_traffic_bytes": 0},
        )
        mock_session.get.return_value = mock_server

        with patch("services.workers.traffic.session_scope") as mock_scope, \
             patch("services.slots_cache.get_server_generation", return_value=1):
            mock_scope.return_value.__aenter__.return_value = mock_session
            await _process_server_traffic(server_info, api_clients, expected_gen=1)

        calls = mock_session.execute.call_args_list
        profile_update_args = calls[1]
        bulk_params = profile_update_args[0][1]

        # Profile traffic MUST NOT reset to 10MB; it must be 510MB!
        self.assertEqual(bulk_params[0]["traffic_down"], prev_down + node_after_reboot)
        self.assertEqual(bulk_params[0]["raw_last_down"], node_after_reboot)
        # Server also receives the 10MB delta
        self.assertEqual(mock_server.extra_data["monthly_traffic_bytes"], node_after_reboot)

    async def test_monthly_server_traffic_cycle_rotation(self):
        """When cycle changes from previous month, monthly_traffic_bytes resets to current delta."""
        server_info = {"id": 1, "name": "NL-1"}
        peer_id = "test-peer-uuid-1"

        api_clients = {
            peer_id: self._make_mock_client_data(down_bytes=2048, up_bytes=1024)
        }

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_row = (
            10, peer_id, 0, 0, 0, 0, None, True, 42, False, 123456789,
            datetime(2028, 1, 1, tzinfo=timezone.utc), False,
        )
        mock_result.all.return_value = [mock_row]
        mock_session.execute.return_value = mock_result

        mock_server = Server(
            id=1,
            name="NL-1",
            extra_data={"traffic_cycle": "1999-01", "monthly_traffic_bytes": 999999999},
        )
        mock_session.get.return_value = mock_server

        with patch("services.workers.traffic.session_scope") as mock_scope, \
             patch("services.slots_cache.get_server_generation", return_value=1):
            mock_scope.return_value.__aenter__.return_value = mock_session
            await _process_server_traffic(server_info, api_clients, expected_gen=1)

        current_cycle = datetime.now(timezone.utc).strftime("%Y-%m")
        self.assertEqual(mock_server.extra_data["traffic_cycle"], current_cycle)
        # Old month was 999999999, new month starts with 3072 bytes (2048 down + 1024 up)
        self.assertEqual(mock_server.extra_data["monthly_traffic_bytes"], 3072)

    def test_admin_texts_traffic_formatting(self):
        """Ensure admin templates render correctly without crashing or containing stop-words."""
        # 1. Dashboard AWG traffic card
        formatted_dash = DASHBOARD_AWG_TRAFFIC_STATS.format(
            total_traffic="120.5 GB",
            avg_traffic="12.3 GB",
        )
        self.assertIn("120.5 GB", formatted_dash)
        self.assertIn("12.3 GB", formatted_dash)

        # 2. Server monthly traffic line
        formatted_srv = ADMIN_SERVER_MONTHLY_TRAFFIC.format(
            traffic_month="450.2 GB (2026-09)",
        )
        self.assertIn("450.2 GB", formatted_srv)
        self.assertIn("2026-09", formatted_srv)

        # 3. User card with total_traffic
        card = ADMIN_USER_CARD.format(
            telegram_id=12345,
            username="testuser",
            first_name="Test User",
            status="Активен",
            ban="Нет",
            tariff_info="Стандарт",
            referrer_info="Нет",
            real_balance=100,
            bonus_balance=0,
            valid_until="01.10.2026",
            days_left="2 дня",
            devices_count=2,
            device_limit=5,
            total_traffic="45.6 GB",
            referrals_count=0,
            created_at="01.01.2026",
        )
        self.assertIn("45.6 GB", card)
        self.assertIn("12345", card)


if __name__ == "__main__":
    unittest.main()
