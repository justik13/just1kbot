import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import CallbackQuery, Chat, Message
from aiogram.types import User as TelegramUser

from bot.keyboards import common as common_kb
from bot.keyboards import device as device_kb
from bot.keyboards import payment as payment_kb
from bot.keyboards import user as user_kb
from bot.keyboards.admin import (
    dashboard as admin_dashboard_kb,
)
from bot.keyboards.admin import (
    users as admin_users_kb,
)
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


class TestBotKeyboardsFullCoverage(unittest.TestCase):
    def test_user_keyboards(self):
        kb_history = user_kb.get_history_keyboard()
        self.assertIsNotNone(kb_history)
        self.assertEqual(
            [button.callback_data for row in kb_history.inline_keyboard for button in row],
            ["menu_balance", "back_to_main_menu"],
        )

        kb_ref = user_kb.get_referral_keyboard(referral_link="https://t.me/bot?start=123")
        self.assertIsNotNone(kb_ref)

    def test_device_keyboards(self):
        kb_dev = device_kb.get_device_keyboard(profile_id=1, config_ready=True)
        self.assertIsNotNone(kb_dev)

    def test_payment_keyboards(self):
        kb_bal = payment_kb.get_balance_keyboard()
        self.assertIsNotNone(kb_bal)

    def test_common_keyboards(self):
        btn_back = common_kb.get_back_button("main_menu")
        self.assertIsNotNone(btn_back)

        hub_user = common_kb.get_hub_keyboard(is_admin=False)
        user_callbacks = [
            button.callback_data
            for row in hub_user.inline_keyboard
            for button in row
            if button.callback_data
        ]
        self.assertNotIn("white_internet", user_callbacks)
        self.assertEqual(len(user_callbacks), len(set(user_callbacks)))

        hub_admin = common_kb.get_hub_keyboard(is_admin=True)
        admin_callbacks = [
            button.callback_data
            for row in hub_admin.inline_keyboard
            for button in row
            if button.callback_data
        ]
        self.assertIn("white_internet", admin_callbacks)
        self.assertEqual(len(admin_callbacks), len(set(admin_callbacks)))

    def test_admin_keyboards(self):
        kb_admin_menu = admin_dashboard_kb.get_admin_menu()
        self.assertIsNotNone(kb_admin_menu)
        kb_admin_card = admin_users_kb.get_admin_user_card_keyboard(user_id=12345, is_banned=False)
        self.assertIsNotNone(kb_admin_card)


if __name__ == "__main__":
    unittest.main()
