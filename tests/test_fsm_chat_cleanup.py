import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, User

from bot.handlers.admin.broadcast import process_broadcast_message
from bot.handlers.admin.users.balance_routes import process_balance_reason
from bot.handlers.admin.users.list_routes import process_search_user
from bot.states import AdminStates


class TestFSMChatCleanup(unittest.IsolatedAsyncioTestCase):
    """Verify guaranteed chat cleanup and trigger_message_id across FSM input routes."""

    async def asyncSetUp(self):
        self.storage = MemoryStorage()
        self.admin_user = User(id=999, is_bot=False, first_name="Admin")
        self.chat = Chat(id=999, type="private")
        self.key = StorageKey(bot_id=1, chat_id=999, user_id=999)
        self.state = FSMContext(storage=self.storage, key=self.key)
        self.mock_session = AsyncMock()

    def _create_msg(self, text: str | None = None, message_id: int = 123) -> MagicMock:
        msg = MagicMock(spec=Message)
        msg.from_user = self.admin_user
        msg.chat = self.chat
        msg.bot = AsyncMock()
        msg.message_id = message_id
        msg.text = text
        msg.caption = None
        msg.photo = None
        msg.video = None
        msg.animation = None
        msg.document = None
        msg.voice = None
        msg.video_note = None
        msg.audio = None
        msg.sticker = None
        msg.content_type = "text"
        msg.delete = AsyncMock()
        msg.answer = AsyncMock()
        return msg

    async def test_broadcast_message_normal_text_cleanup(self):
        await self.state.set_state(AdminStates.entering_broadcast_message)
        await self.state.update_data(target_audience="all")

        msg = self._create_msg(text="Привет всем пользователям!", message_id=456)

        with patch("bot.handlers.admin.broadcast.is_admin", return_value=True), \
             patch("bot.handlers.admin.broadcast.render_hub", new_callable=AsyncMock) as mock_render, \
             patch("bot.handlers.admin.broadcast._dispatch_message", new_callable=AsyncMock):

            await process_broadcast_message(msg, self.state, self.mock_session)

            msg.delete.assert_awaited_once()
            mock_render.assert_awaited_once()
            call_kwargs = mock_render.call_args[1]
            self.assertEqual(call_kwargs.get("trigger_message_id"), 456)

    async def test_broadcast_message_empty_text_cleanup(self):
        await self.state.set_state(AdminStates.entering_broadcast_message)
        msg = self._create_msg(text=None, message_id=789)

        with patch("bot.handlers.admin.broadcast.is_admin", return_value=True), \
             patch("bot.handlers.admin.broadcast.render_hub", new_callable=AsyncMock) as mock_render:

            await process_broadcast_message(msg, self.state, self.mock_session)

            msg.delete.assert_awaited_once()
            mock_render.assert_awaited_once()
            call_kwargs = mock_render.call_args[1]
            self.assertEqual(call_kwargs.get("trigger_message_id"), 789)

    async def test_search_user_non_text_cleanup(self):
        await self.state.set_state(AdminStates.searching_user)
        msg = self._create_msg(text=None, message_id=555)

        with patch("bot.handlers.admin.users.list_routes.is_admin", return_value=True), \
             patch("bot.handlers.admin.users.list_routes.render_hub", new_callable=AsyncMock) as mock_render:

            await process_search_user(msg, self.state, self.mock_session)

            msg.delete.assert_awaited_once()
            mock_render.assert_awaited_once()
            call_kwargs = mock_render.call_args[1]
            self.assertEqual(call_kwargs.get("trigger_message_id"), 555)

    async def test_search_user_not_found_cleanup(self):
        await self.state.set_state(AdminStates.searching_user)
        msg = self._create_msg(text="nonexistent_user", message_id=777)

        with patch("bot.handlers.admin.users.list_routes.is_admin", return_value=True), \
             patch("bot.handlers.admin.users.list_routes.render_hub", new_callable=AsyncMock) as mock_render, \
             patch("database.repositories.users_repo.search_user_flexible", new_callable=AsyncMock, return_value=None):

            await process_search_user(msg, self.state, self.mock_session)

            msg.delete.assert_awaited_once()
            mock_render.assert_awaited_once()
            call_kwargs = mock_render.call_args[1]
            self.assertEqual(call_kwargs.get("trigger_message_id"), 777)

    async def test_balance_reason_user_not_found_cleanup(self):
        await self.state.set_state(AdminStates.entering_user_balance_reason)
        await self.state.update_data(target_telegram_id=12345, amount=100, action_type="topup")
        msg = self._create_msg(text="Бонус за участие в бета-тесте", message_id=888)

        with patch("bot.handlers.admin.users.balance_routes.is_admin", return_value=True), \
             patch("bot.handlers.admin.users.balance_routes.render_hub", new_callable=AsyncMock) as mock_render, \
             patch("bot.handlers.admin.users.balance_routes.get_user_by_telegram_id", new_callable=AsyncMock, return_value=None):

            await process_balance_reason(msg, self.state, self.mock_session)

            msg.delete.assert_awaited_once()
            mock_render.assert_awaited_once()
            call_kwargs = mock_render.call_args[1]
            self.assertEqual(call_kwargs.get("trigger_message_id"), 888)
