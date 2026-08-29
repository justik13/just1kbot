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
            self.assertIn("(+2 внешн.)", summary)

    async def test_dashboard_server_capacity_summary_normal_when_matching(self):
        server1 = SimpleNamespace(id=1, name="Netherlands", country_flag="🇳🇱", max_clients=240)
        session = AsyncMock()

        with patch("database.repositories.servers_repo.get_active_servers", AsyncMock(return_value=[server1])), \
             patch("database.repositories.servers_repo.get_server_peer_counts", AsyncMock(return_value={1: 21})), \
             patch("services.slots_cache.get_cached_peer_count", return_value=21):
            summary = await _get_servers_capacity_summary(session)

            self.assertIn("21/240", summary)
            self.assertNotIn("внешн.", summary)

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

            # New header format: "На узле: <b>2</b> (Бот: 1, Внешние: 1)"
            self.assertIn("На узле: <b>2</b>", rendered_text)
            self.assertIn("Бот: 1", rendered_text)
            self.assertIn("Внешние: 1", rendered_text)

            # Assert rows
            self.assertIn("@alice", rendered_text)
            self.assertIn("Внешний пир", rendered_text)
            self.assertIn("Windows 11 PC", rendered_text)

            # Assert keyboard buttons and contextual navigation
            buttons = [b for row in reply_markup.inline_keyboard for b in row]
            button_callbacks = [b.callback_data for b in buttons]

            self.assertIn("admin_user_card:55555:server_peers:1:1", button_callbacks)
            self.assertIn("admin_server_peer_info:1", button_callbacks)
            self.assertIn("admin_server_card:1", button_callbacks)
            self.assertIn("admin_users_filter:server:1:1", button_callbacks)

    async def test_show_server_peers_api_returns_none_shows_error_banner(self):
        """When get_all_clients() returns None (API failure), show error banner, not empty list."""
        server = SimpleNamespace(id=2, name="Germany", country_flag="🇩🇪", api_url="https://de.example", api_key="key")
        session = AsyncMock()
        session.get = AsyncMock(return_value=server)

        bot_user = SimpleNamespace(id=20, telegram_id=77777, username="bob", first_name="Bob")
        bot_profile = SimpleNamespace(
            id=201, server_id=2, peer_id="key-bob", user=bot_user,
            device_name="Android", allocated_ip="10.8.0.3",
            last_connected=None, provisioning_status="active",
        )
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [bot_profile]
        session.scalars = AsyncMock(return_value=scalars_mock)

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=1),
            data="admin_server_peers:2:1",
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock()),
        )

        with patch("bot.handlers.admin.servers.peers_routes.is_admin", return_value=True), \
             patch("bot.handlers.admin.servers.peers_routes.AmneziaClient") as mock_client_cls:
            mock_client = MagicMock()
            # API returns None — node responded but data unavailable
            mock_client.get_all_clients = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            await show_server_peers(callback, session)

        rendered_text = callback.message.edit_text.call_args[0][0]
        # Must show error banner — NOT treat as empty node
        self.assertIn("API узла недоступен", rendered_text)
        # Must still show the profile from DB (not pretend node is empty)
        self.assertIn("@bob", rendered_text)
        # Must NOT show "Не на узле" when API is unavailable (state unknown)
        self.assertNotIn("Не на узле", rendered_text)

    async def test_show_server_peers_api_exception_shows_error_banner(self):
        """When get_all_clients() raises, show error banner instead of empty list."""
        server = SimpleNamespace(id=3, name="Poland", country_flag="🇵🇱", api_url="https://pl.example", api_key="key")
        session = AsyncMock()
        session.get = AsyncMock(return_value=server)

        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        session.scalars = AsyncMock(return_value=scalars_mock)

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=1),
            data="admin_server_peers:3:1",
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock()),
        )

        with patch("bot.handlers.admin.servers.peers_routes.is_admin", return_value=True), \
             patch("bot.handlers.admin.servers.peers_routes.AmneziaClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.get_all_clients = AsyncMock(side_effect=ConnectionError("timeout"))
            mock_client_cls.return_value = mock_client

            await show_server_peers(callback, session)

        rendered_text = callback.message.edit_text.call_args[0][0]
        self.assertIn("API узла недоступен", rendered_text)

    async def test_show_server_peers_shows_missing_note_when_db_profiles_absent_from_node(self):
        """When DB profiles have peer_id not on node, they appear in missing section with warning note."""
        server = SimpleNamespace(id=4, name="Finland", country_flag="🇫🇮", api_url="https://fi.example", api_key="key")
        session = AsyncMock()
        session.get = AsyncMock(return_value=server)

        bot_user = SimpleNamespace(id=30, telegram_id=33333, username="carol", first_name="Carol")
        # Profile with peer_id that is NOT present on node
        missing_profile = SimpleNamespace(
            id=301, server_id=4, peer_id="key-carol-missing", user=bot_user,
            device_name="MacBook", allocated_ip="10.8.0.5",
            last_connected=None, provisioning_status="active",
        )
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [missing_profile]
        session.scalars = AsyncMock(return_value=scalars_mock)

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=1),
            data="admin_server_peers:4:1",
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock()),
        )

        with patch("bot.handlers.admin.servers.peers_routes.is_admin", return_value=True), \
             patch("bot.handlers.admin.servers.peers_routes.AmneziaClient") as mock_client_cls:
            mock_client = MagicMock()
            # Node returns empty list — profile exists in DB but not on node
            mock_client.get_all_clients = AsyncMock(return_value=[])
            mock_client_cls.return_value = mock_client

            await show_server_peers(callback, session)

        rendered_text = callback.message.edit_text.call_args[0][0]
        # Should show "Не на узле" for missing profile
        self.assertIn("Не на узле", rendered_text)
        # Header must show missing note
        self.assertIn("Не на узле:", rendered_text)
        # Live peers = 0, missing = 1
        self.assertIn("На узле: <b>0</b>", rendered_text)

    async def test_server_list_shows_minus_when_cached_less_than_db(self):
        """When cached_used < db_used, server list button shows (-N) to alert admin."""
        server1 = SimpleNamespace(id=1, name="Netherlands", country_flag="🇳🇱", is_active=True, max_clients=240)
        session = AsyncMock()

        with patch("bot.handlers.admin.servers.common.get_server_peer_counts", AsyncMock(return_value={1: 21})), \
             patch("services.slots_cache.get_cached_peer_count", return_value=18):
            rendered, builder = await _build_servers_list_text_and_kb(
                [server1], page=1, total_pages=1, total=1, session=session
            )
            buttons = [b for row in builder.as_markup().inline_keyboard for b in row]
            labels = [b.text for b in buttons]

            # cached=18, db=21 → should show 18(-3)/240
            self.assertTrue(any("18(-3)/240" in lbl for lbl in labels))

    async def test_users_list_filter_labels_clean_when_counts_fail(self):
        """When get_user_filter_counts fails, filter buttons show clean labels without (0)."""
        from bot.handlers.admin.users.common import _build_users_list_text_and_kb
        session = AsyncMock()

        with patch("database.repositories.users_repo.get_user_filter_counts", AsyncMock(side_effect=RuntimeError("db error"))):
            rendered, builder = await _build_users_list_text_and_kb(
                [], page=1, total_pages=1, total=0, session=session
            )
            buttons = [b for row in builder.as_markup().inline_keyboard for b in row]
            labels = [b.text for b in buttons]

            # Filter buttons must NOT show "(0)" when query fails
            self.assertFalse(any("(0)" in lbl for lbl in labels))
            self.assertTrue(any("Все" in lbl for lbl in labels))

    async def test_show_server_peers_includes_deleting_profile_as_bot_peer(self):
        """A profile in 'deleting' lifecycle state with matching peer_id must be recognized as bot peer, not external."""
        server = SimpleNamespace(id=5, name="Estonia", country_flag="🇪🇪", api_url="https://ee.example", api_key="key")
        session = AsyncMock()
        session.get = AsyncMock(return_value=server)

        bot_user = SimpleNamespace(id=50, telegram_id=88888, username="dan", first_name="Dan")
        deleting_profile = SimpleNamespace(
            id=501, server_id=5, peer_id="key-deleting-dan", user=bot_user,
            device_name="Tablet", allocated_ip="10.8.0.8",
            last_connected=None, provisioning_status="deleting",
        )
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [deleting_profile]
        session.scalars = AsyncMock(return_value=scalars_mock)

        amnezia_peer = SimpleNamespace(id="key-deleting-dan", client_name="Tablet")

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=1),
            data="admin_server_peers:5:1",
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock()),
        )

        with patch("bot.handlers.admin.servers.peers_routes.is_admin", return_value=True), \
             patch("bot.handlers.admin.servers.peers_routes.AmneziaClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.get_all_clients = AsyncMock(return_value=[amnezia_peer])
            mock_client_cls.return_value = mock_client

            await show_server_peers(callback, session)

        rendered_text = callback.message.edit_text.call_args[0][0]
        # Should be classified under bot, not external
        self.assertIn("Бот: 1", rendered_text)
        self.assertIn("Внешние: 0", rendered_text)
        self.assertIn("@dan", rendered_text)
        self.assertNotIn("Внешний пир", rendered_text)
