import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import CallbackQuery, Chat, Message
from aiogram.types import User as TelegramUser

from bot import texts
from bot.middlewares.action_lock import ActionLockMiddleware
from bot.middlewares.ban_check import BanCheckMiddleware
from bot.middlewares.clean_chat import CleanChatMiddleware, stop_clean_chat_worker
from bot.middlewares.correlation import CorrelationMiddleware
from bot.middlewares.db_session import DBSessionMiddleware
from bot.middlewares.private_chat import PrivateChatMiddleware
from bot.middlewares.throttling import ThrottlingMiddleware


class TestBotMiddlewaresFullCoverage(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await stop_clean_chat_worker()

    async def test_action_lock_middleware(self):
        middleware = ActionLockMiddleware()
        handler = AsyncMock(return_value="OK")
        msg = Message(message_id=1, date=123, chat=Chat(id=100, type="private"))
        res = await middleware(handler, msg, {})
        self.assertEqual(res, "OK")
        cb_user = TelegramUser(id=100, is_bot=False, first_name="Test")
        cb = CallbackQuery(id="1", from_user=cb_user, chat_instance="1", data="unlocked_action")
        res2 = await middleware(handler, cb, {})
        self.assertEqual(res2, "OK")

    async def test_ban_check_middleware(self):
        middleware = BanCheckMiddleware()
        handler = AsyncMock(return_value="OK")
        banned_user = MagicMock(is_banned=True)
        data = {"db_user": banned_user}
        msg = Message(message_id=1, date=123, chat=Chat(id=100, type="private"))
        res = await middleware(handler, msg, data)
        self.assertIsNone(res)
        active_user = MagicMock(is_banned=False)
        data["db_user"] = active_user
        res2 = await middleware(handler, msg, data)
        self.assertEqual(res2, "OK")

    async def test_clean_chat_middleware(self):
        middleware = CleanChatMiddleware()
        handler = AsyncMock(return_value="OK")
        bot = AsyncMock()
        msg = Message(message_id=10, date=123, chat=Chat(id=100, type="private"))
        data = {"bot": bot}
        res = await middleware(handler, msg, data)
        self.assertEqual(res, "OK")

    async def test_clean_chat_queue_full_uses_direct_delete_fallback(self):
        middleware = CleanChatMiddleware()
        handler = AsyncMock(return_value="OK")
        bot = AsyncMock()
        msg = Message(message_id=11, date=123, chat=Chat(id=100, type="private"))
        direct_delete = AsyncMock()
        with patch("bot.middlewares.clean_chat._ensure_worker_started"), patch(
            "bot.middlewares.clean_chat._delete_queue", new=asyncio.Queue(maxsize=1)
        ), patch("bot.middlewares.clean_chat._delete_message", direct_delete):
            from bot.middlewares import clean_chat
            clean_chat._delete_queue.put_nowait((bot, 100, 10))
            res = await middleware(handler, msg, {"bot": bot})
            await asyncio.sleep(0.1)
        self.assertEqual(res, "OK")
        direct_delete.assert_awaited_once_with(msg.bot, 100, 11)

    async def test_correlation_middleware(self):
        middleware = CorrelationMiddleware()
        handler = AsyncMock(return_value="OK")
        msg = Message(message_id=1, date=123, chat=Chat(id=100, type="private"))
        res = await middleware(handler, msg, {})
        self.assertEqual(res, "OK")

    async def test_db_session_middleware(self):
        middleware = DBSessionMiddleware()
        handler = AsyncMock(return_value="OK")
        msg = Message(message_id=1, date=123, chat=Chat(id=100, type="private"))
        data = {}
        with patch("bot.middlewares.db_session.session_scope") as mock_scope:
            mock_session = AsyncMock()
            mock_scope.return_value.__aenter__.return_value = mock_session
            res = await middleware(handler, msg, data)
            self.assertEqual(res, "OK")
            self.assertIn("session", data)

    async def test_db_session_middleware_error_message(self):
        from unittest.mock import ANY
        from sqlalchemy.exc import SQLAlchemyError
        middleware = DBSessionMiddleware()
        handler = AsyncMock(side_effect=SQLAlchemyError("DB down"))
        msg = AsyncMock(spec=Message)
        msg.bot = AsyncMock()
        msg.chat = MagicMock()
        msg.chat.id = 123
        err_reply = MagicMock()
        err_reply.message_id = 456
        msg.answer = AsyncMock(return_value=err_reply)
        msg.delete = AsyncMock()
        state_mock = AsyncMock()
        data = {"state": state_mock}

        with (
            patch("bot.middlewares.db_session.session_scope"),
            patch("utils.telegram.spawn_auto_delete") as mock_spawn_delete,
        ):
            res = await middleware(handler, msg, data)
            self.assertIsNone(res)
            msg.delete.assert_called_once()
            state_mock.clear.assert_called_once()
            msg.answer.assert_called_once_with(
                texts.ERROR_TECHNICAL_MESSAGE,
                reply_markup=ANY,
                parse_mode="HTML",
            )
            mock_spawn_delete.assert_called_once_with(
                msg.bot, 123, 456, delay=7.0
            )

    async def test_db_session_middleware_error_callback(self):
        from sqlalchemy.exc import SQLAlchemyError
        middleware = DBSessionMiddleware()
        handler = AsyncMock(side_effect=SQLAlchemyError("DB down"))
        cb = AsyncMock(spec=CallbackQuery)
        data = {}
        with patch("bot.middlewares.db_session.session_scope"):
            res = await middleware(handler, cb, data)
            self.assertIsNone(res)
            cb.answer.assert_called_once_with(
                texts.ERROR_TECHNICAL_ALERT,
                show_alert=True,
            )

    async def test_private_chat_middleware(self):
        middleware = PrivateChatMiddleware()
        handler = AsyncMock(return_value="OK")
        msg = Message(message_id=1, date=123, chat=Chat(id=100, type="private"))
        res = await middleware(handler, msg, {})
        self.assertEqual(res, "OK")
        msg_group = Message(message_id=1, date=123, chat=Chat(id=100, type="group"))
        res_g = await middleware(handler, msg_group, {})
        self.assertIsNone(res_g)

    async def test_throttling_middleware(self):
        middleware = ThrottlingMiddleware()
        handler = AsyncMock(return_value="OK")
        user = TelegramUser(id=1001, is_bot=False, first_name="User")
        msg = Message(message_id=1, date=123, chat=Chat(id=1001, type="private"), from_user=user)
        res1 = await middleware(handler, msg, {})
        self.assertEqual(res1, "OK")


if __name__ == "__main__":
    unittest.main()
