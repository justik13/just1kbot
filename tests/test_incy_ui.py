import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.handlers.connection.common import _build_connections_screen
from bot.handlers.connection.incy_routes import show_incy_subscription
from database.models import User


class INCYUITests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.env_patcher = patch.dict(
            os.environ,
            {
                "BOT_TOKEN": "123:test",
                "REDIS_URL": "redis://localhost:6379/1",
                "REDIS_PASSWORD": "test",
                "ADMIN_IDS": "[123456789]",
                "SUPPORT_USERNAME": "test_support",
                "DOMAIN": "test.domain",
                "SSL_EMAIL": "test@domain.com",
                "YOOKASSA_SHOP_ID": "123456",
                "YOOKASSA_SECRET_KEY": "test_secret",
                "YOOKASSA_RETURN_URL": "https://t.me/{bot_username}",
                "YOOKASSA_WEBHOOK_PORT": "8080",
                "DB_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
                "AMNEZIA_BRIDGE_HMAC_SECRET": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/db",
            },
        )
        cls.env_patcher.start()
        from config.settings import get_settings
        get_settings.cache_clear()

    @classmethod
    def tearDownClass(cls):
        from config.settings import get_settings
        get_settings.cache_clear()
        cls.env_patcher.stop()

    @patch("bot.handlers.connection.common.get_user_profiles")
    @patch("bot.handlers.connection.common._get_effective_device_limit")
    async def test_button_presence_in_connections_screen(
        self, mock_device_limit, mock_get_profiles
    ):
        mock_get_profiles.return_value = []
        mock_device_limit.return_value = 5

        user = User(id=1, telegram_id=999, device_limit=5)
        session = AsyncMock()

        rendered, builder = await _build_connections_screen(
            user=user,
            session=session,
            read_only=False,
        )
        markup = builder.as_markup()
        buttons = [
            btn.text
            for row in markup.inline_keyboard
            for btn in row
        ]
        callbacks = [
            btn.callback_data
            for row in markup.inline_keyboard
            for btn in row
            if btn.callback_data
        ]

        self.assertTrue(any("Добавить в INCY" in btn and "Экспериментально" in btn for btn in buttons))
        self.assertIn("menu_incy_subscription", callbacks)

        # Check order: add_device -> menu_incy_subscription -> status
        add_idx = callbacks.index("add_device")
        incy_idx = callbacks.index("menu_incy_subscription")
        self.assertEqual(incy_idx, add_idx + 1)

    @patch("bot.handlers.connection.incy_routes.SubscriptionTokenService.get_or_create_token")
    @patch("bot.handlers.connection.incy_routes.render_hub")
    async def test_show_incy_subscription_handler(
        self, mock_render_hub, mock_get_token
    ):
        mock_get_token.return_value = "token_abc_123"

        bot = AsyncMock()
        message = MagicMock(spec=Message)
        message.chat = MagicMock(id=999)
        callback = MagicMock(spec=CallbackQuery)
        callback.bot = bot
        callback.message = message
        callback.answer = AsyncMock()

        state = AsyncMock(spec=FSMContext)
        session = AsyncMock()
        db_user = User(id=1, telegram_id=999, subscription_token="token_abc_123")

        await show_incy_subscription(callback, state, session, db_user=db_user)

        callback.answer.assert_awaited_once_with(show_alert=False)
        mock_get_token.assert_awaited_once_with(session, db_user)
        mock_render_hub.assert_awaited_once()

        args = mock_render_hub.call_args[0]
        text_arg = args[2]
        keyboard_arg = args[3]

        self.assertIn("https://", text_arg)
        self.assertIn("sub/token_abc_123", text_arg)
        self.assertIn("Как настроить", text_arg)

        btn_urls = [
            btn.url
            for row in keyboard_arg.inline_keyboard
            for btn in row
            if btn.url
        ]
        self.assertTrue(any("sub/open/token_abc_123" in u for u in btn_urls))

        btn_copy = [
            btn.copy_text.text
            for row in keyboard_arg.inline_keyboard
            for btn in row
            if getattr(btn, "copy_text", None)
        ]
        self.assertTrue(any("token_abc_123" in t for t in btn_copy))
        self.assertTrue(any("sub/token_abc_123" in t for t in btn_copy))

        btn_callbacks = [
            btn.callback_data
            for row in keyboard_arg.inline_keyboard
            for btn in row
            if btn.callback_data
        ]
        self.assertIn("back_to_connections", btn_callbacks)
        self.assertIn("rotate_incy_token", btn_callbacks)

        all_buttons = [btn for row in keyboard_arg.inline_keyboard for btn in row]
        self.assertEqual(len(all_buttons), 4)

    @patch("bot.handlers.connection.incy_routes.SubscriptionTokenService.rotate_token")
    @patch("bot.handlers.connection.incy_routes.render_hub")
    async def test_rotate_incy_subscription_handler(
        self, mock_render_hub, mock_rotate_token
    ):
        from bot.handlers.connection.incy_routes import rotate_incy_subscription

        mock_rotate_token.return_value = "new_rotated_token_456"

        bot = AsyncMock()
        message = MagicMock(spec=Message)
        message.chat = MagicMock(id=999)
        callback = MagicMock(spec=CallbackQuery)
        callback.bot = bot
        callback.message = message
        callback.answer = AsyncMock()

        state = AsyncMock(spec=FSMContext)
        session = AsyncMock()
        session.commit = AsyncMock()
        db_user = User(id=1, telegram_id=999, subscription_token="old_token")

        await rotate_incy_subscription(callback, state, session, db_user=db_user)

        callback.answer.assert_any_await("✅ Ссылка успешно сброшена! Старая ссылка аннулирована.", show_alert=True)
        mock_rotate_token.assert_awaited_once_with(session, db_user)
        mock_render_hub.assert_awaited_once()

        args = mock_render_hub.call_args[0]
        text_arg = args[2]
        keyboard_arg = args[3]

        self.assertIn("sub/new_rotated_token_456", text_arg)
        btn_urls = [btn.url for row in keyboard_arg.inline_keyboard for btn in row if btn.url]
        self.assertTrue(any("sub/open/new_rotated_token_456" in u for u in btn_urls))

    @patch("bot.handlers.connection.incy_routes.SubscriptionTokenService.rotate_token")
    @patch("bot.handlers.connection.incy_routes.render_hub")
    async def test_rotate_incy_subscription_handler_on_error(
        self, mock_render_hub, mock_rotate_token
    ):
        from bot.handlers.connection.incy_routes import rotate_incy_subscription

        mock_rotate_token.side_effect = RuntimeError("DB error")

        bot = AsyncMock()
        message = MagicMock(spec=Message)
        message.chat = MagicMock(id=999)
        callback = MagicMock(spec=CallbackQuery)
        callback.bot = bot
        callback.message = message
        callback.answer = AsyncMock()

        state = AsyncMock(spec=FSMContext)
        session = AsyncMock()
        db_user = User(id=1, telegram_id=999, subscription_token="old_token")

        await rotate_incy_subscription(callback, state, session, db_user=db_user)

        callback.answer.assert_any_await("Ошибка при сбросе ссылки. Попробуйте позже.", show_alert=True)
        mock_render_hub.assert_not_awaited()

    @patch("bot.handlers.connection.incy_routes.SubscriptionTokenService.rotate_token")
    @patch("bot.handlers.connection.incy_routes.render_hub")
    async def test_rotate_incy_subscription_handler_on_commit_failure(
        self, mock_render_hub, mock_rotate_token
    ):
        from bot.handlers.connection.incy_routes import rotate_incy_subscription

        mock_rotate_token.return_value = "uncommitted_token_999"

        bot = AsyncMock()
        message = MagicMock(spec=Message)
        message.chat = MagicMock(id=999)
        callback = MagicMock(spec=CallbackQuery)
        callback.bot = bot
        callback.message = message
        callback.answer = AsyncMock()

        state = AsyncMock(spec=FSMContext)
        session = AsyncMock()
        session.commit.side_effect = RuntimeError("DB connection dropped on commit")
        db_user = User(id=1, telegram_id=999, subscription_token="old_token")

        await rotate_incy_subscription(callback, state, session, db_user=db_user)

        # If commit fails, render_hub must NOT be called with the uncommitted token
        callback.answer.assert_any_await("Ошибка при сбросе ссылки. Попробуйте позже.", show_alert=True)
        mock_render_hub.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
