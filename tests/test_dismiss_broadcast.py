import unittest
from unittest.mock import AsyncMock, MagicMock

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from bot.handlers.admin.broadcast import router as admin_broadcast_router
from bot.handlers.fallback import dismiss_broadcast_message, router as fallback_router


class TestDismissBroadcastUnit(unittest.IsolatedAsyncioTestCase):
    async def test_dismiss_broadcast_message_deletes_message_and_answers_callback(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.answer = AsyncMock()
        cb.message = MagicMock(spec=Message)
        cb.message.delete = AsyncMock()

        await dismiss_broadcast_message(cb)

        cb.answer.assert_awaited_once_with(show_alert=False)
        cb.message.delete.assert_awaited_once()

    async def test_dismiss_broadcast_message_handles_telegram_bad_request(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.answer = AsyncMock()
        cb.message = MagicMock(spec=Message)
        cb.message.delete = AsyncMock(
            side_effect=TelegramBadRequest(method="deleteMessage", message="message to delete not found")
        )

        # Should not raise exception
        await dismiss_broadcast_message(cb)

        cb.answer.assert_awaited_once_with(show_alert=False)
        cb.message.delete.assert_awaited_once()

    async def test_dismiss_broadcast_message_handles_telegram_api_error(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.answer = AsyncMock()
        cb.message = MagicMock(spec=Message)
        cb.message.delete = AsyncMock(
            side_effect=TelegramAPIError(method="deleteMessage", message="network failure")
        )

        await dismiss_broadcast_message(cb)

        cb.answer.assert_awaited_once_with(show_alert=False)
        cb.message.delete.assert_awaited_once()

    async def test_dismiss_broadcast_message_handles_none_message(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.answer = AsyncMock()
        cb.message = None

        await dismiss_broadcast_message(cb)

        cb.answer.assert_awaited_once_with(show_alert=False)


class TestDismissBroadcastRouting(unittest.TestCase):
    def test_dismiss_broadcast_handler_registered_in_fallback_router(self):
        """Verifies dismiss_broadcast is registered on fallback_router (public, accessible to all users)."""
        registered = False
        for handler in fallback_router.callback_query.handlers:
            if handler.callback == dismiss_broadcast_message:
                registered = True
                break
        self.assertTrue(
            registered,
            "dismiss_broadcast_message must be registered on public fallback_router so ordinary users can dismiss messages",
        )

    def test_dismiss_broadcast_not_in_admin_broadcast_router(self):
        """Regression Shield: verifies dismiss_broadcast is NOT in admin subrouter (which would block non-admin users via AdminFilter)."""
        for handler in admin_broadcast_router.callback_query.handlers:
            self.assertNotEqual(
                handler.callback,
                dismiss_broadcast_message,
                "dismiss_broadcast_message must NOT be registered under admin_router",
            )


if __name__ == "__main__":
    unittest.main()
