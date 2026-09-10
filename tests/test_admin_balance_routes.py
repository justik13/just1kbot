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
            patch("bot.handlers.admin.users.balance_routes.create_admin_adjustment", new=AsyncMock(return_value=(MagicMock(), True))) as mock_adj,
            patch("bot.handlers.admin.users.balance_routes.AuditService.log_action", new=AsyncMock()),
            patch("bot.handlers.admin.users.balance_routes.show_user_balance_menu", new=AsyncMock()) as mock_menu,
        ):
            await admin_balance_preset(real_callback, session)
            mock_menu.assert_awaited_once()
            call_kwargs = mock_menu.call_args[1]
            self.assertEqual(call_kwargs.get("target_telegram_id"), 888)
            self.assertEqual(real_callback.data, "admin_user_balance:888")
            mock_adj.assert_awaited_once()
            _, adj_kwargs = mock_adj.call_args
            self.assertEqual(adj_kwargs["user_id"], 7)
            self.assertEqual(adj_kwargs["signed_amount"], 100)
            self.assertEqual(adj_kwargs["metadata"], {"admin_id": 123456789, "reason": "preset_100_123456789"})
            self.assertTrue(adj_kwargs["idempotency_key"].startswith("admin_adj:"))

    async def test_admin_balance_preset_signature_compatible_with_real_repo(self):
        """Verify admin_balance_preset parameter names match real create_admin_adjustment signature."""
        import inspect
        from bot.handlers.admin.users.balance_routes import create_admin_adjustment as handler_adj
        from database.repositories.account_ledger_repo import create_admin_adjustment as repo_adj

        self.assertIs(handler_adj, repo_adj)
        sig = inspect.signature(repo_adj)
        # Verify repo signature has expected parameter names
        self.assertIn("signed_amount", sig.parameters)
        self.assertIn("idempotency_key", sig.parameters)
        self.assertIn("metadata", sig.parameters)
        self.assertNotIn("amount", sig.parameters)
        self.assertNotIn("account_type", sig.parameters)

    async def test_admin_balance_preset_rolls_back_on_invariant_error(self):
        """When create_admin_adjustment raises AccountLedgerInvariantError, session must rollback so op_key is not committed."""
        from aiogram.types import CallbackQuery, User as TgUser
        from bot.handlers.admin.users.balance_routes import admin_balance_preset
        from database.repositories.account_ledger_repo import AccountLedgerInvariantError

        real_callback = CallbackQuery(
            id="query_bal_124",
            from_user=TgUser(id=123456789, is_bot=False, first_name="Admin"),
            chat_instance="chat_inst_bal_2",
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
            patch(
                "bot.handlers.admin.users.balance_routes.create_admin_adjustment",
                side_effect=AccountLedgerInvariantError("Insufficient funds"),
            ),
        ):
            await admin_balance_preset(real_callback, session)
            session.rollback.assert_awaited_once()
            real_callback.answer.assert_awaited_once()
            self.assertIn("Insufficient funds", real_callback.answer.call_args[0][0])

    async def test_admin_balance_preset_rejects_unlisted_amount(self):
        """Preset amounts not in (100, 300, 500, 1000) must be rejected with ERROR_INVALID_REQUEST."""
        from aiogram.types import CallbackQuery, User as TgUser
        from bot import texts
        from bot.handlers.admin.users.balance_routes import admin_balance_preset

        for invalid_amount in (-1000, 0, 50, 250, 99999):
            callback = CallbackQuery(
                id=f"query_bal_inv_{invalid_amount}",
                from_user=TgUser(id=123456789, is_bot=False, first_name="Admin"),
                chat_instance="chat_inst_bal_inv",
                data=f"admin_bal_preset:888:{invalid_amount}",
            )
            object.__setattr__(callback, "answer", AsyncMock())
            session = AsyncMock()

            with patch("bot.handlers.admin.users.balance_routes.is_admin", return_value=True):
                await admin_balance_preset(callback, session)
                callback.answer.assert_awaited_once_with(texts.ERROR_INVALID_REQUEST, show_alert=True)

    async def test_admin_balance_confirm_deduct_aborts_when_bonus_depleted(self):
        """If bonus was spent during FSM step, confirm must abort and not debit real money."""
        from aiogram.types import CallbackQuery, User as TgUser
        from bot import texts
        from bot.handlers.admin.users.balance_routes import apply_user_balance_change
        from database.repositories.account_ledger_repo import AccountBalanceSnapshot

        callback = CallbackQuery(
            id="query_bal_conf_1",
            from_user=TgUser(id=123456789, is_bot=False, first_name="Admin"),
            chat_instance="chat_inst_bal_conf",
            data="confirm_admin_balance_apply",
        )
        object.__setattr__(callback, "answer", AsyncMock())

        state = AsyncMock()
        state.get_data.return_value = {
            "target_telegram_id": 888,
            "amount": 100,
            "action_type": "deduct",
            "adjustment_id": "test_adj_1",
        }

        user = MagicMock()
        user.id = 7
        user.telegram_id = 888

        session = AsyncMock()

        stale_bonus_snapshot = AccountBalanceSnapshot(
            accounting_position=Decimal(500),
            available=Decimal(500),
            reserved=Decimal(0),
            debt=Decimal(0),
            bonus_available=Decimal(0),  # User spent all bonus!
            real_available=Decimal(500),
        )

        with (
            patch("bot.handlers.admin.users.balance_routes.is_admin", return_value=True),
            patch("bot.handlers.admin.users.balance_routes.get_user_by_telegram_id", return_value=user),
            patch("bot.handlers.admin.users.balance_routes.get_account_balance", return_value=stale_bonus_snapshot),
            patch("bot.handlers.admin.users.balance_routes.create_admin_adjustment") as mock_adj,
        ):
            await apply_user_balance_change(callback, state, session)
            session.rollback.assert_awaited_once()
            mock_adj.assert_not_called()
            callback.answer.assert_awaited_once()
            self.assertIn("Недостаточно бонусных средств", callback.answer.call_args[0][0])
            state.clear.assert_called_once()


if __name__ == "__main__":
    unittest.main()

