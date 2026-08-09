import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class TestAdminUsersImportsAndServerUsage(unittest.IsolatedAsyncioTestCase):
    async def test_admin_users_router_imports_cleanly(self):
        # The production failure was an ImportError reached only when the
        # callback was invoked, so a normal test-suite import was not enough.
        from bot.handlers.admin.users import list_routes

        self.assertIsNotNone(list_routes.router)
        self.assertTrue(callable(list_routes.users_filter_pagination))

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
