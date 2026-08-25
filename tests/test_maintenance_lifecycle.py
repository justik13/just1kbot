import unittest
from decimal import Decimal
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
from bot.handlers.payment.purchase_routes import confirm_purchase, review_purchase
from bot.handlers.payment.tariff_change_routes import (
    confirm_tariff_change,
    review_tariff_change,
)
from database.models import MaintenanceMode, Payment, User
from services.account_topup import settle_succeeded_topup
from services.maintenance_service import MaintenanceService
from utils.datetime_helpers import now_utc


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

    async def test_purchase_review_and_confirm_blocked_during_maintenance(self):
        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = MagicMock(id=88888)
        callback.data = "balance_purchase_review:11111111-1111-1111-1111-111111111111"
        callback.bot = MagicMock()
        callback.message = MagicMock(chat=MagicMock(id=88888), message_id=104)
        callback.answer = AsyncMock()

        state = MagicMock(spec=FSMContext)
        state.clear = AsyncMock()
        session = AsyncMock()
        db_user = User(id=1, telegram_id=88888, username="test", first_name="Test")

        with patch("bot.handlers.payment.purchase_routes.MaintenanceService.can_user_perform_action", new=AsyncMock(return_value=False)), \
             patch("bot.handlers.payment.purchase_routes._render_maintenance", new_callable=AsyncMock) as mock_render_maint:

            await review_purchase(callback, session, db_user=db_user)
            mock_render_maint.assert_called_once_with(callback, session, back_to="payment_showcase")

            mock_render_maint.reset_mock()
            callback.data = "balance_purchase_confirm:11111111-1111-1111-1111-111111111111"
            await confirm_purchase(callback, state, session, db_user=db_user)
            mock_render_maint.assert_called_once_with(callback, session, back_to="payment_showcase")

    async def test_tariff_change_review_and_confirm_blocked_during_maintenance(self):
        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = MagicMock(id=88888)
        callback.data = "balance_change_review:22222222-2222-2222-2222-222222222222"
        callback.bot = MagicMock()
        callback.message = MagicMock(chat=MagicMock(id=88888), message_id=105)
        callback.answer = AsyncMock()

        state = MagicMock(spec=FSMContext)
        state.clear = AsyncMock()
        session = AsyncMock()
        db_user = User(id=1, telegram_id=88888, username="test", first_name="Test")

        with patch("bot.handlers.payment.tariff_change_routes.MaintenanceService.can_user_perform_action", new=AsyncMock(return_value=False)), \
             patch("bot.handlers.payment.tariff_change_routes._render_maintenance", new_callable=AsyncMock) as mock_render_maint:

            await review_tariff_change(callback, session, db_user=db_user)
            mock_render_maint.assert_called_once_with(callback, session, back_to="payment_change_tariff")

            mock_render_maint.reset_mock()
            callback.data = "balance_change_confirm:22222222-2222-2222-2222-222222222222"
            await confirm_tariff_change(callback, state, session, db_user=db_user)
            mock_render_maint.assert_called_once_with(callback, session, back_to="payment_change_tariff")

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

    async def test_background_topup_settlement_succeeds_even_during_maintenance(self):
        from database.repositories.account_ledger_repo import AccountBalanceSnapshot
        session = AsyncMock()
        session.add = MagicMock()
        payment = Payment(
            id=777,
            user_id=1,
            amount=Decimal(300),
            currency="RUB",
            provider_status="succeeded",
            provider_confirmed_at=now_utc(),
            fulfillment_status="pending",
            credited_at=None,
            topup_context={},
        )
        user = User(id=1, telegram_id=88888, username="test", first_name="Test")
        bot = MagicMock()
        mock_settings = MagicMock(BALANCE_MAX_AVAILABLE_RUB="100000")

        with patch("services.account_topup.lock_checkout_user", AsyncMock(return_value=user)), \
             patch("services.account_topup.get_account_balance", AsyncMock(return_value=AccountBalanceSnapshot(
                 accounting_position=Decimal(300),
                 available=Decimal(300),
                 reserved=Decimal(0),
                 debt=Decimal(0),
                 real_position=Decimal(300),
                 bonus_position=Decimal(0),
                 real_available=Decimal(300),
                 bonus_available=Decimal(0),
             ))), \
             patch("services.account_topup.credit_succeeded_topup", AsyncMock(return_value=(MagicMock(), True))), \
             patch("services.account_topup.refresh_user_dispute_hold", AsyncMock()), \
             patch("services.audit_service.AuditService.log_action", AsyncMock()), \
             patch("database.connection.queue_post_commit_task", MagicMock()), \
             patch("services.maintenance_service.is_maintenance_enabled", AsyncMock(return_value=True)):

            # Background settlement must never be blocked by maintenance
            created, balance = await settle_succeeded_topup(
                session, payment=payment, source="yookassa_webhook", settings=mock_settings, bot=bot
            )
            self.assertTrue(created)
            self.assertEqual(balance.available, Decimal(300))
