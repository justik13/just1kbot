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

    def test_traffic_watchdog_backup_restoration_and_accounting(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "traffic.json")
            bak_file = state_file + ".bak"

            def update_tx(raw_tx: int, cycle: str, initial_offset_gb: float = 0) -> int:
                data = None
                for path in (state_file, bak_file):
                    if os.path.exists(path):
                        try:
                            with open(path, "r", encoding="utf-8") as f:
                                c = json.load(f)
                                if isinstance(c, dict) and "accumulated_tx" in c:
                                    data = c
                                    break
                        except Exception:
                            continue

                is_fresh = False
                if data is None:
                    is_fresh = True
                    data = {
                        "cycle": cycle,
                        "raw_tx_last": raw_tx,
                        "accumulated_tx": max(0, int(initial_offset_gb * (1024 ** 3))),
                        "warn_sent": False,
                        "cutoff_sent": False,
                    }

                saved_cycle = data.get("cycle", "")
                accumulated = int(data.get("accumulated_tx", 0))
                raw_tx_last = int(data.get("raw_tx_last", 0))
                warn_sent = bool(data.get("warn_sent", False))
                cutoff_sent = bool(data.get("cutoff_sent", False))

                if not is_fresh:
                    if saved_cycle != cycle:
                        accumulated = 0
                        raw_tx_last = raw_tx
                        warn_sent = False
                        cutoff_sent = False
                    else:
                        if raw_tx >= raw_tx_last:
                            delta = raw_tx - raw_tx_last
                        else:
                            delta = raw_tx
                        accumulated += delta
                        raw_tx_last = raw_tx

                data["cycle"] = cycle
                data["accumulated_tx"] = accumulated
                data["raw_tx_last"] = raw_tx_last
                data["warn_sent"] = warn_sent
                data["cutoff_sent"] = cutoff_sent

                if os.path.exists(state_file):
                    with open(bak_file, "w", encoding="utf-8") as fbak:
                        json.dump(data, fbak)

                with open(state_file, "w", encoding="utf-8") as fp:
                    json.dump(data, fp)

                return accumulated

            # 1. Initial boot with 10 GB offset mid-cycle
            acc1 = update_tx(1000, "2026-09-01", initial_offset_gb=10)
            self.assertEqual(acc1, 10 * 1024**3)

            # 2. Traffic moves: raw rises from 1000 to 5000 (+4000)
            acc2 = update_tx(5000, "2026-09-01")
            self.assertEqual(acc2, 10 * 1024**3 + 4000)

            # 3. Corrupt primary state file: verify fallback to .bak
            with open(state_file, "w", encoding="utf-8") as f:
                f.write("{corrupt-json-truncated...")

            acc3 = update_tx(6000, "2026-09-01")
            self.assertEqual(acc3, 10 * 1024**3 + 5000)

            # 4. Next billing cycle arrives: accumulated resets to 0
            acc4 = update_tx(1000, "2026-10-01")
            self.assertEqual(acc4, 0)


if __name__ == "__main__":
    unittest.main()
