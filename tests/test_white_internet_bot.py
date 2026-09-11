"""Unit tests for Telegram bot handlers of White Internet."""

import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import CallbackQuery, User as TgUser

from bot.handlers.white_internet import (
    handle_wl_reset_devices,
    process_white_internet_buy,
    process_white_internet_renew,
    process_topup_pack,
    process_add_device_confirm,
)
from config.enums import WhiteInternetStatus
from database.models import User, WhiteInternetSubscription
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

        with patch("bot.handlers.white_internet.is_admin", return_value=True), \
             patch("bot.handlers.white_internet.get_user_by_telegram_id", return_value=self.user), \
             patch("bot.handlers.white_internet.get_account_balance", return_value=high_balance) as mock_balance, \
             patch("services.white_internet_service.WhiteInternetService.topup_quota", return_value=(True, "OK", MagicMock())) as mock_topup, \
             patch("bot.handlers.white_internet.show_white_internet_menu", new_callable=AsyncMock) as mock_menu:
            await process_topup_pack(query, self.session)

            mock_balance.assert_awaited_once_with(self.session, user_id=self.user.id)
            mock_topup.assert_awaited_once_with(self.session, self.user.id, 25, actor_telegram_id=self.tg_user.id)
            self.session.commit.assert_awaited_once()
            mock_menu.assert_awaited_once_with(query, self.session)

    async def test_topup_pack_allows_repeated_purchases(self):
        """Users can repeatedly buy quota packs without being trapped by message_id idempotency."""
        query = MagicMock(spec=CallbackQuery)
        query.from_user = self.tg_user
        query.data = "wl_topup_pack_25"
        query.message = MagicMock()
        query.message.message_id = 999
        query.answer = AsyncMock()

        high_balance = AccountBalanceSnapshot(
            accounting_position=Decimal("300.00"),
            available=Decimal("300.00"),
            reserved=Decimal("0.00"),
            debt=Decimal("0.00"),
        )

        with patch("bot.handlers.white_internet.is_admin", return_value=True), \
             patch("bot.handlers.white_internet.get_user_by_telegram_id", return_value=self.user), \
             patch("bot.handlers.white_internet.get_account_balance", return_value=high_balance), \
             patch("services.white_internet_service.WhiteInternetService.topup_quota", return_value=(True, "OK", MagicMock())) as mock_topup, \
             patch("bot.handlers.white_internet.show_white_internet_menu", new_callable=AsyncMock):
            # First purchase
            await process_topup_pack(query, self.session)
            self.assertEqual(mock_topup.await_count, 1)

            # Second purchase with identical message_id must not be blocked by ADMIN_BALANCE_OP_ALREADY_PROCESSED
            await process_topup_pack(query, self.session)
            self.assertEqual(mock_topup.await_count, 2)

    async def test_add_device_confirm_allows_repeated_purchases(self):
        """Users can repeatedly buy device slots without being trapped by message_id idempotency."""
        query = MagicMock(spec=CallbackQuery)
        query.from_user = self.tg_user
        query.data = "wl_add_device_confirm"
        query.message = MagicMock()
        query.message.message_id = 888
        query.answer = AsyncMock()

        sub = WhiteInternetSubscription(
            id=1,
            user_id=self.user.id,
            status=WhiteInternetStatus.ACTIVE,
            device_limit=1,
            is_trial=False,
        )

        high_balance = AccountBalanceSnapshot(
            accounting_position=Decimal("500.00"),
            available=Decimal("500.00"),
            reserved=Decimal("0.00"),
            debt=Decimal("0.00"),
        )

        with patch("bot.handlers.white_internet.is_admin", return_value=True), \
             patch("bot.handlers.white_internet.get_user_by_telegram_id", return_value=self.user), \
             patch("bot.handlers.white_internet.white_internet_repo.get_subscription_by_user_id", return_value=sub), \
             patch("bot.handlers.white_internet.get_account_balance", return_value=high_balance), \
             patch("services.white_internet_service.WhiteInternetService.purchase_device_slot", return_value=(True, "OK", sub)) as mock_buy_slot, \
             patch("bot.handlers.white_internet.show_white_internet_menu", new_callable=AsyncMock):
            # First device slot purchase
            await process_add_device_confirm(query, self.session)
            self.assertEqual(mock_buy_slot.await_count, 1)

            # Second device slot purchase with identical message_id must not be blocked by ADMIN_BALANCE_OP_ALREADY_PROCESSED
            await process_add_device_confirm(query, self.session)
            self.assertEqual(mock_buy_slot.await_count, 2)

    def test_action_lock_middleware_prefixes_configured(self):
        """ActionLockMiddleware must configure locked and stale prefixes for White Internet purchase actions."""
        from bot.middlewares.action_lock import LOCKED_ACTION_PREFIXES, STALE_ACTION_PREFIXES

        self.assertIn("wl_topup_execute:", LOCKED_ACTION_PREFIXES)
        self.assertIn("wl_topup_pack_", LOCKED_ACTION_PREFIXES)
        self.assertIn("wl_topup_shortage:", LOCKED_ACTION_PREFIXES)
        self.assertIn("wl_add_device_confirm", LOCKED_ACTION_PREFIXES)
        self.assertIn("wl_buy_execute", LOCKED_ACTION_PREFIXES)
        self.assertIn("wl_buy_confirm", LOCKED_ACTION_PREFIXES)
        self.assertIn("wl_renew_execute", LOCKED_ACTION_PREFIXES)
        self.assertIn("wl_renew_confirm", LOCKED_ACTION_PREFIXES)

        self.assertIn("wl_topup_execute:", STALE_ACTION_PREFIXES)
        self.assertIn("wl_topup_pack_", STALE_ACTION_PREFIXES)
        self.assertIn("wl_topup_shortage:", STALE_ACTION_PREFIXES)
        self.assertIn("wl_add_device_confirm", STALE_ACTION_PREFIXES)
        self.assertIn("wl_buy_execute", STALE_ACTION_PREFIXES)
        self.assertIn("wl_buy_confirm", STALE_ACTION_PREFIXES)
        self.assertIn("wl_renew_execute", STALE_ACTION_PREFIXES)
        self.assertIn("wl_renew_confirm", STALE_ACTION_PREFIXES)

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
        self.assertTrue("wl_buy_preview" in callbacks_none or "wl_buy_confirm" in callbacks_none)
        self.assertIn("back_to_main_menu", callbacks_none)

        # 2. EXPIRED -> Renew button + Back button
        sub_expired = WhiteInternetSubscription(id=1, user_id=1, status=WhiteInternetStatus.EXPIRED)
        kb_expired = get_white_internet_overview_keyboard(sub_expired, bot_domain=domain)
        self.assertIsNotNone(kb_expired)
        callbacks_expired = [btn.callback_data for row in kb_expired.inline_keyboard for btn in row]
        self.assertTrue("wl_renew_preview" in callbacks_expired or "wl_renew_confirm" in callbacks_expired)
        self.assertIn("back_to_main_menu", callbacks_expired)

        # 3. DISABLED -> Back button only
        sub_disabled = WhiteInternetSubscription(id=2, user_id=1, status=WhiteInternetStatus.DISABLED)
        kb_disabled = get_white_internet_overview_keyboard(sub_disabled, bot_domain=domain)
        self.assertIsNotNone(kb_disabled)
        callbacks_disabled = [btn.callback_data for row in kb_disabled.inline_keyboard for btn in row]
        self.assertEqual(callbacks_disabled, ["back_to_main_menu"])

        # 4. PENDING -> Refresh button + Back button
        sub_pending = WhiteInternetSubscription(id=3, user_id=1, status=WhiteInternetStatus.PENDING)
        kb_pending = get_white_internet_overview_keyboard(sub_pending, bot_domain=domain)
        self.assertIsNotNone(kb_pending)
        callbacks_pending = [btn.callback_data for row in kb_pending.inline_keyboard for btn in row]
        self.assertEqual(callbacks_pending, ["white_internet", "back_to_main_menu"])

        # 5. EXHAUSTED -> Top-up + Renew + Back button (admin)
        sub_exhausted = WhiteInternetSubscription(id=4, user_id=1, status=WhiteInternetStatus.EXHAUSTED)
        kb_exhausted = get_white_internet_overview_keyboard(sub_exhausted, bot_domain=domain, is_admin_user=True)
        self.assertIsNotNone(kb_exhausted)
        callbacks_exhausted = [btn.callback_data for row in kb_exhausted.inline_keyboard for btn in row]
        self.assertIn("wl_topup_menu", callbacks_exhausted)
        self.assertTrue("wl_renew_preview" in callbacks_exhausted or "wl_renew_confirm" in callbacks_exhausted)
        self.assertIn("back_to_main_menu", callbacks_exhausted)

        # 6. ACTIVE + Provisioned -> Copy text + Instructions + Top-up + Renew + Back button (admin)
        sub_active = WhiteInternetSubscription(
            id=5,
            user_id=1,
            token="tok123",
            status=WhiteInternetStatus.ACTIVE,
            provisioning_status=WhiteInternetProvisioningStatus.ACTIVE,
        )
        kb_active = get_white_internet_overview_keyboard(sub_active, bot_domain=domain, is_admin_user=True)
        self.assertIsNotNone(kb_active)
        callbacks_active = [btn.callback_data for row in kb_active.inline_keyboard for btn in row if btn.callback_data]
        self.assertIn("wl_show_link", callbacks_active)
        self.assertIn("wl_topup_menu", callbacks_active)
        self.assertTrue("wl_renew_preview" in callbacks_active or "wl_renew_confirm" in callbacks_active)
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

    async def test_show_white_internet_menu_formats_adaptive_traffic(self):
        """Traffic is formatted adaptively (KiB/MiB/GiB), so small usage is not hidden as 0.0 ГБ."""
        from datetime import datetime, timezone
        from bot.handlers.white_internet import show_white_internet_menu, _format_bytes
        from database.models import WhiteInternetSubscription
        from config.enums import WhiteInternetStatus

        self.assertEqual(_format_bytes(468480), "457.5 KiB")
        self.assertEqual(_format_bytes(10 * 1024**3), "10.0 GiB")

        used_bytes = 468480  # 457.5 KiB
        total_bytes = 10 * 1024**3  # 10.0 GiB
        avail_bytes = total_bytes - used_bytes

        sub = WhiteInternetSubscription(
            id=1,
            user_id=self.user.id,
            origin_node_id=1,
            token="token123",
            status=WhiteInternetStatus.ACTIVE,
            traffic_limit_bytes=total_bytes,
            traffic_used_bytes=used_bytes,
            expires_at=datetime(2026, 9, 8, 15, 9, tzinfo=timezone.utc),
        )

        query = MagicMock(spec=CallbackQuery)
        query.from_user = self.tg_user
        query.message = MagicMock()
        query.message.edit_text = AsyncMock()
        query.answer = AsyncMock()

        with patch("bot.handlers.white_internet.get_user_by_telegram_id", return_value=self.user), \
             patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=sub), \
             patch("database.repositories.white_internet_repo.get_available_quota_bytes", return_value=avail_bytes), \
             patch("bot.handlers.white_internet._resolve_subscription_domain", return_value="cdn.example.com"):
            await show_white_internet_menu(query, self.session)

        query.message.edit_text.assert_awaited_once()
        rendered_text = query.message.edit_text.call_args[0][0]
        self.assertIn("457.5 KiB", rendered_text)
        self.assertIn("10.0 GiB", rendered_text)
        self.assertNotIn("0.0 ГБ", rendered_text)
        self.assertIn("Устройства:</b> 0 из 1", rendered_text)

    async def test_wl_reset_devices_success(self):
        query = MagicMock(spec=CallbackQuery)
        query.from_user = self.tg_user
        query.message = MagicMock()
        query.message.edit_text = AsyncMock()
        query.answer = AsyncMock()

        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.id = 42
        sub.user_id = self.user.id
        sub.status = WhiteInternetStatus.ACTIVE
        sub.active_hwids = {"hwid-1": "2026-09-01T10:00:00+00:00"}

        with patch("bot.handlers.white_internet.get_user_by_telegram_id", return_value=self.user), \
             patch("bot.handlers.white_internet.white_internet_repo.get_subscription_by_user_id", return_value=sub), \
             patch("bot.handlers.white_internet.white_internet_repo.reset_active_hwids_atomic", new_callable=AsyncMock) as mock_reset, \
             patch("bot.handlers.white_internet.show_white_internet_menu", new_callable=AsyncMock) as mock_menu:
            await handle_wl_reset_devices(query, self.session)
            mock_reset.assert_awaited_once_with(self.session, sub.id)
            self.session.commit.assert_awaited_once()
            query.answer.assert_awaited_once()
            mock_menu.assert_awaited_once_with(query, self.session)

    async def test_wl_reset_devices_no_sub(self):
        query = MagicMock(spec=CallbackQuery)
        query.from_user = self.tg_user
        query.message = MagicMock()
        query.answer = AsyncMock()

        with patch("bot.handlers.white_internet.get_user_by_telegram_id", return_value=self.user), \
             patch("bot.handlers.white_internet.white_internet_repo.get_subscription_by_user_id", return_value=None):
            await handle_wl_reset_devices(query, self.session)
            query.answer.assert_awaited_once()
            self.session.commit.assert_not_called()

    async def test_user_receives_strictly_sub_url_never_raw_vless_or_config(self):
        """User receives exclusively https:// subscription URLs, never raw vless:// keys or raw configs."""
        from bot.handlers.white_internet import get_white_internet_overview_keyboard, show_subscription_link
        from config.enums import WhiteInternetProvisioningStatus, WhiteInternetStatus
        from database.models import WhiteInternetSubscription

        for is_trial in (True, False):
            sub = WhiteInternetSubscription(
                id=1,
                user_id=self.user.id,
                token="test-secret-token-abc123xyz",
                uuid="a1b2c3d4-e5f6-47a8-b9c0-d1e2f3a4b5c6",
                status=WhiteInternetStatus.ACTIVE,
                provisioning_status=WhiteInternetProvisioningStatus.ACTIVE,
                is_trial=is_trial,
            )
            domain = "cdn.just1k.online"
            kb = get_white_internet_overview_keyboard(sub, bot_domain=domain)

            copy_btns = [btn for row in kb.inline_keyboard for btn in row if btn.copy_text]
            self.assertEqual(len(copy_btns), 1)
            copy_text = copy_btns[0].copy_text.text
            self.assertTrue(copy_text.startswith("https://"), f"Must start with https://, got: {copy_text}")
            self.assertIn("/sub/", copy_text)
            self.assertIn(sub.token, copy_text)
            self.assertNotIn("vless://", copy_text)
            self.assertNotIn("vmess://", copy_text)
            self.assertNotIn(sub.uuid, copy_text)

            query = MagicMock(spec=CallbackQuery)
            query.from_user = self.tg_user
            query.message = MagicMock()
            query.message.edit_text = AsyncMock()
            query.answer = AsyncMock()

            with patch("bot.handlers.white_internet.get_user_by_telegram_id", return_value=self.user), \
                 patch("bot.handlers.white_internet.white_internet_repo.get_subscription_by_user_id", return_value=sub), \
                 patch("bot.handlers.white_internet._resolve_subscription_domain", return_value=domain), \
                 patch("bot.handlers.white_internet._resolve_subscription_prefix_for_sub", return_value="/sub/wl"):
                await show_subscription_link(query, self.session)

            query.message.edit_text.assert_awaited()
            rendered_text = query.message.edit_text.call_args[0][0]
            markup = query.message.edit_text.call_args[1]["reply_markup"]

            self.assertIn(f"https://{domain}/sub/wl/{sub.token}", rendered_text)
            self.assertNotIn("vless://", rendered_text)
            self.assertNotIn(sub.uuid, rendered_text)

            modal_copy_btns = [btn for row in markup.inline_keyboard for btn in row if btn.copy_text]
            self.assertEqual(len(modal_copy_btns), 1)
            modal_copy_text = modal_copy_btns[0].copy_text.text
            self.assertEqual(modal_copy_text, f"https://{domain}/sub/wl/{sub.token}")
            self.assertNotIn("vless://", modal_copy_text)

    async def test_process_wl_topup_shortage_contextual_actions(self):
        """Shortage top-up callbacks must properly parse action type and pass into context."""
        from bot.handlers.white_internet import process_wl_topup_shortage

        test_cases = [
            ("wl_topup_shortage:150:buy", {"source": "white_internet", "auto_fulfill_action": "white_internet_buy"}),
            ("wl_topup_shortage:200:renew", {"source": "white_internet", "auto_fulfill_action": "white_internet_renew"}),
            ("wl_topup_shortage:30:add_device", {"source": "white_internet", "auto_fulfill_action": "white_internet_add_device"}),
            ("wl_topup_shortage:50:pack:10", {"source": "white_internet", "auto_fulfill_action": "white_internet_pack", "pack_gb": 10}),
            ("wl_topup_shortage:100", {"source": "white_internet", "auto_fulfill_action": "white_internet_buy"}),
        ]

        for callback_str, expected_context in test_cases:
            query = MagicMock(spec=CallbackQuery)
            query.data = callback_str
            query.from_user = self.tg_user
            query.answer = AsyncMock()

            with patch("bot.handlers.white_internet.get_user_by_telegram_id", return_value=self.user), \
                 patch("bot.handlers.white_internet.MaintenanceService.can_user_perform_action", return_value=True), \
                 patch("bot.handlers.white_internet._create_and_render_topup", new_callable=AsyncMock) as mock_create:
                await process_wl_topup_shortage(query, self.session)

                mock_create.assert_awaited_once()
                call_kwargs = mock_create.call_args[1]
                self.assertEqual(call_kwargs["context"], expected_context)

    async def test_settle_succeeded_topup_white_internet_auto_fulfill(self):
        """settle_succeeded_topup must automatically execute White Internet purchase when auto_fulfill_action is set."""
        from services.account_topup import settle_succeeded_topup
        from database.models import Payment
        from utils.datetime_helpers import now_utc

        payment = Payment(
            id=99,
            user_id=self.user.id,
            amount=Decimal("150.00"),
            currency="RUB",
            public_order_id="topup_test_99",
            provider_status="succeeded",
            provider_confirmed_at=now_utc(),
            paid_at=now_utc(),
            fulfillment_status="not_ready",
            reconciliation_status="ok",
            topup_context={
                "source": "white_internet",
                "auto_fulfill_action": "white_internet_buy",
            },
        )

        with patch("services.account_topup.credit_succeeded_topup", return_value=(MagicMock(), True)), \
             patch("services.account_topup.lock_checkout_user", return_value=self.user), \
             patch("services.account_topup.get_account_balance", return_value=MagicMock(real_position=Decimal(150), accounting_position=Decimal(150), available=Decimal(150), real_available=Decimal(150), bonus_available=Decimal(0))), \
             patch("services.account_topup.refresh_user_dispute_hold", new_callable=AsyncMock), \
             patch("services.white_internet_service.WhiteInternetService.purchase_subscription", return_value=(True, "OK", MagicMock())) as mock_buy, \
             patch("services.audit_service.AuditService.log_action", new_callable=AsyncMock), \
             patch("services.referral_bonus.grant_referral_bonus_for_topup", return_value=Decimal(0)):

            bot = MagicMock()
            mock_settings = MagicMock()
            mock_settings.BALANCE_MAX_AVAILABLE_RUB = "50000"
            settled, _ = await settle_succeeded_topup(
                self.session,
                payment=payment,
                source="test",
                settings=mock_settings,
                bot=bot,
            )

            self.assertTrue(settled)
            mock_buy.assert_awaited_once_with(self.session, user_id=self.user.id)
            self.assertEqual(payment.topup_context.get("auto_fulfill_status"), "succeeded")

    async def test_hub_header_status_multi_protocol_matrix(self):
        from datetime import timedelta
        from bot.handlers.start import _build_hub_text_and_kb
        from bot import texts
        from config.enums import WhiteInternetStatus
        from utils.datetime_helpers import now_utc

        now = now_utc()
        future = now + timedelta(days=5)

        db_user = MagicMock(spec=User)
        db_user.id = 1
        db_user.telegram_id = 12345
        db_user.first_name = "Alice"
        db_user.subscription_end = future
        db_user.device_limit = 2
        db_user.referred_by = None

        mock_balance = MagicMock(real_available=100, bonus_available=0)

        # Helper to run _build_hub_text_and_kb with mocked check_access and get_subscription
        async def run_hub(awg_active: bool, wi_sub_val):
            with (
                patch("services.subscription.SubscriptionService.check_access", new=AsyncMock(return_value=awg_active)),
                patch("database.repositories.white_internet_repo.get_subscription_by_user_id", new=AsyncMock(return_value=wi_sub_val)),
                patch("bot.handlers.start.get_account_balance", new=AsyncMock(return_value=mock_balance)),
                patch("bot.handlers.start.get_settings", return_value=MagicMock(ADMIN_IDS=[999999])),
                patch("database.repositories.profiles_repo.get_user_profiles", new=AsyncMock(return_value=[])),
                patch("database.repositories.system_settings_repo.get_system_setting", new=AsyncMock(return_value=None)),
            ):
                text, kb = await _build_hub_text_and_kb(self.session, db_user)
                return text

        # 1. Dual active: AWG + WI
        wi_active = MagicMock(status=WhiteInternetStatus.ACTIVE, expires_at=future)
        text1 = await run_hub(True, wi_active)
        self.assertIn(texts.STATUS_SUBSCRIPTION_ACTIVE_DUAL, text1)

        # 2. AWG active + WI exhausted
        wi_exhausted = MagicMock(status=WhiteInternetStatus.EXHAUSTED, expires_at=future)
        text2 = await run_hub(True, wi_exhausted)
        self.assertIn(texts.STATUS_SUBSCRIPTION_ACTIVE_AWG_WI_EXHAUSTED, text2)

        # 3. AWG active + no WI
        text3 = await run_hub(True, None)
        self.assertIn(texts.STATUS_SUBSCRIPTION_ACTIVE, text3)
        self.assertNotIn(texts.STATUS_SUBSCRIPTION_ACTIVE_DUAL, text3)

        # 4. AWG inactive + WI active
        text4 = await run_hub(False, wi_active)
        self.assertIn(texts.STATUS_SUBSCRIPTION_ACTIVE_WI, text4)

        # 5. AWG inactive + WI exhausted
        text5 = await run_hub(False, wi_exhausted)
        self.assertIn(texts.STATUS_SUBSCRIPTION_EXHAUSTED_WI, text5)
        self.assertNotIn("🟢", text5)

        # 6. Both inactive
        text6 = await run_hub(False, None)
        self.assertIn(texts.STATUS_SUBSCRIPTION_INACTIVE, text6)
