from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from database.repositories.account_ledger_repo import (
    AccountBalanceSnapshot,
    AccountLedgerInvariantError,
)
from bot.handlers.admin.users.balance_routes import process_balance_deduct


class TestAdminBalanceRoutes(unittest.IsolatedAsyncioTestCase):
    async def test_process_balance_deduct_insufficient_funds(self):
        message = AsyncMock()
        message.from_user.id = 100
        message.text = "500"
        message.chat.id = 100
        message.message_id = 99

        state = AsyncMock()
        state.get_data.return_value = {"target_telegram_id": 888}

        user = MagicMock()
        user.id = 7
        user.telegram_id = 888

        snapshot = AccountBalanceSnapshot(
            accounting_position=Decimal("100"),
            available=Decimal("100"),
            reserved=Decimal("0"),
            debt=Decimal("0"),
        )

        session = AsyncMock()

        with (
            patch("bot.handlers.admin.users.balance_routes.is_admin", return_value=True),
            patch("bot.handlers.admin.users.balance_routes.get_user_by_telegram_id", return_value=user),
            patch("bot.handlers.admin.users.balance_routes.get_account_balance", return_value=snapshot),
            patch("bot.handlers.admin.users.balance_routes.render_hub") as mock_render,
        ):
            await process_balance_deduct(message, state, session)

            mock_render.assert_called_once()
            text_arg = mock_render.call_args[0][2]
            self.assertIn("недостаточно средств", text_arg)
            state.clear.assert_called_once()

    async def test_process_balance_deduct_handles_ledger_error(self):
        message = AsyncMock()
        message.from_user.id = 100
        message.text = "50"
        message.chat.id = 100
        message.message_id = 99

        state = AsyncMock()
        state.get_data.return_value = {"target_telegram_id": 888}

        user = MagicMock()
        user.id = 7
        user.telegram_id = 888

        snapshot = AccountBalanceSnapshot(
            accounting_position=Decimal("100"),
            available=Decimal("100"),
            reserved=Decimal("0"),
            debt=Decimal("0"),
        )

        session = AsyncMock()

        with (
            patch("bot.handlers.admin.users.balance_routes.is_admin", return_value=True),
            patch("bot.handlers.admin.users.balance_routes.get_user_by_telegram_id", return_value=user),
            patch("bot.handlers.admin.users.balance_routes.get_account_balance", return_value=snapshot),
            patch("bot.handlers.admin.users.balance_routes.create_admin_adjustment", side_effect=AccountLedgerInvariantError("lots_error")),
            patch("bot.handlers.admin.users.balance_routes.render_hub") as mock_render,
        ):
            await process_balance_deduct(message, state, session)

            mock_render.assert_called_once()
            text_arg = mock_render.call_args[0][2]
            self.assertIn("Не удалось списать средства", text_arg)
            state.clear.assert_called_once()


if __name__ == "__main__":
    unittest.main()
