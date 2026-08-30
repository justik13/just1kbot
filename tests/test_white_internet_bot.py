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

