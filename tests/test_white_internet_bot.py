"""Unit tests for Telegram bot handlers of White Internet."""

import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import CallbackQuery, User as TgUser

from bot.handlers.white_internet import (
    process_white_internet_buy,
    process_white_internet_renew,
    process_topup_pack,
)
from database.models import User
from database.repositories.account_ledger_repo import AccountBalanceSnapshot


class TestWhiteInternetBotHandlers(unittest.IsolatedAsyncioTestCase):
    """Test suite for Telegram UI callback handlers."""

    async def asyncSetUp(self):
        self.session = AsyncMock()
        mock_res = MagicMock()
        mock_res.scalar_one_or_none.return_value = None
        mock_res.scalars.return_value.all.return_value = []
        self.session.execute.return_value = mock_res
        self.session.add = MagicMock()
        self.user = User(id=42, telegram_id=999888777)
        self.tg_user = TgUser(id=999888777, is_bot=False, first_name="TestUser")
        self.trial_patcher = patch("bot.handlers.white_internet.WHITE_INTERNET_TRIAL_MODE_ONLY", False)
        self.trial_patcher.start()

    async def asyncTearDown(self):
        self.trial_patcher.stop()

    async def test_buy_confirm_with_insufficient_balance(self):
        query = MagicMock(spec=CallbackQuery)
        query.from_user = self.tg_user
        query.message = MagicMock()
        query.message.edit_text = AsyncMock()
        query.answer = AsyncMock()

        low_balance = AccountBalanceSnapshot(
            accounting_position=Decimal("50.00"),
            available=Decimal("50.00"),
            reserved=Decimal("0.00"),
            debt=Decimal("0.00"),
        )


        with patch("bot.handlers.white_internet.get_user_by_telegram_id", return_value=self.user):
            with patch("bot.handlers.white_internet.get_account_balance", return_value=low_balance) as mock_balance:
                await process_white_internet_buy(query, self.session)

                mock_balance.assert_awaited_once_with(self.session, user_id=self.user.id)
                query.message.edit_text.assert_awaited_once()
                args, _ = query.message.edit_text.call_args
                self.assertIn("Недостаточно средств", args[0])

    async def test_buy_confirm_with_sufficient_balance_success(self):
        query = MagicMock(spec=CallbackQuery)
        query.from_user = self.tg_user
        query.message = MagicMock()
        query.message.edit_text = AsyncMock()
        query.answer = AsyncMock()

        high_balance = AccountBalanceSnapshot(
            accounting_position=Decimal("500.00"),
            available=Decimal("500.00"),
            reserved=Decimal("0.00"),
            debt=Decimal("0.00"),
        )

        with patch("bot.handlers.white_internet.get_user_by_telegram_id", return_value=self.user):
            with patch("bot.handlers.white_internet.get_account_balance", return_value=high_balance) as mock_balance:
                with patch("services.white_internet_service.WhiteInternetService.purchase_subscription", return_value=(True, "OK", MagicMock())) as mock_buy:
                    with patch("bot.handlers.white_internet.show_white_internet_menu", new_callable=AsyncMock) as mock_menu:
                        await process_white_internet_buy(query, self.session)

                        mock_balance.assert_awaited_once_with(self.session, user_id=self.user.id)
                        mock_buy.assert_awaited_once_with(self.session, self.user.id)
                        self.session.commit.assert_awaited_once()
                        mock_menu.assert_awaited_once_with(query, self.session)

    async def test_renew_confirm_with_sufficient_balance_success(self):
        query = MagicMock(spec=CallbackQuery)
        query.from_user = self.tg_user
        query.message = MagicMock()
        query.message.edit_text = AsyncMock()
        query.answer = AsyncMock()

        high_balance = AccountBalanceSnapshot(
            accounting_position=Decimal("500.00"),
            available=Decimal("500.00"),
            reserved=Decimal("0.00"),
            debt=Decimal("0.00"),
        )


        with patch("bot.handlers.white_internet.get_user_by_telegram_id", return_value=self.user):
            with patch("bot.handlers.white_internet.get_account_balance", return_value=high_balance) as mock_balance:
                with patch("services.white_internet_service.WhiteInternetService.renew_subscription", return_value=(True, "OK", MagicMock())) as mock_renew:
                    with patch("bot.handlers.white_internet.show_white_internet_menu", new_callable=AsyncMock) as mock_menu:
                        await process_white_internet_renew(query, self.session)

                        mock_balance.assert_awaited_once_with(self.session, user_id=self.user.id)
                        mock_renew.assert_awaited_once_with(self.session, self.user.id)
                        self.session.commit.assert_awaited_once()
                        mock_menu.assert_awaited_once_with(query, self.session)

    async def test_topup_pack_with_sufficient_balance_success(self):
        query = MagicMock(spec=CallbackQuery)
        query.from_user = self.tg_user
        query.data = "wl_topup_pack_25"
        query.message = MagicMock()
        query.message.edit_text = AsyncMock()
        query.answer = AsyncMock()

        high_balance = AccountBalanceSnapshot(
            accounting_position=Decimal("150.00"),
            available=Decimal("150.00"),
            reserved=Decimal("0.00"),
            debt=Decimal("0.00"),
        )

        with patch("bot.handlers.white_internet.get_user_by_telegram_id", return_value=self.user):
            with patch("bot.handlers.white_internet.get_account_balance", return_value=high_balance) as mock_balance:
                with patch("services.white_internet_service.WhiteInternetService.topup_quota", return_value=(True, "OK", MagicMock())) as mock_topup:
                    with patch("bot.handlers.white_internet.show_white_internet_menu", new_callable=AsyncMock) as mock_menu:
                        await process_topup_pack(query, self.session)

                        mock_balance.assert_awaited_once_with(self.session, user_id=self.user.id)
                        mock_topup.assert_awaited_once_with(self.session, self.user.id, 25)
                        self.session.commit.assert_awaited_once()
                        mock_menu.assert_awaited_once_with(query, self.session)

    def test_overview_keyboard_generates_valid_telegram_buttons(self):
        """Active subscription keyboard must use native CopyTextButton and Bot API compliant buttons."""
        from config.enums import WhiteInternetProvisioningStatus, WhiteInternetStatus
        from database.models import WhiteInternetSubscription
        from bot.handlers.white_internet import get_white_internet_overview_keyboard

        sub = WhiteInternetSubscription(
            id=1,
            user_id=42,
            token="secure-token-123456789",
            uuid="a2b9d4e1-73c5-4812-b964-f3e7b85a1902",
            status=WhiteInternetStatus.ACTIVE,
            provisioning_status=WhiteInternetProvisioningStatus.ACTIVE,
        )

        domain = "vpn.just1k.online"
        kb = get_white_internet_overview_keyboard(sub, bot_domain=domain)

        # Telegram Bot API contract: inline keyboard URLs must only be http/https/tg
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.url:
                    self.assertTrue(
                        btn.url.startswith(("http://", "https://", "tg://")),
                        f"Forbidden custom URL scheme in inline button: {btn.url}",
                    )

        # Find the copy text button and instruction button
        copy_button = None
        instruction_button = None
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.copy_text and btn.copy_text.text == f"https://{domain}/sub/wl/{sub.token}":
                    copy_button = btn
                if btn.callback_data == "wl_show_link":
                    instruction_button = btn

        self.assertIsNotNone(copy_button, "Copy link button with CopyTextButton not found")
        self.assertEqual(copy_button.copy_text.text, f"https://{domain}/sub/wl/{sub.token}")
        self.assertIsNotNone(instruction_button, "Instruction button with wl_show_link callback not found")

    def test_overview_keyboard_all_states_return_valid_markup(self):
        """All subscription states must return non-empty InlineKeyboardMarkup with appropriate buttons."""
        from bot.handlers.white_internet import get_white_internet_overview_keyboard
        from config.enums import WhiteInternetProvisioningStatus, WhiteInternetStatus
        from database.models import WhiteInternetSubscription

        domain = "vpn.just1k.online"

        # 1. No subscription -> Buy button + Back button
        kb_none = get_white_internet_overview_keyboard(None, bot_domain=domain)
        self.assertIsNotNone(kb_none)
        callbacks_none = [btn.callback_data for row in kb_none.inline_keyboard for btn in row]
        self.assertIn("wl_buy_confirm", callbacks_none)
        self.assertIn("back_to_main_menu", callbacks_none)

        # 2. EXPIRED -> Renew button + Back button
        sub_expired = WhiteInternetSubscription(id=1, user_id=1, status=WhiteInternetStatus.EXPIRED)
        kb_expired = get_white_internet_overview_keyboard(sub_expired, bot_domain=domain)
        self.assertIsNotNone(kb_expired)
        callbacks_expired = [btn.callback_data for row in kb_expired.inline_keyboard for btn in row]
        self.assertIn("wl_renew_confirm", callbacks_expired)
        self.assertIn("back_to_main_menu", callbacks_expired)

        # 3. DISABLED -> Back button only
        sub_disabled = WhiteInternetSubscription(id=2, user_id=1, status=WhiteInternetStatus.DISABLED)
        kb_disabled = get_white_internet_overview_keyboard(sub_disabled, bot_domain=domain)
        self.assertIsNotNone(kb_disabled)
        callbacks_disabled = [btn.callback_data for row in kb_disabled.inline_keyboard for btn in row]
        self.assertEqual(callbacks_disabled, ["back_to_main_menu"])

        # 4. PENDING -> Back button only
        sub_pending = WhiteInternetSubscription(id=3, user_id=1, status=WhiteInternetStatus.PENDING)
        kb_pending = get_white_internet_overview_keyboard(sub_pending, bot_domain=domain)
        self.assertIsNotNone(kb_pending)
        callbacks_pending = [btn.callback_data for row in kb_pending.inline_keyboard for btn in row]
        self.assertEqual(callbacks_pending, ["back_to_main_menu"])

        # 5. EXHAUSTED -> Top-up + Renew + Back button
        sub_exhausted = WhiteInternetSubscription(id=4, user_id=1, status=WhiteInternetStatus.EXHAUSTED)
        kb_exhausted = get_white_internet_overview_keyboard(sub_exhausted, bot_domain=domain)
        self.assertIsNotNone(kb_exhausted)
        callbacks_exhausted = [btn.callback_data for row in kb_exhausted.inline_keyboard for btn in row]
        self.assertIn("wl_topup_menu", callbacks_exhausted)
        self.assertIn("wl_renew_confirm", callbacks_exhausted)
        self.assertIn("back_to_main_menu", callbacks_exhausted)

        # 6. ACTIVE + Provisioned -> Copy text + Instructions + Top-up + Renew + Back button
        sub_active = WhiteInternetSubscription(
            id=5,
            user_id=1,
            token="tok123",
            status=WhiteInternetStatus.ACTIVE,
            provisioning_status=WhiteInternetProvisioningStatus.ACTIVE,
        )
        kb_active = get_white_internet_overview_keyboard(sub_active, bot_domain=domain)
        self.assertIsNotNone(kb_active)
        callbacks_active = [btn.callback_data for row in kb_active.inline_keyboard for btn in row if btn.callback_data]
        self.assertIn("wl_show_link", callbacks_active)
        self.assertIn("wl_topup_menu", callbacks_active)
        self.assertIn("wl_renew_confirm", callbacks_active)
        self.assertIn("back_to_main_menu", callbacks_active)

    async def test_show_subscription_link_renders_clean_incy_instructions(self):
        """Clicking wl_show_link renders instructions for INCY and provides CopyTextButton for subscription."""
        from unittest.mock import AsyncMock, patch
        from aiogram.types import CallbackQuery, User as TgUser, Message
        from database.models import User, WhiteInternetSubscription
        from config.enums import WhiteInternetStatus
        from bot.handlers.white_internet import show_subscription_link

        user = User(id=1, telegram_id=123456789)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=1,
            token="sub-secret-token",
            status=WhiteInternetStatus.ACTIVE,
        )

        mock_query = AsyncMock(spec=CallbackQuery)
        mock_query.answer = AsyncMock()
        mock_query.from_user = TgUser(id=123456789, is_bot=False, first_name="Tester")
        mock_query.message = AsyncMock(spec=Message)
        mock_query.message.edit_text = AsyncMock()

        mock_session = AsyncMock()

        mock_settings = MagicMock(DOMAIN="bot.example.com")
        with patch("bot.handlers.white_internet.get_settings", return_value=mock_settings):
            with patch("bot.handlers.white_internet.get_user_by_telegram_id", return_value=user):
                with patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=sub):
                    await show_subscription_link(mock_query, mock_session)

                mock_query.message.edit_text.assert_awaited_once()
                call_args = mock_query.message.edit_text.call_args
                text = call_args[0][0]
                reply_markup = call_args[1]["reply_markup"]

                self.assertIn("INCY", text)
                self.assertIn("sub-secret-token", text)

                # Check keyboard buttons
                copy_btns = []
                back_btn = None
                for row in reply_markup.inline_keyboard:
                    for btn in row:
                        if btn.copy_text:
                            copy_btns.append(btn)
                        if btn.callback_data == "white_internet":
                            back_btn = btn

                self.assertTrue(len(copy_btns) >= 1, "Copy subscription link button must be present")
                self.assertTrue(any("sub-secret-token" in b.copy_text.text for b in copy_btns))
                self.assertIsNotNone(back_btn, "Back button must be present")

    async def test_resolve_subscription_domain_priority(self):
        """Domain resolution prioritizes server cdn_domain, then env var, then bot domain, then server domain."""
        import os
        from unittest.mock import AsyncMock, MagicMock, patch
        from bot.handlers.white_internet import _resolve_subscription_domain
        from database.models import Server, WhiteInternetSubscription

        sub = WhiteInternetSubscription(id=1, user_id=1, origin_node_id=10)
        server_with_cdn = Server(
            id=10,
            name="Origin",
            api_url="https://origin.example.com:8444",
            api_key="key",
            extra_data={"cdn_domain": "cdn.example.com"},
        )

        mock_session = AsyncMock()
        mock_session.get.return_value = server_with_cdn

        # 1. Server extra_data['cdn_domain'] has highest priority
        domain = await _resolve_subscription_domain(mock_session, sub)
        self.assertEqual(domain, "cdn.example.com")

        # 2. Env fallback when server extra_data lacks cdn_domain
        server_no_cdn = Server(
            id=10,
            name="Origin",
            api_url="https://origin.example.com:8444",
            api_key="key",
            extra_data={},
        )
        mock_session.get.return_value = server_no_cdn
        with patch.dict(os.environ, {"WHITE_INTERNET_CDN_DOMAIN": "envcdn.example.com"}):
            domain = await _resolve_subscription_domain(mock_session, sub)
            self.assertEqual(domain, "envcdn.example.com")

        # 3. Bot domain fallback when no CDN domain configured
        mock_settings = MagicMock(DOMAIN="bot.example.com")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WHITE_INTERNET_CDN_DOMAIN", None)
            with patch("bot.handlers.white_internet.get_settings", return_value=mock_settings):
                domain = await _resolve_subscription_domain(mock_session, sub)
                self.assertEqual(domain, "bot.example.com")

        # 4. Server primary domain fallback when bot domain missing
        mock_settings_empty = MagicMock(DOMAIN="")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WHITE_INTERNET_CDN_DOMAIN", None)
            os.environ.pop("DOMAIN", None)
            os.environ.pop("BOT_DOMAIN", None)
            with patch("bot.handlers.white_internet.get_settings", return_value=mock_settings_empty):
                domain = await _resolve_subscription_domain(mock_session, sub)
                self.assertEqual(domain, "origin.example.com")

    async def test_show_subscription_link_uses_cdn_domain_when_available(self):
        """When origin node has cdn_domain, wl_show_link button text uses cdn_domain."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from aiogram.types import CallbackQuery, User as TgUser, Message
        from database.models import User, Server, WhiteInternetSubscription
        from config.enums import WhiteInternetStatus
        from bot.handlers.white_internet import show_subscription_link

        user = User(id=1, telegram_id=123456789)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=1,
            origin_node_id=10,
            token="sub-secret-token",
            status=WhiteInternetStatus.ACTIVE,
        )
        origin_node = Server(
            id=10,
            name="Origin",
            api_url="https://origin.just1k.best:8444",
            api_key="key",
            extra_data={"cdn_domain": "cdn.just1k.best"},
        )

        mock_query = AsyncMock(spec=CallbackQuery)
        mock_query.answer = AsyncMock()
        mock_query.from_user = TgUser(id=123456789, is_bot=False, first_name="Tester")
        mock_query.message = AsyncMock(spec=Message)
        mock_query.message.edit_text = AsyncMock()

        mock_session = AsyncMock()
        mock_session.get.return_value = origin_node

        mock_settings = MagicMock(DOMAIN="just1k.best")
        with patch("bot.handlers.white_internet.get_settings", return_value=mock_settings):
            with patch("bot.handlers.white_internet.get_user_by_telegram_id", return_value=user):
                with patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=sub):
                    await show_subscription_link(mock_query, mock_session)

        mock_query.message.edit_text.assert_awaited_once()
        call_args = mock_query.message.edit_text.call_args
        text = call_args[0][0]
        reply_markup = call_args[1]["reply_markup"]

        self.assertIn("https://cdn.just1k.best/sub/wl/sub-secret-token", text)
        copy_btn = next((btn for row in reply_markup.inline_keyboard for btn in row if btn.copy_text), None)
        self.assertIsNotNone(copy_btn)
        self.assertEqual(copy_btn.copy_text.text, "https://cdn.just1k.best/sub/wl/sub-secret-token")
