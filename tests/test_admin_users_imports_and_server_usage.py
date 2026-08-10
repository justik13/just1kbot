import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select


class TestAdminUsersImportsAndServerUsage(unittest.IsolatedAsyncioTestCase):
    async def test_admin_users_router_imports_cleanly(self):
        # The production failure was an ImportError reached only when the
        # callback was invoked, so a normal test-suite import was not enough.
        from bot.handlers.admin.users import list_routes

        self.assertIsNotNone(list_routes.router)
        self.assertTrue(callable(list_routes.users_filter_pagination))

    async def test_server_user_filter_builds_without_runtime_import_error(self):
        from database.models import User
        from database.repositories.users_repo import _apply_user_filters

        stmt = _apply_user_filters(select(User), "server", "7")
        sql = str(stmt)

        self.assertIn("vpn_profiles", sql)
        self.assertIn("server_id", sql)
        self.assertIn("PROVISIONING_STATUS", sql.upper())

    async def test_extended_filter_menu_server_buttons_include_country_flag(self):
        from bot.handlers.admin.users.list_routes import show_extended_filter_menu

        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=1),
            data="admin_users_filter_menu:server",
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock()),
        )
        server = SimpleNamespace(id=7, name="DE-1", country_flag="🇩🇪")

        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [server]
        session = AsyncMock()
        session.scalars = AsyncMock(return_value=scalars_mock)

        with patch("bot.handlers.admin.users.list_routes.is_admin", return_value=True):
            await show_extended_filter_menu(callback, session)

        call_args = callback.message.edit_text.call_args
        reply_markup = call_args.kwargs["reply_markup"]
        buttons = [btn.text for row in reply_markup.inline_keyboard for btn in row]

        self.assertIn("🖥 🇩🇪 DE-1", buttons)

    async def test_users_keyboard_exposes_server_filter_but_not_country_filter(self):
        from bot.handlers.admin.users.common import _build_users_list_text_and_kb

        user = SimpleNamespace(
            telegram_id=700,
            username="tester",
            subscription_end=None,
            is_banned=False,
            is_bot_blocked=False,
            profiles=[],
        )

        with patch(
            "bot.handlers.admin.users.common.format_days_left",
            return_value="—",
        ):
            _, builder = await _build_users_list_text_and_kb(
                [user],
                page=1,
                total_pages=1,
                total=1,
            )

        buttons = [
            button
            for row in builder.as_markup().inline_keyboard
            for button in row
        ]
        labels = {button.text for button in buttons}

        self.assertIn("🖥 По VPN серверам", labels)
        self.assertNotIn("🌐 По странам", labels)

    async def test_server_card_uses_database_peer_count_not_missing_model_field(self):
        from bot.handlers.admin.servers import common

        callback = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock()),
        )
        server = SimpleNamespace(
            id=7,
            name="DE-1",
            country_flag="🇩🇪",
            is_active=True,
            protocol="amneziawg2",
            max_clients=240,
            api_url="https://example.invalid/api",
        )
        session = AsyncMock()

        with patch.object(
            common,
            "get_server_peer_counts",
            new=AsyncMock(return_value={7: 37}),
        ):
            await common._show_server_card(callback, session, server)

        rendered = callback.message.edit_text.call_args.args[0]
        self.assertIn("37 / 240", rendered)
        self.assertNotIn("0 / 240", rendered)


if __name__ == "__main__":
    unittest.main()
