import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from bot import texts

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
            profiles=[],
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

        self.assertTrue(
            any("Добавить в INCY (iOS / Android)" in btn and "🧪" in btn for btn in buttons)
        )
        self.assertIn("menu_incy_subscription", callbacks)

        # Check order: add_device -> menu_incy_subscription -> status
        add_idx = callbacks.index("add_device")
        incy_idx = callbacks.index("menu_incy_subscription")
        self.assertEqual(incy_idx, add_idx + 1)

    @patch("bot.handlers.connection.common.get_user_profiles")
    @patch("bot.handlers.connection.common._get_effective_device_limit")
    async def test_button_hidden_when_read_only(
        self, mock_device_limit, mock_get_profiles
    ):
        mock_get_profiles.return_value = []
        mock_device_limit.return_value = 5

        user = User(id=1, telegram_id=999, device_limit=5)
        session = AsyncMock()

        rendered, builder = await _build_connections_screen(
            user=user,
            session=session,
            profiles=[],
            read_only=True,
        )
        markup = builder.as_markup()
        callbacks = [
            btn.callback_data
            for row in markup.inline_keyboard
            for btn in row
            if btn.callback_data
        ]
        self.assertNotIn("menu_incy_subscription", callbacks)

    @patch("bot.handlers.connection.common.get_user_profiles")
    @patch("bot.handlers.connection.common._get_effective_device_limit")
    @patch("bot.handlers.connection.common.SubscriptionTokenService.is_enabled")
    async def test_button_hidden_when_service_disabled(
        self, mock_is_enabled, mock_device_limit, mock_get_profiles
    ):
        mock_is_enabled.return_value = False
        mock_get_profiles.return_value = []
        mock_device_limit.return_value = 5

        user = User(id=1, telegram_id=999, device_limit=5)
        session = AsyncMock()

        rendered, builder = await _build_connections_screen(
            user=user,
            session=session,
            profiles=[],
            read_only=False,
        )
        markup = builder.as_markup()
        callbacks = [
            btn.callback_data
            for row in markup.inline_keyboard
            for btn in row
            if btn.callback_data
        ]
        self.assertNotIn("menu_incy_subscription", callbacks)

    @patch("integrations.incy.bot_routes.SubscriptionService.check_access")
    @patch("integrations.incy.bot_routes.SubscriptionTokenService.get_or_create_token")
    @patch("integrations.incy.bot_routes.render_hub")
    async def test_show_incy_subscription_handler(
        self, mock_render_hub, mock_get_token, mock_check_access
    ):
        mock_check_access.return_value = True
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

    @patch("integrations.incy.bot_routes.SubscriptionTokenService.is_enabled")
    async def test_show_incy_subscription_when_disabled(
        self, mock_is_enabled
    ):
        mock_is_enabled.return_value = False

        callback = MagicMock(spec=CallbackQuery)
        callback.answer = AsyncMock()
        state = AsyncMock(spec=FSMContext)
        session = AsyncMock()
        db_user = User(id=1, telegram_id=999)

        await show_incy_subscription(callback, state, session, db_user=db_user)
        callback.answer.assert_awaited_once_with(
            "⚠️ Подписка INCY временно недоступна.", show_alert=True
        )

    @patch("integrations.incy.bot_routes.SubscriptionService.check_access")
    async def test_show_incy_subscription_when_no_access(
        self, mock_check_access
    ):
        mock_check_access.return_value = False

        callback = MagicMock(spec=CallbackQuery)
        callback.answer = AsyncMock()
        state = AsyncMock(spec=FSMContext)
        session = AsyncMock()
        db_user = User(id=1, telegram_id=999)

        await show_incy_subscription(callback, state, session, db_user=db_user)
        callback.answer.assert_awaited_once_with(
            "⚠️ Доступ неактивен. Продлите подписку.", show_alert=True
        )

    @patch("integrations.incy.bot_routes.SubscriptionService.check_access")
    @patch("integrations.incy.bot_routes.SubscriptionTokenService.rotate_token")
    @patch("integrations.incy.bot_routes.render_hub")
    async def test_rotate_incy_subscription_handler(
        self, mock_render_hub, mock_rotate_token, mock_check_access
    ):
        from integrations.incy.bot_routes import rotate_incy_subscription

        mock_check_access.return_value = True
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

        callback.answer.assert_any_await(texts.ALERT_INCY_ROTATE_SUCCESS, show_alert=True)
        mock_rotate_token.assert_awaited_once_with(session, db_user)
        mock_render_hub.assert_awaited_once()

        args = mock_render_hub.call_args[0]
        text_arg = args[2]
        keyboard_arg = args[3]

        self.assertIn("sub/new_rotated_token_456", text_arg)
        self.assertIn("iOS / Android", text_arg)
        self.assertIn("Windows 10/11 (x64) и macOS 14+", text_arg)
        self.assertIn("AmneziaVPN", text_arg)
        self.assertIn("AmneziaWG", text_arg)
        btn_urls = [btn.url for row in keyboard_arg.inline_keyboard for btn in row if btn.url]
        self.assertTrue(any("sub/open/new_rotated_token_456" in u for u in btn_urls))

    @patch("integrations.incy.bot_routes.SubscriptionService.check_access")
    @patch("integrations.incy.bot_routes.SubscriptionTokenService.rotate_token")
    @patch("integrations.incy.bot_routes.render_hub")
    async def test_rotate_incy_subscription_handler_on_error(
        self, mock_render_hub, mock_rotate_token, mock_check_access
    ):
        from integrations.incy.bot_routes import rotate_incy_subscription

        mock_check_access.return_value = True
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

        callback.answer.assert_any_await(texts.ALERT_INCY_ROTATE_ERROR, show_alert=True)
        mock_render_hub.assert_not_awaited()

    @patch("integrations.incy.bot_routes.SubscriptionService.check_access")
    @patch("integrations.incy.bot_routes.SubscriptionTokenService.rotate_token")
    @patch("integrations.incy.bot_routes.render_hub")
    async def test_rotate_incy_subscription_handler_on_commit_failure(
        self, mock_render_hub, mock_rotate_token, mock_check_access
    ):
        from integrations.incy.bot_routes import rotate_incy_subscription

        mock_check_access.return_value = True
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
        callback.answer.assert_any_await(texts.ALERT_INCY_ROTATE_ERROR, show_alert=True)
        mock_render_hub.assert_not_awaited()


