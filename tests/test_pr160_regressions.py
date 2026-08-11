import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot import texts
from bot.handlers import fallback, start
from bot.keyboards.common import get_hub_keyboard
from bot.keyboards.device import get_device_keyboard


class TestPr160Regressions(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_profile_callbacks_redirect_to_hub(self):
        callback = MagicMock()
        state = MagicMock()
        session = MagicMock()
        db_user = MagicMock()

        with patch("bot.handlers.start.back_to_main_menu", new=AsyncMock()) as hub:
            await fallback.legacy_profile_callback(
                callback,
                state,
                session,
                db_user,
            )

        hub.assert_awaited_once_with(
            callback,
            state,
            db_user,
            session,
        )

    async def test_back_to_main_menu_rejects_missing_session_before_db_render(self):
        callback = MagicMock()
        callback.answer = AsyncMock()
        callback.message = MagicMock()
        state = MagicMock()
        state.clear = AsyncMock()
        db_user = MagicMock()

        await start.back_to_main_menu(
            callback,
            state,
            db_user=db_user,
            session=None,
        )

        state.clear.assert_awaited_once()
        callback.answer.assert_awaited_once_with(
            texts.ERROR_USER_NOT_FOUND,
            show_alert=True,
        )

    async def test_white_internet_callback_is_alert(self):
        callback = MagicMock()
        callback.answer = AsyncMock()

        await fallback.white_internet_callback(callback)

        callback.answer.assert_awaited_once_with(
            "🔨 Раздел находится в разработке",
            show_alert=True,
        )

    def test_hub_keyboard_all_variants_have_white_internet_last(self):
        for is_active in (False, True):
            for is_admin in (False, True):
                for mtproto_url in (None, "https://example.test/mtproto"):
                    keyboard = get_hub_keyboard(
                        is_admin=is_admin,
                        is_active=is_active,
                        mtproto_url=mtproto_url,
                    )
                    buttons = [
                        button
                        for row in keyboard.inline_keyboard
                        for button in row
                    ]
                    callbacks = [
                        button.callback_data
                        for button in buttons
                        if button.callback_data is not None
                    ]

                    self.assertEqual(callbacks[-1], "white_internet")
                    self.assertEqual(len(callbacks), len(set(callbacks)))
                    if mtproto_url:
                        self.assertEqual(buttons[-2].url, mtproto_url)

    def test_device_download_file_action_is_preserved(self):
        ready = get_device_keyboard(profile_id=123, config_ready=True)
        ready_callbacks = [
            button.callback_data
            for row in ready.inline_keyboard
            for button in row
        ]
        self.assertIn("show_config:123", ready_callbacks)
        self.assertIn("download_conf:123", ready_callbacks)

        pending = get_device_keyboard(profile_id=123, config_ready=False)
        pending_callbacks = [
            button.callback_data
            for row in pending.inline_keyboard
            for button in row
        ]
        self.assertNotIn("show_config:123", pending_callbacks)
        self.assertNotIn("download_conf:123", pending_callbacks)

    def test_legal_urls_and_faq_match_current_navigation(self):
        self.assertEqual(
            texts.TOS_AGREEMENT_URL,
            "https://telegra.ph/Polzovatelskoe-soglashenie-07-23-48",
        )
        self.assertEqual(
            texts.PRIVACY_POLICY_URL,
            "https://telegra.ph/Politika-konfidencialnosti-07-23-84",
        )
        self.assertNotIn("👤 Профиль", texts.FAQ_TEXT)
        self.assertIn("🤝 Пригласить друга", texts.FAQ_TEXT)

    async def test_hub_renders_referrer_without_breaking_missing_referrer(self):
        user = SimpleNamespace(
            id=10,
            telegram_id=100,
            first_name="User",
            referred_by=200,
            subscription_end=None,
            device_limit=2,
        )
        referrer = SimpleNamespace(
            telegram_id=200,
            first_name="Referrer",
            username="ref",
        )
        balance = SimpleNamespace(real_available=123, bonus_available=7)

        with (
            patch.object(start.SubscriptionService, "check_access", new=AsyncMock(return_value=True)),
            patch.object(start, "get_account_balance", new=AsyncMock(return_value=balance)),
            patch(
                "database.repositories.profiles_repo.get_user_profiles",
                new=AsyncMock(return_value=[object()]),
            ),
            patch.object(start, "get_user_by_telegram_id", new=AsyncMock(return_value=referrer)),
            patch(
                "database.repositories.system_settings_repo.get_system_setting",
                new=AsyncMock(return_value=None),
            ),
            patch.object(start, "get_hub_keyboard", return_value=MagicMock()),
        ):
            text, _ = await start._build_hub_text_and_kb(MagicMock(), user)

        self.assertIn("Telegram ID:", text)
        self.assertIn("🤝 Вас пригласил: Referrer (@ref) (ID: <code>200</code>)", text)
        self.assertIn("1/2", text)
        self.assertIn("123 ₽", text)
        self.assertIn("7 ₽", text)


if __name__ == "__main__":
    unittest.main()
