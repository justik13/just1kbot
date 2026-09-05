import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from database.repositories.system_settings_repo import set_system_setting
from database.repositories.users_repo import search_user_flexible
from services.amnezia_client import AmneziaClient
from services.workers.node_monitor import check_node_resources_and_alerts
from bot.formatters import format_admin_breadcrumbs


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

    async def test_search_user_flexible_telegram_id_found(self):
        session = AsyncMock()
        user_mock = MagicMock()
        user_mock.telegram_id = 8141287721

        with (
            patch("database.repositories.users_repo.get_user_by_telegram_id", return_value=user_mock),
            patch("database.repositories.users_repo.get_user_by_username", return_value=None),
        ):
            res = await search_user_flexible(session, "8141287721")
            self.assertEqual(res, user_mock)
            session.execute.assert_not_called()

    async def test_search_user_flexible_telegram_id_not_found_large_number(self):
        # When telegram_id > 2^31 - 1 is not found, it must NOT execute User.id == num_id
        # which would cause asyncpg int32 OverflowError/DataError.
        session = AsyncMock()

        with (
            patch("database.repositories.users_repo.get_user_by_telegram_id", return_value=None) as mock_get_tg,
            patch("database.repositories.users_repo.get_user_by_username", return_value=None) as mock_get_username,
        ):
            res = await search_user_flexible(session, "8141287721")
            self.assertIsNone(res)
            mock_get_tg.assert_called_once_with(session, 8141287721)
            mock_get_username.assert_called_once_with(session, "8141287721")
            # Crucial: session.execute MUST NOT be called with User.id == 8141287721
            session.execute.assert_not_called()

    async def test_search_user_flexible_huge_number_above_int64(self):
        session = AsyncMock()

        with (
            patch("database.repositories.users_repo.get_user_by_telegram_id", return_value=None) as mock_get_tg,
            patch("database.repositories.users_repo.get_user_by_username", return_value=None) as mock_get_username,
        ):
            res = await search_user_flexible(session, "9999999999999999999999999999999999999999")
            self.assertIsNone(res)
            # Numbers exceeding int64 must not be passed to get_user_by_telegram_id
            mock_get_tg.assert_not_called()
            mock_get_username.assert_called_once()
            session.execute.assert_not_called()

    async def test_search_user_flexible_db_id_within_int32(self):
        session = AsyncMock()
        mock_result = MagicMock()
        user_mock = MagicMock()
        user_mock.id = 42
        mock_result.scalar_one_or_none.return_value = user_mock
        session.execute.return_value = mock_result

        with (
            patch("database.repositories.users_repo.get_user_by_telegram_id", return_value=None),
            patch("database.repositories.users_repo.get_user_by_username", return_value=None),
        ):
            res = await search_user_flexible(session, "42")
            self.assertEqual(res, user_mock)
            session.execute.assert_called_once()

    async def test_repo_int_bounds_protection(self):
        from database.repositories.users_repo import get_user_by_id, get_user_by_telegram_id
        session = AsyncMock()

        # Out-of-bounds user_id (int32 limit: 2_147_483_647)
        self.assertIsNone(await get_user_by_id(session, 8141287721))
        self.assertIsNone(await get_user_by_id(session, 0))
        self.assertIsNone(await get_user_by_id(session, -1))

        # Out-of-bounds telegram_id (int64 limit: 9_223_372_036_854_775_807)
        self.assertIsNone(await get_user_by_telegram_id(session, 10**20))
        self.assertIsNone(await get_user_by_telegram_id(session, 0))
        self.assertIsNone(await get_user_by_telegram_id(session, -1))
        session.execute.assert_not_called()

    def test_parse_callback_id_bounds(self):
        from utils.callbacks import parse_callback_id, parse_callback_int
        self.assertEqual(parse_callback_id("admin_server_card:123"), 123)
        self.assertEqual(parse_callback_id("admin_user_card:8141287721"), 8141287721)
        self.assertIsNone(parse_callback_id("admin_user_card:999999999999999999999999999999"))
        self.assertIsNone(parse_callback_int(["admin", "999999999999999999999999999999"], 1))

    async def test_node_monitor_disk_alert(self):
        bot = AsyncMock()
        server = MagicMock()
        server.id = 1
        server.name = "DE-1"
        server.api_url = "http://127.0.0.1:3000"
        server.api_key = "secret"

        client_mock = AsyncMock()
        client_mock.healthcheck = AsyncMock(return_value=True)
        client_mock.get_server_load = AsyncMock(return_value={"disk_percent": 92.5})

        mock_scope = AsyncMock()
        mock_scope.__aenter__.return_value = AsyncMock()

        settings_obj = MagicMock()
        settings_obj.ADMIN_IDS = [999]

        server.is_active = True
        server.disabled_reason = None
        server.health_state = "ONLINE"
        server.problem_started_at = None
        server.next_check_at = None

        with (
            patch("services.workers.node_monitor.session_scope", return_value=mock_scope),
            patch("services.workers.node_monitor.get_all_servers", AsyncMock(return_value=[server])),
            patch("services.workers.node_monitor.get_server_by_id", AsyncMock(return_value=server)),
            patch("database.repositories.servers_repo.get_all_servers", AsyncMock(return_value=[server])),
            patch("services.workers.node_monitor.update_server_health_snapshot", AsyncMock(return_value=(server, True))),
            patch("services.workers.node_monitor.AmneziaClient", return_value=client_mock),
            patch("services.workers.node_monitor.get_settings", return_value=settings_obj),
            patch("config.settings.get_settings", return_value=settings_obj),
        ):
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
        server.is_active = True
        server.disabled_reason = None
        server.health_state = "ONLINE"
        server.problem_started_at = None
        server.next_check_at = None

        client_mock = AsyncMock()
        client_mock.healthcheck = AsyncMock(return_value=False)

        mock_scope = AsyncMock()
        mock_scope.__aenter__.return_value = AsyncMock()

        settings_obj = MagicMock()
        settings_obj.ADMIN_IDS = [888]

        with (
            patch("services.workers.node_monitor.session_scope", return_value=mock_scope),
            patch("services.workers.node_monitor.get_all_servers", AsyncMock(return_value=[server])),
            patch("services.workers.node_monitor.get_server_by_id", AsyncMock(return_value=server)),
            patch("database.repositories.servers_repo.get_all_servers", AsyncMock(return_value=[server])),
            patch("services.workers.node_monitor.update_server_health_snapshot", AsyncMock(return_value=(server, True))),
            patch("services.workers.node_monitor.AmneziaClient", return_value=client_mock),
            patch("services.workers.node_monitor.get_settings", return_value=settings_obj),
            patch("config.settings.get_settings", return_value=settings_obj),
        ):
            # Tick 1: FAIL #1 -> WAITING_CONFIRMATION
            await check_node_resources_and_alerts(bot)
            bot.send_message.assert_not_called()

            # Fast-forward 30s confirmation window
            import time

            from services.workers.node_monitor import get_server_monitor_state
            st = get_server_monitor_state(2)
            st.next_check_at = time.monotonic() - 1.0

            # Tick 2: FAIL #2 -> PROBLEM alert
            await check_node_resources_and_alerts(bot)

            bot.send_message.assert_called_once()
            call_kwargs = bot.send_message.call_args[1]
            self.assertEqual(call_kwargs["chat_id"], 888)
            self.assertIn("Проблема с VPN-сервером", call_kwargs["text"])





    async def test_amnezia_client_get_server_load(self):
        client = AmneziaClient("http://localhost:3000", "testkey")
        with patch.object(client, "_request", return_value={"cpu_percent": 12.0, "disk_percent": 45.0}):
            res = await client.get_server_load()
            self.assertIsNotNone(res)
            self.assertEqual(res["cpu_percent"], 12.0)


if __name__ == "__main__":
    unittest.main()
