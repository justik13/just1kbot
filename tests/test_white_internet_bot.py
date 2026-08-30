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

        with patch("bot.handlers.white_internet.get_user_by_telegram_id", return_value=self.user):
            with patch("bot.handlers.white_internet.get_account_balance", return_value=high_balance) as mock_balance:
                with patch("services.white_internet_service.WhiteInternetService.topup_quota", return_value=(True, "OK", MagicMock())) as mock_topup:
                    with patch("bot.handlers.white_internet.show_white_internet_menu", new_callable=AsyncMock) as mock_menu:
                        await process_topup_pack(query, self.session)

                        mock_balance.assert_awaited_once_with(self.session, user_id=self.user.id)
                        mock_topup.assert_awaited_once_with(self.session, self.user.id, 25)
                        self.session.commit.assert_awaited_once()
                        mock_menu.assert_awaited_once_with(query, self.session)

    def test_overview_keyboard_generates_valid_incy_deep_link(self):
        """Active subscription keyboard must include decrypted-valid incy://crypt1/ deep link."""
        from config.enums import WhiteInternetProvisioningStatus, WhiteInternetStatus
        from database.models import WhiteInternetSubscription
        from bot.handlers.white_internet import get_white_internet_overview_keyboard
        from services.incy_crypto import decrypt_link

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

        # Find the connect button with URL
        connect_button = None
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.url and btn.url.startswith("incy://crypt1/"):
                    connect_button = btn
                    break

        self.assertIsNotNone(connect_button, "Connect in App button with incy://crypt1/ URL not found")
        decrypted = decrypt_link(connect_button.url)
        self.assertEqual(decrypted["url"], f"https://{domain}/sub/wl/{sub.token}")
        self.assertEqual(decrypted["name"], "Just1k Белый Интернет")

        # Find the copy text button
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


