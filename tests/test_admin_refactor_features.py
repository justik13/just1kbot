import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from utils.formatters import format_admin_breadcrumbs
from database.repositories.system_settings_repo import set_system_setting
from database.repositories.users_repo import search_user_flexible
from services.workers.node_monitor import check_node_resources_and_alerts
from services.amnezia_client import AmneziaClient



class TestAdminRefactorFeatures(unittest.IsolatedAsyncioTestCase):
    def test_format_admin_breadcrumbs(self):
        res = format_admin_breadcrumbs("🖥 Серверы", "Node #1")
        self.assertIn("🏠 Админка ➔ 🖥 Серверы ➔ Node #1", res)
        self.assertTrue(res.startswith("📌 <b>"))

    async def test_system_settings_repo(self):
        session = AsyncMock()
        session.add = MagicMock()
        session.get.return_value = None


        with patch("database.repositories.system_settings_repo.now_utc"):
            await set_system_setting(session, "mtproto_proxy_url", "https://t.me/proxy?server=127.0.0.1", updated_by=123)
            session.add.assert_called_once()
            session.flush.assert_called_once()

    async def test_search_user_flexible_username(self):
        session = AsyncMock()
        user_mock = MagicMock()
        user_mock.username = "test_user"

        with (
            patch("database.repositories.users_repo.get_user_by_telegram_id", return_value=None),
            patch("database.repositories.users_repo.get_user_by_username", return_value=user_mock),
        ):
            res = await search_user_flexible(session, "@test_user")
            self.assertEqual(res, user_mock)

    async def test_node_monitor_disk_alert(self):
        bot = AsyncMock()
        server = MagicMock()
        server.id = 1
        server.name = "DE-1"
        server.api_url = "http://127.0.0.1:3000"
        server.api_key = "secret"

        client_mock = AsyncMock()
        client_mock.healthcheck.return_value = True
        client_mock.get_server_load.return_value = {"disk_percent": 92.5}

        with (
            patch("services.workers.node_monitor.session_scope"),
            patch("services.workers.node_monitor.get_active_servers", return_value=[server]),
            patch("services.workers.node_monitor.AmneziaClient", return_value=client_mock),
            patch("services.workers.node_monitor.get_settings") as mock_settings,
            patch.dict("services.workers.node_monitor._last_alert_time", {}, clear=True),
        ):
            settings_obj = MagicMock()
            settings_obj.ADMIN_IDS = [999]
            mock_settings.return_value = settings_obj



            await check_node_resources_and_alerts(bot)

            bot.send_message.assert_called_once()
            call_kwargs = bot.send_message.call_args[1]
            self.assertEqual(call_kwargs["chat_id"], 999)
            self.assertIn("Диск VPN-ноды забит > 85%", call_kwargs["text"])

    async def test_node_monitor_down_alert(self):
        bot = AsyncMock()
        server = MagicMock()
        server.id = 2
        server.name = "NL-1"
        server.api_url = "http://127.0.0.1:3001"
        server.api_key = "secret"

        client_mock = AsyncMock()
        client_mock.healthcheck.return_value = False

        with (
            patch("services.workers.node_monitor.session_scope"),
            patch("services.workers.node_monitor.get_active_servers", return_value=[server]),
            patch("services.workers.node_monitor.AmneziaClient", return_value=client_mock),
            patch("services.workers.node_monitor.get_settings") as mock_settings,
            patch.dict("services.workers.node_monitor._last_alert_time", {}, clear=True),
        ):
            settings_obj = MagicMock()
            settings_obj.ADMIN_IDS = [888]
            mock_settings.return_value = settings_obj

            await check_node_resources_and_alerts(bot)

            bot.send_message.assert_called_once()
            call_kwargs = bot.send_message.call_args[1]
            self.assertEqual(call_kwargs["chat_id"], 888)
            self.assertIn("VPN-нода недоступна", call_kwargs["text"])


    async def test_amnezia_client_get_server_load(self):
        client = AmneziaClient("http://localhost:3000", "testkey")
        with patch.object(client, "_request", return_value={"cpu_percent": 12.0, "disk_percent": 45.0}):
            res = await client.get_server_load()
            self.assertIsNotNone(res)
            self.assertEqual(res["cpu_percent"], 12.0)


if __name__ == "__main__":
    unittest.main()
