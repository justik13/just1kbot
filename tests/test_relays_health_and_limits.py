import datetime
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from aiogram.types import CallbackQuery, User as TgUser

from bot.handlers.admin.servers.card_routes import show_server_relays
from config.constants import XRAY_PROTOCOL
from config.enums import ServerHealthState
from database.models import Server
from services.xray_node_client import XrayNodeClient


class TestRelaysHealthAndNodeLimits(unittest.IsolatedAsyncioTestCase):
    async def test_xray_node_client_get_relays_health_success(self):
        client = XrayNodeClient()
        fake_response = {
            "status": "ok",
            "count": 2,
            "all_healthy": True,
            "relays": [
                {"name": "DE", "code": "de", "ip": "1.2.3.4", "port": 10443, "healthy": True, "rtt_ms": 35.5, "error": None},
                {"name": "PL", "code": "pl", "ip": "5.6.7.8", "port": 10443, "healthy": True, "rtt_ms": 42.1, "error": None},
            ],
        }

        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = (200, fake_response, None)
            ok, data, err = await client.get_relays_health("https://origin.node:8444", "test-key")

            self.assertTrue(ok)
            self.assertIsNone(err)
            self.assertEqual(data["status"], "ok")
            self.assertEqual(len(data["relays"]), 2)
            mock_req.assert_called_once_with("GET", "https://origin.node:8444/v1/relays/health", {
                "X-API-Key": "test-key",
                "Accept": "application/json",
                "Content-Type": "application/json",
            })

    async def test_xray_node_client_get_relays_health_failure(self):
        client = XrayNodeClient()
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = (500, None, "Internal Server Error")
            ok, data, err = await client.get_relays_health("https://origin.node:8444", "test-key")

            self.assertFalse(ok)
            self.assertIsNone(data)
            self.assertEqual(err, "Internal Server Error")

    async def test_show_server_relays_handler_renders_status(self):
        server = Server(
            id=42,
            name="Origin-Main",
            country_flag="🇷🇺",
            protocol=XRAY_PROTOCOL,
            api_url="https://origin.node:8444",
            api_key="key-123",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.ONLINE,
        )

        mock_session = AsyncMock()
        mock_callback = AsyncMock(spec=CallbackQuery)
        mock_callback.answer = AsyncMock()
        mock_callback.from_user = TgUser(id=1001, is_bot=False, first_name="Admin")
        mock_callback.data = "admin_server_relays:42"
        mock_callback.message = AsyncMock()

        relays_data = {
            "status": "degraded",
            "count": 2,
            "all_healthy": False,
            "relays": [
                {"name": "Германия (Frankfurt)", "code": "de", "ip": "1.2.3.4", "port": 10443, "healthy": True, "rtt_ms": 32.4, "error": None},
                {"name": "Польша (Warsaw)", "code": "pl", "ip": "5.6.7.8", "port": 10443, "healthy": False, "rtt_ms": None, "error": "Timeout (2.5s)"},
            ],
        }

        with patch("bot.handlers.admin.servers.card_routes.is_admin", return_value=True), \
             patch("bot.handlers.admin.servers.card_routes.get_server_by_id", new_callable=AsyncMock, return_value=server), \
             patch.object(XrayNodeClient, "check_health", new_callable=AsyncMock, return_value=(True, "epoch-1", {})), \
             patch.object(XrayNodeClient, "get_relays_health", new_callable=AsyncMock, return_value=(True, relays_data, None)):

            await show_server_relays(mock_callback, mock_session)

            mock_callback.message.edit_text.assert_called_once()
            call_args = mock_callback.message.edit_text.call_args
            rendered_text = call_args.args[0] if call_args.args else call_args.kwargs.get("text", "")
            self.assertIn("Origin-Main", rendered_text)
            self.assertIn("Frankfurt", rendered_text)
            self.assertIn("Warsaw", rendered_text)
            self.assertIn("32.4 ms", rendered_text)
            self.assertIn("Timeout", rendered_text)
            self.assertIn("🟢 Онлайн (RTT:", rendered_text)

    async def test_show_server_relays_origin_offline_renders_offline_badge(self):
        server = Server(
            id=42,
            name="Origin-Down",
            country_flag="🇷🇺",
            protocol=XRAY_PROTOCOL,
            api_url="https://origin.down:8444",
            api_key="key-down",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.PROBLEM,
        )

        mock_session = AsyncMock()
        mock_callback = AsyncMock(spec=CallbackQuery)
        mock_callback.answer = AsyncMock()
        mock_callback.from_user = TgUser(id=1001, is_bot=False, first_name="Admin")
        mock_callback.data = "admin_server_relays:42"
        mock_callback.message = AsyncMock()

        with patch("bot.handlers.admin.servers.card_routes.is_admin", return_value=True), \
             patch("bot.handlers.admin.servers.card_routes.get_server_by_id", new_callable=AsyncMock, return_value=server), \
             patch.object(XrayNodeClient, "check_health", new_callable=AsyncMock, return_value=(False, None, "Connection refused")), \
             patch.object(XrayNodeClient, "get_relays_health", new_callable=AsyncMock) as mock_relays:

            await show_server_relays(mock_callback, mock_session)

            # get_relays_health should not be called if Origin check_health failed
            mock_relays.assert_not_called()
            mock_callback.message.edit_text.assert_called_once()
            call_args = mock_callback.message.edit_text.call_args
            rendered_text = call_args.args[0] if call_args.args else call_args.kwargs.get("text", "")
            self.assertIn("Origin (Прямой выход):</b> 🔴 Офлайн", rendered_text)
            self.assertNotIn("🟢 Онлайн", rendered_text)

    async def test_show_server_relays_empty_renders_ru_access_note(self):
        server = Server(
            id=42,
            name="Origin-Solo",
            country_flag="🇷🇺",
            protocol=XRAY_PROTOCOL,
            api_url="https://origin.solo:8444",
            api_key="key-solo",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.ONLINE,
        )

        mock_session = AsyncMock()
        mock_callback = AsyncMock(spec=CallbackQuery)
        mock_callback.answer = AsyncMock()
        mock_callback.from_user = TgUser(id=1001, is_bot=False, first_name="Admin")
        mock_callback.data = "admin_server_relays:42"
        mock_callback.message = AsyncMock()

        with patch("bot.handlers.admin.servers.card_routes.is_admin", return_value=True), \
             patch("bot.handlers.admin.servers.card_routes.get_server_by_id", new_callable=AsyncMock, return_value=server), \
             patch.object(XrayNodeClient, "check_health", new_callable=AsyncMock, return_value=(True, "epoch-1", {})), \
             patch.object(XrayNodeClient, "get_relays_health", new_callable=AsyncMock, return_value=(True, {"status": "empty", "relays": []}, None)):

            await show_server_relays(mock_callback, mock_session)

            mock_callback.message.edit_text.assert_called_once()
            call_args = mock_callback.message.edit_text.call_args
            rendered_text = call_args.args[0] if call_args.args else call_args.kwargs.get("text", "")
            self.assertIn("Доступен прямой выход в зону RU", rendered_text)

    def test_traffic_watchdog_billing_cycle_python_logic(self):
        def get_cycle(now: datetime.datetime, reset_day: int) -> str:
            r_day = max(1, min(28, reset_day))
            if now.day >= r_day:
                cycle_start = datetime.date(now.year, now.month, r_day)
            else:
                first_this_month = datetime.date(now.year, now.month, 1)
                last_prev_month = first_this_month - datetime.timedelta(days=1)
                cycle_start = datetime.date(last_prev_month.year, last_prev_month.month, min(r_day, last_prev_month.day))
            return cycle_start.strftime('%Y-%m-%d')

        d1 = datetime.datetime(2026, 9, 15, 12, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(get_cycle(d1, 1), "2026-09-01")

        d2 = datetime.datetime(2026, 9, 5, 12, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(get_cycle(d2, 10), "2026-08-10")

    def test_traffic_watchdog_accounting_and_decision_logic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "traffic.json")

            def check_traffic(tx_raw: int, limit_gb: int, reset_day: int, cycle: str, cutoff_active: bool):
                data = {}
                if os.path.exists(state_file):
                    try:
                        with open(state_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                    except Exception:
                        pass

                saved_cycle = data.get("cycle", "")
                accumulated = int(data.get("accumulated_tx", 0))
                raw_tx_last = int(data.get("raw_tx_last", 0))
                warn_sent = bool(data.get("warn_sent", False))
                cutoff_sent = bool(data.get("cutoff_sent", False))

                if saved_cycle != cycle:
                    accumulated = 0
                    raw_tx_last = tx_raw
                    warn_sent = False
                    cutoff_sent = False
                else:
                    if tx_raw >= raw_tx_last:
                        delta = tx_raw - raw_tx_last
                    else:
                        delta = tx_raw
                    accumulated += delta
                    raw_tx_last = tx_raw

                limit_bytes = limit_gb * (1024 ** 3)
                pct = (float(accumulated) / float(limit_bytes) * 100) if limit_bytes > 0 else 0.0

                action = "none"
                if accumulated < limit_bytes and cutoff_active:
                    action = "resume"
                    cutoff_sent = False
                elif accumulated >= limit_bytes:
                    if not cutoff_sent:
                        action = "cutoff"
                        cutoff_sent = True
                    else:
                        action = "ensure_stopped"
                else:
                    if pct >= 90.0 and not warn_sent:
                        action = "warn"
                        warn_sent = True

                data = {
                    "cycle": cycle,
                    "raw_tx_last": raw_tx_last,
                    "accumulated_tx": accumulated,
                    "warn_sent": warn_sent,
                    "cutoff_sent": cutoff_sent,
                }
                with open(state_file, "w", encoding="utf-8") as fp:
                    json.dump(data, fp)

                return action, accumulated, pct

            # 1. Initial run: raw counter 1000 MB
            mb = 1024 * 1024
            gb = 1024 * 1024 * 1024
            act, acc, pct = check_traffic(1000 * mb, 8000, 1, "2026-09-01", False)
            self.assertEqual(act, "none")
            self.assertEqual(acc, 0)

            # 2. Traffic moves: +5000 GB -> under 90% threshold
            act, acc, pct = check_traffic(1000 * mb + 5000 * gb, 8000, 1, "2026-09-01", False)
            self.assertEqual(act, "none")
            self.assertEqual(acc, 5000 * gb)

            # 3. Traffic reaches 7300 GB (91.25%) -> triggers 'warn' once
            act, acc, pct = check_traffic(1000 * mb + 7300 * gb, 8000, 1, "2026-09-01", False)
            self.assertEqual(act, "warn")
            self.assertGreaterEqual(pct, 90.0)

            # 4. Next run still 7300 GB -> no duplicate warn
            act, acc, pct = check_traffic(1000 * mb + 7300 * gb, 8000, 1, "2026-09-01", False)
            self.assertEqual(act, "none")

            # 5. Traffic reaches 8050 GB (100.6%) -> triggers 'cutoff'
            act, acc, pct = check_traffic(1000 * mb + 8050 * gb, 8000, 1, "2026-09-01", False)
            self.assertEqual(act, "cutoff")

            # 6. Next run still 8050 GB -> returns ensure_stopped to idempotently keep xray down
            act, acc, pct = check_traffic(1000 * mb + 8050 * gb, 8000, 1, "2026-09-01", True)
            self.assertEqual(act, "ensure_stopped")

            # 7. Next billing cycle arrives -> auto-resume
            act, acc, pct = check_traffic(500 * mb, 8000, 1, "2026-10-01", True)
            self.assertEqual(act, "resume")
            self.assertEqual(acc, 0)

    async def test_admin_server_card_keyboard_2_column_layout(self):
        from bot.keyboards.admin.servers import get_admin_server_card_keyboard

        # Xray server card layout
        kb_xray = get_admin_server_card_keyboard(server_id=1, is_active=True, is_xray=True)
        rows_xray = kb_xray.inline_keyboard
        self.assertEqual(len(rows_xray), 7, f"Expected 7 rows for Xray server card, got {len(rows_xray)}")
        # Row 1: Relays + Migrate (2 buttons)
        self.assertEqual(len(rows_xray[0]), 2)
        self.assertIn("admin_server_relays:1", rows_xray[0][0].callback_data)
        self.assertIn("admin_server_migrate:1", rows_xray[0][1].callback_data)
        # Row 2: Users + Broadcast (2 buttons)
        self.assertEqual(len(rows_xray[1]), 2)
        # Row 6: Toggle + Delete (2 buttons)
        self.assertEqual(len(rows_xray[5]), 2)
        # Row 7: Back to servers (1 button, full width)
        self.assertEqual(len(rows_xray[6]), 1)
        self.assertEqual(rows_xray[6][0].callback_data, "admin_servers")

        # AWG server card layout
        kb_awg = get_admin_server_card_keyboard(server_id=2, is_active=True, used_clients=10, max_clients=200, is_xray=False)
        rows_awg = kb_awg.inline_keyboard
        self.assertEqual(len(rows_awg), 7, f"Expected 7 rows for AWG server card, got {len(rows_awg)}")
        # Row 1: Peers + Users (2 buttons)
        self.assertEqual(len(rows_awg[0]), 2)
        self.assertIn("admin_server_peers:2:1", rows_awg[0][0].callback_data)
        # Row 5: Change Limit (1 button)
        self.assertEqual(len(rows_awg[4]), 1)
        # Row 6: Toggle + Delete (2 buttons)
        self.assertEqual(len(rows_awg[5]), 2)
        # Row 7: Back to servers (1 button)
        self.assertEqual(len(rows_awg[6]), 1)

    async def test_white_internet_overview_keyboard_2_column_layout(self):
        from bot.handlers.white_internet import get_white_internet_overview_keyboard
        from config.enums import WhiteInternetStatus
        from database.models import WhiteInternetSubscription

        sub = WhiteInternetSubscription(
            id=10,
            user_id=100,
            token="test-tok-123",
            status=WhiteInternetStatus.ACTIVE,
            device_limit=1,
            is_trial=False,
            expires_at=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=5),
        )

        kb = get_white_internet_overview_keyboard(sub, bot_domain="bot.example.com")
        rows = kb.inline_keyboard
        self.assertEqual(len(rows), 5, f"Expected 5 rows for active sub with topup & reset, got {len(rows)}")
        # Row 1: Copy link (1)
        self.assertEqual(len(rows[0]), 1)
        self.assertIsNotNone(rows[0][0].copy_text)
        # Row 2: Instructions (1)
        self.assertEqual(len(rows[1]), 1)
        self.assertEqual(rows[1][0].callback_data, "wl_show_link")
        # Row 3: Renew (1)
        self.assertEqual(len(rows[2]), 1)
        self.assertEqual(rows[2][0].callback_data, "wl_renew_preview")
        # Row 4: Topup + Add Device (2 buttons in row)
        self.assertEqual(len(rows[3]), 2)
        self.assertEqual(rows[3][0].callback_data, "wl_topup_menu")
        self.assertEqual(rows[3][1].callback_data, "wl_add_device_menu")
        # Row 5: Reset Devices + Back (2 buttons in row)
        self.assertEqual(len(rows[4]), 2)
        self.assertEqual(rows[4][0].callback_data, "wl_reset_devices")
        self.assertEqual(rows[4][1].callback_data, "back_to_main_menu")

    async def test_show_server_relays_config_error_status_renders_api_error(self):
        server = Server(
            id=42,
            name="Origin-Corrupt",
            country_flag="🇷🇺",
            protocol=XRAY_PROTOCOL,
            api_url="https://origin.corrupt:8444",
            api_key="key-corrupt",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.ONLINE,
        )

        mock_session = AsyncMock()
        mock_callback = AsyncMock(spec=CallbackQuery)
        mock_callback.answer = AsyncMock()
        mock_callback.from_user = TgUser(id=1001, is_bot=False, first_name="Admin")
        mock_callback.data = "admin_server_relays:42"
        mock_callback.message = AsyncMock()

        error_data = {
            "status": "error",
            "count": 0,
            "all_healthy": False,
            "relays": [],
            "error": "Failed to parse relays.json: invalid JSON",
        }

        with patch("bot.handlers.admin.servers.card_routes.is_admin", return_value=True), \
             patch("bot.handlers.admin.servers.card_routes.get_server_by_id", new_callable=AsyncMock, return_value=server), \
             patch.object(XrayNodeClient, "check_health", new_callable=AsyncMock, return_value=(True, "epoch-1", {})), \
             patch.object(XrayNodeClient, "get_relays_health", new_callable=AsyncMock, return_value=(True, error_data, None)):

            await show_server_relays(mock_callback, mock_session)

            mock_callback.message.edit_text.assert_called_once()
            call_args = mock_callback.message.edit_text.call_args
            rendered_text = call_args.args[0] if call_args.args else call_args.kwargs.get("text", "")
            self.assertIn("Ошибка проверки узлов:", rendered_text)
            self.assertIn("Failed to parse relays.json: invalid JSON", rendered_text)
            self.assertNotIn("На этом сервере нет подключенных Relay-узлов", rendered_text)


if __name__ == "__main__":
    unittest.main()
