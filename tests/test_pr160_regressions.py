import re
import unittest
import uuid
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

    def test_hub_keyboard_admin_and_non_admin_access(self):
        for is_active in (False, True):
            for mtproto_url in (None, "https://example.test/mtproto"):
                admin_keyboard = get_hub_keyboard(
                    is_admin=True,
                    is_active=is_active,
                    mtproto_url=mtproto_url,
                )
                admin_buttons = [
                    button
                    for row in admin_keyboard.inline_keyboard
                    for button in row
                ]
                admin_callbacks = [
                    button.callback_data
                    for button in admin_buttons
                    if button.callback_data is not None
                ]
                self.assertIn("menu_admin", admin_callbacks)
                self.assertEqual(
                    admin_callbacks[-2:],
                    ["white_internet", "menu_admin"],
                )
                self.assertEqual(
                    len(admin_callbacks),
                    len(set(admin_callbacks)),
                )

                user_keyboard = get_hub_keyboard(
                    is_admin=False,
                    is_active=is_active,
                    mtproto_url=mtproto_url,
                )
                user_buttons = [
                    button
                    for row in user_keyboard.inline_keyboard
                    for button in row
                ]
                user_callbacks = [
                    button.callback_data
                    for button in user_buttons
                    if button.callback_data is not None
                ]
                self.assertNotIn("menu_admin", user_callbacks)
                self.assertEqual(user_callbacks[-1], "white_internet")
                self.assertEqual(
                    len(user_callbacks),
                    len(set(user_callbacks)),
                )

                url_buttons = [button for button in user_buttons if button.url]
                if mtproto_url:
                    self.assertEqual(len(url_buttons), 1)
                    self.assertEqual(url_buttons[0].url, mtproto_url)
                else:
                    self.assertEqual(url_buttons, [])

    async def test_admin_menu_callback_rejects_non_admin(self):
        from bot.handlers.admin import dashboard

        callback = MagicMock()
        callback.from_user.id = 123456
        callback.answer = AsyncMock()
        state = MagicMock()
        state.clear = AsyncMock()
        session = MagicMock()

        with patch.object(dashboard, "is_admin", return_value=False), patch.object(
            dashboard, "_show_admin_dashboard", new=AsyncMock()
        ) as show_dashboard:
            await dashboard.show_admin_menu(callback, state, session)

        callback.answer.assert_awaited_once_with(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        state.clear.assert_not_awaited()
        show_dashboard.assert_not_awaited()

    async def test_balance_purchase_back_cancels_commits_and_then_navigates(self):
        from bot.handlers.payment import purchase_routes, showcase_routes

        quote_id = uuid.uuid4()
        user = SimpleNamespace(id=42)
        quote = SimpleNamespace(operation_type="purchase")
        callback = MagicMock()
        callback.data = f"balance_purchase_cancel:{quote_id}"
        callback.answer = AsyncMock()
        session = MagicMock()
        session.commit = AsyncMock()

        with (
            patch.object(
                purchase_routes,
                "cancel_account_purchase_quote",
                new=AsyncMock(return_value=quote),
            ) as cancel_quote,
            patch.object(
                showcase_routes,
                "show_tariff_showcase_callback",
                new=AsyncMock(),
            ) as show_showcase,
        ):
            await purchase_routes.cancel_purchase(callback, session, user)

        cancel_quote.assert_awaited_once_with(
            session,
            user_id=42,
            quote_public_id=quote_id,
        )
        session.commit.assert_awaited_once()
        callback.answer.assert_awaited_once_with(show_alert=False)
        show_showcase.assert_awaited_once_with(callback, session)

    async def test_balance_purchase_back_does_not_navigate_when_cancel_fails(self):
        from bot.handlers.payment import purchase_routes, showcase_routes
        from services.account_purchase import AccountPurchaseError

        quote_id = uuid.uuid4()
        user = SimpleNamespace(id=42)
        callback = MagicMock()
        callback.data = f"balance_purchase_cancel:{quote_id}"
        callback.answer = AsyncMock()
        session = MagicMock()
        session.commit = AsyncMock()

        with (
            patch.object(
                purchase_routes,
                "cancel_account_purchase_quote",
                new=AsyncMock(
                    side_effect=AccountPurchaseError("quote_not_active")
                ),
            ),
            patch.object(
                showcase_routes,
                "show_tariff_showcase_callback",
                new=AsyncMock(),
            ) as show_showcase,
        ):
            await purchase_routes.cancel_purchase(callback, session, user)

        callback.answer.assert_awaited_once()
        session.commit.assert_not_awaited()
        show_showcase.assert_not_awaited()

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
        tos_url = "https://telegra.ph/Polzovatelskoe-soglashenie-07-23-48"
        privacy_url = "https://telegra.ph/Politika-konfidencialnosti-07-23-84"
        allowed_urls = {tos_url, privacy_url}

        self.assertEqual(texts.TOS_AGREEMENT_URL, tos_url)
        self.assertEqual(texts.PRIVACY_POLICY_URL, privacy_url)
        self.assertNotIn("👤 Профиль", texts.FAQ_TEXT)
        self.assertIn("🤝 Пригласить друга", texts.FAQ_TEXT)

        for key in texts.get_all_text_keys():
            value = texts.get_text(key)
            if not isinstance(value, str):
                continue
            found_urls = re.findall(r"https://telegra\.ph/[A-Za-z0-9_-]+", value)
            self.assertTrue(
                set(found_urls).issubset(allowed_urls),
                msg=f"Unexpected legal URL in {key}: {found_urls}",
            )

    async def test_hub_renders_admin_button_only_for_configured_admin(self):
        admin = SimpleNamespace(
            id=10,
            telegram_id=100,
            first_name="Admin",
            referred_by=None,
            subscription_end=None,
            device_limit=2,
        )
        user = SimpleNamespace(
            id=11,
            telegram_id=101,
            first_name="User",
            referred_by=None,
            subscription_end=None,
            device_limit=2,
        )
        balance = SimpleNamespace(real_available=123, bonus_available=7)
        settings = SimpleNamespace(ADMIN_IDS={100})

        with (
            patch.object(start.SubscriptionService, "check_access", new=AsyncMock(return_value=True)),
            patch.object(start, "get_account_balance", new=AsyncMock(return_value=balance)),
            patch(
                "database.repositories.profiles_repo.get_user_profiles",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "database.repositories.system_settings_repo.get_system_setting",
                new=AsyncMock(return_value=None),
            ),
            patch.object(start, "get_settings", return_value=settings),
        ):
            _, admin_kb = await start._build_hub_text_and_kb(MagicMock(), admin)
            _, user_kb = await start._build_hub_text_and_kb(MagicMock(), user)

        admin_callbacks = [
            button.callback_data
            for row in admin_kb.inline_keyboard
            for button in row
            if button.callback_data
        ]
        user_callbacks = [
            button.callback_data
            for row in user_kb.inline_keyboard
            for button in row
            if button.callback_data
        ]
        self.assertIn("menu_admin", admin_callbacks)
        self.assertNotIn("menu_admin", user_callbacks)

    async def test_hub_renders_referrer(self):
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
        settings = SimpleNamespace(ADMIN_IDS=set())

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
            patch.object(start, "get_settings", return_value=settings),
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
