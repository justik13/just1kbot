import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import Message, User

from bot.handlers.admin.users.message_routes import process_send_user_message
from database.models import User as DBUser


class AdminDirectMessageCleanChatTests(unittest.IsolatedAsyncioTestCase):
    """
    Test that process_send_user_message does NOT call message.answer(notice),
    and instead calls _show_user_card_edit with notice=notice,
    which deletes the admin input message via trigger_message_id.
    """

    async def test_admin_direct_message_uses_clean_chat_and_notice_in_hub(self):
        message = AsyncMock(spec=Message)
        message.from_user = User(id=123456789, is_bot=False, first_name="Admin")
        message.chat = MagicMock(id=123456789)
        message.message_id = 999
        message.text = "Test admin direct message content"
        message.photo = None
        message.document = None
        message.bot = AsyncMock()

        state = AsyncMock()
        state.get_data = AsyncMock(return_value={
            "target_telegram_id": 902161217,
            "target_user_db_id": 4,
        })
        state.clear = AsyncMock()

        target_user = DBUser(id=4, telegram_id=902161217, username="test_target")

        session = AsyncMock()

        with patch("bot.handlers.admin.users.message_routes.is_admin", return_value=True), \
             patch("bot.handlers.admin.users.message_routes.get_user_by_telegram_id", return_value=target_user), \
             patch("bot.handlers.admin.users.message_routes.AuditService.log_action", new_callable=AsyncMock), \
             patch("bot.handlers.admin.users.message_routes._show_user_card_edit", new_callable=AsyncMock) as mock_show_card:

            await process_send_user_message(message, state, session)

            # 1. message.answer should NOT have been called for the success notice
            message.answer.assert_not_called()

            # 2. Target user received the message via bot.send_message
            message.bot.send_message.assert_called_once_with(
                902161217,
                "📨 <b>Сообщение от администрации:</b>\n\nTest admin direct message content",
                reply_markup=unittest.mock.ANY,
                parse_mode="HTML",
            )

            # 3. _show_user_card_edit was called with notice containing the success text
            mock_show_card.assert_called_once()
            _, kwargs = mock_show_card.call_args
            notice = kwargs.get("notice") or (mock_show_card.call_args[0][3] if len(mock_show_card.call_args[0]) > 3 else None)
            self.assertIsNotNone(notice)
            self.assertIn("902161217", notice)
            self.assertIn("успешно отправлено", notice)
