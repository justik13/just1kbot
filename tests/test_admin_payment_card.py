import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.types import User as TelegramUser

from bot.handlers.admin.payments import show_payment_card
from database.models import Payment, User


class AdminPaymentCardTests(unittest.IsolatedAsyncioTestCase):
    @patch("bot.handlers.admin.payments.is_admin", return_value=True)
    @patch("bot.handlers.admin.payments.get_payment_by_id", new_callable=AsyncMock)
    @patch("bot.handlers.admin.payments.get_payment_refundable_amount", new_callable=AsyncMock)
    async def test_show_payment_card_success(
        self,
        mock_get_refundable,
        mock_get_payment_by_id,
        mock_is_admin,
    ):
        mock_get_refundable.return_value = Decimal(0)

        dummy_user = User(
            id=1,
            telegram_id=872658825,
            username="testuser",
        )
        dummy_payment = Payment(
            id=42,
            user_id=1,
            amount=Decimal("100.00"),
            currency="RUB",
            public_order_id="pay_123",
            provider_idempotency_key="key_123",
            provider_status="succeeded",
            fulfillment_status="succeeded",
            reconciliation_status="ok",
            created_at=datetime.now(timezone.utc),
            paid_at=datetime.now(timezone.utc),
            external_id="ext_123",
        )
        dummy_payment.user = dummy_user

        mock_get_payment_by_id.return_value = dummy_payment

        callback = AsyncMock(spec=CallbackQuery)
        callback.data = "admin_payment_card:42"
        callback.from_user = TelegramUser(id=872658825, is_bot=False, first_name="Admin")
        callback.message = MagicMock(spec=Message)
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()

        state = AsyncMock(spec=FSMContext)
        session = AsyncMock()

        await show_payment_card(callback, state, session)

        mock_get_payment_by_id.assert_awaited_once_with(session, 42)
        callback.message.edit_text.assert_awaited_once()
        callback.answer.assert_awaited_once_with(show_alert=False)
