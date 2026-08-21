import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.fsm.context import FSMContext

from bot import texts
from bot.handlers.admin.servers.delete_routes import confirm_delete_server
from bot.states import AdminStates


class AdminServerDeleteSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmation_callback_must_match_fsm_target(self):
        callback = MagicMock()
        callback.from_user.id = 1
        callback.data = "confirm_server_delete:20"
        callback.answer = AsyncMock()
        state = AsyncMock(spec=FSMContext)
        state.get_state.return_value = AdminStates.confirming_server_delete
        state.get_data.return_value = {"delete_server_id": 10}

        with patch(
            "bot.handlers.admin.servers.delete_routes.is_admin",
            return_value=True,
        ), patch(
            "bot.handlers.admin.servers.delete_routes.parse_callback_id",
            return_value=20,
        ):
            await confirm_delete_server(callback, state, AsyncMock())

        callback.answer.assert_awaited_once_with(
            texts.UI_BOT_HANDLERS_ADMIN_SERVERS_DELETE_ROUTES_L134_1,
            show_alert=True,
        )
        state.clear.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
