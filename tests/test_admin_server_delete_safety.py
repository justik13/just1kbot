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
            texts.ERROR_INVALID_REQUEST,
            show_alert=True,
        )
        state.clear.assert_awaited_once()

    async def test_deletion_blocked_when_active_white_internet_subscriptions_exist(self):
        callback = MagicMock()
        callback.from_user.id = 1
        callback.data = "confirm_server_delete:10"
        callback.answer = AsyncMock()
        callback.message.edit_text = AsyncMock()
        state = AsyncMock(spec=FSMContext)
        state.get_state.return_value = AdminStates.confirming_server_delete
        state.get_data.return_value = {"delete_server_id": 10}

        mock_server = MagicMock()
        mock_server.id = 10
        mock_server.name = "Test Origin"
        mock_server.api_url = "http://127.0.0.1:8444"
        mock_server.api_key = "secret"

        mock_session = AsyncMock()
        # 1st execute: select server with_for_update
        # 2nd execute: select profiles
        # 3rd execute: select operations
        # 4th execute: select active WhiteInternetSubscription
        res_server = MagicMock()
        res_server.scalar_one_or_none.return_value = mock_server

        res_profiles = MagicMock()
        res_profiles.scalars.return_value.all.return_value = []

        res_ops = MagicMock()
        res_ops.scalars.return_value.all.return_value = []

        mock_active_sub = MagicMock()
        mock_active_sub.id = 1
        res_wl = MagicMock()
        res_wl.scalars.return_value.all.return_value = [mock_active_sub]

        mock_session.execute.side_effect = [res_server, res_profiles, res_ops, res_wl]

        with patch(
            "bot.handlers.admin.servers.delete_routes.is_admin",
            return_value=True,
        ), patch(
            "bot.handlers.admin.servers.delete_routes.parse_callback_id",
            return_value=10,
        ):
            await confirm_delete_server(callback, state, mock_session)

        mock_session.rollback.assert_awaited_once()
        self.assertTrue(callback.answer.call_args[1]["show_alert"])
        self.assertIn("активных подписок White Internet", callback.answer.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
