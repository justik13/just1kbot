import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers.admin.users.message_routes import process_send_user_message


class TestAdminDirectMessages(unittest.IsolatedAsyncioTestCase):
    async def test_admin_direct_message_includes_dismiss_button(self):
        message = AsyncMock()
        message.from_user.id = 100
        message.text = "Тестовое сообщение от админа"
        message.photo = None
        message.document = None
        message.chat.id = 100
        message.message_id = 50

        state = AsyncMock()
        state.get_data.return_value = {"target_telegram_id": 777, "target_user_db_id": 10}

        user = MagicMock()
        user.id = 10
        user.telegram_id = 777

        session = AsyncMock()

        with (
            patch("bot.handlers.admin.users.message_routes.is_admin", return_value=True),
            patch("bot.handlers.admin.users.message_routes.get_user_by_telegram_id", return_value=user),
            patch("bot.handlers.admin.users.message_routes._show_user_card_edit"),
            patch("services.audit_service.AuditService.log_action"),
        ):
            await process_send_user_message(message, state, session)

            message.bot.send_message.assert_called_once()
            call_kwargs = message.bot.send_message.call_args[1]
            self.assertEqual(777, message.bot.send_message.call_args[0][0])
            self.assertIn("📨 <b>Сообщение от администрации:</b>\n\nТестовое сообщение от админа", message.bot.send_message.call_args[0][1])
            self.assertIsNotNone(call_kwargs.get("reply_markup"))
            inline_keyboard = call_kwargs["reply_markup"].inline_keyboard
            self.assertEqual("dismiss_notification", inline_keyboard[0][0].callback_data)
            self.assertEqual("✅ Прочитано", inline_keyboard[0][0].text)


if __name__ == "__main__":
    unittest.main()