class IncyWebTemplatesTests(unittest.TestCase):
    def test_render_open_html_contains_valid_store_and_platform_links(self):
        from bot.handlers.incy_web_templates import render_open_html

        html_out = render_open_html(
            sub_url="https://vpn.example.com/sub/token_123",
            deep_link="incy://import/https%3A%2F%2Fvpn.example.com%2Fsub%2Ftoken_123",
        )

        self.assertIn("https://apps.apple.com/app/incy/id6756943388", html_out)
        self.assertIn("https://play.google.com/store/apps/details?id=llc.itdev.incy", html_out)
        self.assertIn("https://github.com/INCY-DEV/incy-platforms", html_out)
        self.assertIn("https://incy.cc/", html_out)
        self.assertIn("AmneziaVPN", html_out)
        self.assertIn("AmneziaWG", html_out)
        self.assertIn("Windows 10/11 (x64) и macOS 14+", html_out)

    def test_render_inactive_html_contains_support_link(self):
        from bot.handlers.incy_web_templates import render_inactive_html

        html_out = render_inactive_html(
            sub_url="https://vpn.example.com/sub/token_123",
            support_username="test_support_admin",
        )

        self.assertIn("https://t.me/test_support_admin", html_out)
        self.assertIn("Подписка не активна", html_out)


if __name__ == "__main__":
    unittest.main()
