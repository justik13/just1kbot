import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.handlers.connection.device_create_routes import start_add_device
from bot.handlers.payment.balance_routes import (
    accept_custom_amount,
    choose_topup_amount,
    create_preset_topup,
    request_custom_amount,
)
from bot.handlers.payment.purchase_routes import handle_order_pay_wallet
from database.models import User
from services.maintenance_service import MaintenanceService


class TestMaintenanceServiceLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_admin_bypasses_maintenance(self):
        session = AsyncMock()
        admin_id = 123456
        regular_user_id = 999999

        with patch("services.maintenance_service.is_admin", side_effect=lambda uid: uid == admin_id), \
             patch("services.maintenance_service.is_maintenance_enabled", new=AsyncMock(return_value=True)):

            self.assertTrue(
                await MaintenanceService.can_user_perform_action(session, admin_id),
                "Admins must always bypass maintenance mode"
            )
            self.assertFalse(
                await MaintenanceService.can_user_perform_action(session, regular_user_id),
                "Regular users must be blocked during maintenance mode"
            )

    async def test_regular_user_allowed_when_maintenance_off(self):
        session = AsyncMock()
        regular_user_id = 999999

        with patch("services.maintenance_service.is_admin", return_value=False), \
             patch("services.maintenance_service.is_maintenance_enabled", new=AsyncMock(return_value=False)):

            self.assertTrue(
                await MaintenanceService.can_user_perform_action(session, regular_user_id)
            )

    async def test_balance_topup_blocked_during_maintenance(self):
        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = MagicMock(id=88888)
        callback.bot = MagicMock()
        callback.message = MagicMock(chat=MagicMock(id=88888), message_id=101)
        callback.answer = AsyncMock()

        state = MagicMock(spec=FSMContext)
        state.clear = AsyncMock()
        session = AsyncMock()
        db_user = User(id=1, telegram_id=88888, username="test", first_name="Test")

        with patch("bot.handlers.payment.balance_routes.MaintenanceService.can_user_perform_action", new=AsyncMock(return_value=False)), \
             patch("bot.handlers.payment.balance_routes._render_maintenance", new_callable=AsyncMock) as mock_render_maint:

            await choose_topup_amount(callback, state, session, db_user=db_user)

            mock_render_maint.assert_called_once_with(callback, session, back_to="menu_balance")

    async def test_preset_topup_creation_blocked_during_maintenance(self):
        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = MagicMock(id=88888)
        callback.data = "balance_create:250"
        callback.bot = MagicMock()
        callback.message = MagicMock(chat=MagicMock(id=88888), message_id=102)
        callback.answer = AsyncMock()

        session = AsyncMock()
        db_user = User(id=1, telegram_id=88888, username="test", first_name="Test")

        with patch("bot.handlers.payment.balance_routes.MaintenanceService.can_user_perform_action", new=AsyncMock(return_value=False)), \
             patch("bot.handlers.payment.balance_routes._render_maintenance", new_callable=AsyncMock) as mock_render_maint:

            await create_preset_topup(callback, session, db_user=db_user)

            mock_render_maint.assert_called_once_with(callback, session, back_to="menu_balance")

    async def test_custom_amount_prompt_blocked_during_maintenance(self):
        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = MagicMock(id=88888)
        callback.bot = MagicMock()
        callback.message = MagicMock(chat=MagicMock(id=88888), message_id=103)
        callback.answer = AsyncMock()

        state = MagicMock(spec=FSMContext)
        session = AsyncMock()

        with patch("bot.handlers.payment.balance_routes.MaintenanceService.can_user_perform_action", new=AsyncMock(return_value=False)), \
             patch("bot.handlers.payment.balance_routes._render_maintenance", new_callable=AsyncMock) as mock_render_maint:

            await request_custom_amount(callback, state, session)

            mock_render_maint.assert_called_once_with(callback, session, back_to="menu_balance")
            self.assertFalse(state.set_state.called)

    async def test_accept_custom_amount_clears_state_and_blocks_during_maintenance(self):
        message = MagicMock(spec=Message)
        message.from_user = MagicMock(id=88888)
        message.chat = MagicMock(id=88888)
        message.text = "500"
        message.delete = AsyncMock()

        state = MagicMock(spec=FSMContext)
        state.clear = AsyncMock()
        session = AsyncMock()
        db_user = User(id=1, telegram_id=88888, username="test", first_name="Test")

        with patch("bot.handlers.payment.balance_routes.MaintenanceService.can_user_perform_action", new=AsyncMock(return_value=False)), \
             patch("bot.handlers.payment.balance_routes._render_maintenance", new_callable=AsyncMock) as mock_render_maint:

            await accept_custom_amount(message, state, session, db_user=db_user)

            state.clear.assert_called_once()
            mock_render_maint.assert_called_once_with(message, session, back_to="menu_balance")

    async def test_order_pay_wallet_blocked_during_maintenance(self):
        import uuid
        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = MagicMock(id=88888)
        callback.data = f"order_pay_wallet:{uuid.uuid4()}"
        callback.bot = MagicMock()
        callback.message = MagicMock(chat=MagicMock(id=88888), message_id=104)
        callback.answer = AsyncMock()

        session = AsyncMock()
        db_user = User(id=1, telegram_id=88888, username="test", first_name="Test")

        with patch("bot.handlers.payment.purchase_routes.MaintenanceService.can_user_perform_action", new=AsyncMock(return_value=False)), \
             patch("bot.handlers.payment.purchase_routes._render_maintenance", new_callable=AsyncMock) as mock_render_maint:

            await handle_order_pay_wallet(callback, session, db_user=db_user)
            mock_render_maint.assert_called_once_with(callback, session, back_to="payment_showcase")


    async def test_device_creation_blocked_during_maintenance(self):
        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = MagicMock(id=88888)
        callback.data = "create_device"
        callback.bot = MagicMock()
        callback.message = MagicMock(chat=MagicMock(id=88888), message_id=106)
        callback.answer = AsyncMock()

        state = MagicMock(spec=FSMContext)
        session = AsyncMock()
        db_user = User(id=1, telegram_id=88888, username="test", first_name="Test")

        with patch("bot.handlers.connection.device_create_routes.MaintenanceService.can_user_perform_action", new=AsyncMock(return_value=False)), \
             patch("bot.handlers.connection.device_create_routes._render_maintenance", new_callable=AsyncMock) as mock_render_maint:

            await start_add_device(callback, state, session, db_user=db_user)
            mock_render_maint.assert_called_once_with(callback.message, session, back_to="back_to_connections")
