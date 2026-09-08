import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers.admin.users.balance_routes import (
    process_balance_deduct,
    process_balance_reason,
    process_balance_topup,
)
from database.repositories.account_ledger_repo import (
    AccountBalanceSnapshot,
)


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
            accounting_position=Decimal(100),
            available=Decimal(100),
            reserved=Decimal(0),
            debt=Decimal(0),
            bonus_available=Decimal(100),
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
            self.assertIn("недостаточно бонусных средств", text_arg)
            state.clear.assert_called_once()

    async def test_process_balance_topup_moves_to_reason_step(self):
        message = AsyncMock()
        message.from_user.id = 100
        message.text = "200"
        message.chat.id = 100
        message.message_id = 99

        state = AsyncMock()
        state.get_data.return_value = {"target_telegram_id": 888}

        session = AsyncMock()

        with (
            patch("bot.handlers.admin.users.balance_routes.is_admin", return_value=True),
            patch("bot.handlers.admin.users.balance_routes.render_hub") as mock_render,
        ):
            await process_balance_topup(message, state, session)

            mock_render.assert_called_once()
            state.update_data.assert_called_with(amount=200, action_type="topup")


    async def test_process_balance_reason_renders_confirmation(self):
        message = AsyncMock()
        message.from_user.id = 100
        message.text = "Компенсация за техработы"
        message.chat.id = 100
        message.message_id = 99

        state = AsyncMock()
        state.get_data.return_value = {"target_telegram_id": 888, "amount": 100, "action_type": "topup"}

        user = MagicMock()
        user.id = 7
        user.telegram_id = 888
        user.username = "test_user"

        session = AsyncMock()

        with (
            patch("bot.handlers.admin.users.balance_routes.is_admin", return_value=True),
            patch("bot.handlers.admin.users.balance_routes.get_user_by_telegram_id", return_value=user),
            patch("bot.handlers.admin.users.balance_routes.render_hub") as mock_render,
        ):
            await process_balance_reason(message, state, session)

            mock_render.assert_called_once()
            text_arg = mock_render.call_args[0][2]
            self.assertIn("Подтверждение изменения баланса", text_arg)
            self.assertIn("Компенсация за техработы", text_arg)

    async def test_admin_balance_preset_apply_with_real_frozen_callback_query(self):
        """Zero-mock test: real frozen CallbackQuery must not raise ValidationError on admin_bal_preset."""
        from aiogram.types import CallbackQuery, User as TgUser
        from bot.handlers.admin.users.balance_routes import admin_balance_preset

        real_callback = CallbackQuery(
            id="query_bal_123",
            from_user=TgUser(id=123456789, is_bot=False, first_name="Admin"),
            chat_instance="chat_inst_bal_1",
            data="admin_bal_preset:888:100",
        )
        object.__setattr__(real_callback, "answer", AsyncMock())
        msg = MagicMock()
        msg.chat.id = 123
        msg.message_id = 456
        object.__setattr__(real_callback, "message", msg)

        user = MagicMock()
        user.id = 7
        user.telegram_id = 888

        session = AsyncMock()

        with (
            patch("bot.handlers.admin.users.balance_routes.is_admin", return_value=True),
            patch("bot.handlers.admin.users.balance_routes.get_user_by_telegram_id", return_value=user),
            patch("bot.handlers.admin.users.balance_routes.check_and_record_admin_op", return_value=(True, None)),
            patch("bot.handlers.admin.users.balance_routes.create_admin_adjustment", return_value=None),
            patch("bot.handlers.admin.users.balance_routes.AuditService.log_action", new=AsyncMock()),
            patch("bot.handlers.admin.users.balance_routes.show_user_balance_menu", new=AsyncMock()) as mock_menu,
        ):
            await admin_balance_preset(real_callback, session)
            mock_menu.assert_awaited_once()
            call_kwargs = mock_menu.call_args[1]
            self.assertEqual(call_kwargs.get("target_telegram_id"), 888)
            self.assertEqual(real_callback.data, "admin_user_balance:888")


if __name__ == "__main__":
    unittest.main()
