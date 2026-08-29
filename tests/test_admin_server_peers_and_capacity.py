import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers.admin.dashboard import _get_servers_capacity_summary
from bot.handlers.admin.servers.common import _build_servers_list_text_and_kb
from bot.handlers.admin.servers.peers_routes import show_server_peers
from bot.handlers.admin.users.list_routes import show_user_card
from bot.keyboards.admin.servers import get_admin_server_card_keyboard
from bot.keyboards.admin.users import get_admin_user_card_keyboard
from database.repositories.users_repo import get_user_filter_counts
from utils.datetime_helpers import now_utc


class TestAdminServerPeersAndCapacity(unittest.IsolatedAsyncioTestCase):
    async def test_get_user_filter_counts_aggregates_correctly(self):
        session = AsyncMock()
        mock_result = SimpleNamespace(
            total=34,
            new_7d=5,
            active=21,
            expiring_3d=2,
            expired=13,
            banned=1,
        )
        scalars_mock = MagicMock()
        scalars_mock.one.return_value = mock_result
        session.execute.return_value = scalars_mock

        counts = await get_user_filter_counts(session)
        self.assertEqual(counts["all"], 34)
        self.assertEqual(counts["new_7d"], 5)
        self.assertEqual(counts["active"], 21)
        self.assertEqual(counts["expiring_3d"], 2)
        self.assertEqual(counts["expired"], 13)
        self.assertEqual(counts["banned"], 1)

    async def test_dashboard_server_capacity_summary_shows_admin_peers(self):
        server1 = SimpleNamespace(id=1, name="Netherlands", country_flag="🇳🇱", max_clients=240)
        session = AsyncMock()

        with patch("database.repositories.servers_repo.get_active_servers", AsyncMock(return_value=[server1])), \
             patch("database.repositories.servers_repo.get_server_peer_counts", AsyncMock(return_value={1: 21})), \
             patch("services.slots_cache.get_cached_peer_count", return_value=23):
            summary = await _get_servers_capacity_summary(session)

            self.assertIn("Netherlands", summary)
            self.assertIn("23/240", summary)
            self.assertIn("(+2 админ)", summary)

    async def test_dashboard_server_capacity_summary_normal_when_matching(self):
        server1 = SimpleNamespace(id=1, name="Netherlands", country_flag="🇳🇱", max_clients=240)
        session = AsyncMock()

        with patch("database.repositories.servers_repo.get_active_servers", AsyncMock(return_value=[server1])), \
             patch("database.repositories.servers_repo.get_server_peer_counts", AsyncMock(return_value={1: 21})), \
             patch("services.slots_cache.get_cached_peer_count", return_value=21):
            summary = await _get_servers_capacity_summary(session)

            self.assertIn("21/240", summary)
            self.assertNotIn("админ", summary)

    async def test_server_list_buttons_include_capacity(self):
        server1 = SimpleNamespace(id=1, name="Netherlands", country_flag="🇳🇱", is_active=True, max_clients=240)
        session = AsyncMock()

        with patch("bot.handlers.admin.servers.common.get_server_peer_counts", AsyncMock(return_value={1: 21})), \
             patch("services.slots_cache.get_cached_peer_count", return_value=23):
            rendered, builder = await _build_servers_list_text_and_kb(
                [server1], page=1, total_pages=1, total=1, session=session
            )
            buttons = [b for row in builder.as_markup().inline_keyboard for b in row]
            labels = [b.text for b in buttons]

            self.assertTrue(any("21(+2)/240" in lbl for lbl in labels))

    async def test_server_card_keyboard_has_peers_button(self):
        kb = get_admin_server_card_keyboard(server_id=1, is_active=True, used_clients=23, max_clients=240)
        callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
        texts_list = [b.text for row in kb.inline_keyboard for b in row]

        self.assertIn("admin_server_peers:1:1", callbacks)
        self.assertIn("admin_users_filter:server:1:1", callbacks)
        self.assertTrue(any("23/240" in t for t in texts_list))

    async def test_user_card_keyboard_back_callback_navigation(self):
        # Default back callback
        kb_default = get_admin_user_card_keyboard(user_id=100, is_banned=False)
        back_btn = kb_default.inline_keyboard[-1][0]
        self.assertEqual(back_btn.callback_data, "admin_users")

        # Custom contextual back callback from server peers
        kb_context = get_admin_user_card_keyboard(user_id=100, is_banned=False, back_callback="admin_server_peers:5:2")
        back_btn_custom = kb_context.inline_keyboard[-1][0]
        self.assertEqual(back_btn_custom.callback_data, "admin_server_peers:5:2")

    async def test_show_user_card_preserves_navigation_context(self):
        session = AsyncMock()
        state = AsyncMock()
        user = SimpleNamespace(telegram_id=999, id=1, is_banned=False, is_bot_blocked=False, username="bob", first_name="Bob")

        # Mock callback from users filter
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=1),
            data="admin_user_card:999:users:expiring_3d:none:3",
            answer=AsyncMock(),
        )

        with patch("bot.handlers.admin.users.list_routes.is_admin", return_value=True), \
             patch("bot.handlers.admin.users.list_routes._get_user_with_profiles", AsyncMock(return_value=user)), \
             patch("bot.handlers.admin.users.list_routes._render_user_card", AsyncMock()) as mock_render:
            await show_user_card(callback, state, session)
            mock_render.assert_called_once_with(callback, user, session, back_callback="admin_users_filter:expiring_3d:none:3")

        # Mock callback from server peers
        callback_peers = SimpleNamespace(
            from_user=SimpleNamespace(id=1),
            data="admin_user_card:999:server_peers:7:2",
            answer=AsyncMock(),
        )
        with patch("bot.handlers.admin.users.list_routes.is_admin", return_value=True), \
             patch("bot.handlers.admin.users.list_routes._get_user_with_profiles", AsyncMock(return_value=user)), \
             patch("bot.handlers.admin.users.list_routes._render_user_card", AsyncMock()) as mock_render_peers:
            await show_user_card(callback_peers, state, session)
            mock_render_peers.assert_called_once_with(callback_peers, user, session, back_callback="admin_server_peers:7:2")

    async def test_show_server_peers_correlates_bot_and_external_clients(self):
        server = SimpleNamespace(id=1, name="Netherlands", country_flag="🇳🇱", api_url="https://vpn.example", api_key="secret")
        session = AsyncMock()
        session.get = AsyncMock(return_value=server)

        bot_user = SimpleNamespace(id=10, telegram_id=55555, username="alice", first_name="Alice")
        bot_profile = SimpleNamespace(
            id=101,
            server_id=1,
            peer_id="key-bot-alice",
            user=bot_user,
            device_name="iPhone 15",
            allocated_ip="10.8.0.2",
            last_connected=now_utc(),
            provisioning_status="active",
        )
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [bot_profile]
        session.scalars = AsyncMock(return_value=scalars_mock)

        # 1 matching bot client, 1 external admin client
        amnezia_bot_peer = SimpleNamespace(id="key-bot-alice", client_name="iPhone 15")
        amnezia_admin_peer = SimpleNamespace(id="key-admin-laptop", client_name="Windows 11 PC")

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=1),
            data="admin_server_peers:1:1",
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock()),
        )

        with patch("bot.handlers.admin.servers.peers_routes.is_admin", return_value=True), \
             patch("bot.handlers.admin.servers.peers_routes.AmneziaClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.get_all_clients = AsyncMock(return_value=[amnezia_bot_peer, amnezia_admin_peer])
            mock_client_cls.return_value = mock_client

            await show_server_peers(callback, session)

            callback.message.edit_text.assert_called_once()
            rendered_text = callback.message.edit_text.call_args[0][0]
            reply_markup = callback.message.edit_text.call_args.kwargs["reply_markup"]

            # Assert header counts
            self.assertIn("Всего: 2", rendered_text)
            self.assertIn("Пользователи бота: <b>1</b>", rendered_text)
            self.assertIn("Внешние / Admin: <b>1</b>", rendered_text)

            # Assert rows
            self.assertIn("@alice", rendered_text)
            self.assertIn("Внешний / Admin", rendered_text)
            self.assertIn("Windows 11 PC", rendered_text)

            # Assert keyboard buttons and contextual navigation
            buttons = [b for row in reply_markup.inline_keyboard for b in row]
            button_callbacks = [b.callback_data for b in buttons]

            self.assertIn("admin_user_card:55555:server_peers:1:1", button_callbacks)
            self.assertIn("admin_server_peer_info:1", button_callbacks)
            self.assertIn("admin_server_card:1", button_callbacks)
            self.assertIn("admin_users_filter:server:1:1", button_callbacks)
