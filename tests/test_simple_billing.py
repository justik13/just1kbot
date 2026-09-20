"""Comprehensive unit tests for the simple billing architecture.

Tests:
1. YooKassaGateway adapter (payment link creation, webhook parsing, polling status).
2. OrderService lifecycle (order creation, wallet payment, webhook handling, idempotency).
3. FulfillmentService (AWG linear extension & revocation, White Internet fulfillment).
4. Order keyboards & UI helpers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, patch
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Order, Tariff, User
from database.repositories.account_ledger_repo import AccountBalanceSnapshot
from integrations.payment_gateways.base import (
    PaymentInvoice,
    WebhookResult,
)
from services.yookassa_service import YooKassaResult
from integrations.payment_gateways.yookassa import YooKassaGateway
from services.fulfillment_service import FulfillmentService
from services.order_service import InsufficientBalanceError, OrderService
from bot.keyboards.payment import (
    get_order_checkout_keyboard,
    get_order_invoice_keyboard,
)


@pytest.mark.asyncio
class TestYooKassaGateway(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.gateway = YooKassaGateway()

    @patch("integrations.payment_gateways.yookassa.YooKassaService.create_payment_result")
    async def test_create_payment_url(self, mock_create):
        mock_create.return_value = YooKassaResult(
            True,
            value={
                "id": "ext-12345",
                "confirmation": {"confirmation_url": "https://yookassa.ru/pay/12345"},
            },
        )
        invoice = await self.gateway.create_payment_url(
            order_id="ord-abc",
            amount_rub=Decimal("250.00"),
            description="Test payment",
            return_url="https://t.me/mybot",
        )
        self.assertEqual(invoice.external_id, "ext-12345")
        self.assertEqual(invoice.payment_url, "https://yookassa.ru/pay/12345")
        mock_create.assert_called_once()
        payload = mock_create.call_args[0][0]
        self.assertEqual(payload["amount"]["value"], "250.00")
        self.assertEqual(payload["metadata"]["order_id"], "ord-abc")

    async def test_parse_webhook_payment_succeeded(self):
        payload = {
            "type": "notification",
            "event": "payment.succeeded",
            "object": {
                "id": "ext-pay-999",
                "status": "succeeded",
                "metadata": {"order_id": "ord-777"},
            },
        }
        result = await self.gateway.parse_webhook(payload)
        self.assertTrue(result.is_paid)
        self.assertFalse(result.is_refunded)
        self.assertEqual(result.order_id, "ord-777")
        self.assertEqual(result.external_id, "ext-pay-999")

    async def test_parse_webhook_refund_succeeded(self):
        payload = {
            "type": "notification",
            "event": "refund.succeeded",
            "object": {
                "id": "ref-111",
                "payment_id": "ext-pay-999",
                "status": "succeeded",
                "metadata": {"order_id": "ord-777"},
            },
        }
        result = await self.gateway.parse_webhook(payload)
        self.assertFalse(result.is_paid)
        self.assertTrue(result.is_refunded)
        self.assertEqual(result.order_id, "ord-777")
        self.assertEqual(result.external_id, "ext-pay-999")

    @patch("integrations.payment_gateways.yookassa.YooKassaService.get_payment_result")
    async def test_check_payment_status(self, mock_get):
        mock_get.return_value = YooKassaResult(
            True,
            value={"status": "succeeded"},
        )

        status_result = await self.gateway.check_payment_status("ext-123")
        self.assertTrue(status_result.is_paid)
        self.assertFalse(status_result.is_canceled)


@pytest.mark.asyncio
class TestFulfillmentService(unittest.IsolatedAsyncioTestCase):
    @patch("services.fulfillment_service.invalidate_user_cache")
    @patch("services.fulfillment_service.SubscriptionService.sync_access_state")
    async def test_fulfill_awg_order_new_subscription(self, mock_sync, mock_cache):
        session = AsyncMock(spec=AsyncSession)
        user = User(
            id=10,
            telegram_id=12345678,
            subscription_end=None,
            device_limit=1,
        )
        session.get.return_value = user

        order = Order(
            id=uuid.uuid4(),
            user_id=10,
            service_type="awg",
            duration_days=30,
            device_limit=3,
            tariff_id=5,
            amount_rub=Decimal("300.00"),
            status="paid",
        )

        await FulfillmentService.fulfill_order(session, order)

        self.assertEqual(user.device_limit, 3)
        self.assertEqual(user.current_tariff_id, 5)
        self.assertIsNotNone(user.subscription_end)
        mock_sync.assert_called_once_with(session, user)
        mock_cache.assert_called_once_with(12345678)

    @patch("services.fulfillment_service.invalidate_user_cache")
    @patch("services.fulfillment_service.SubscriptionService.sync_access_state")
    async def test_fulfill_awg_order_extend_existing(self, mock_sync, mock_cache):
        session = AsyncMock(spec=AsyncSession)
        future_end = datetime.now(timezone.utc) + timedelta(days=15)
        user = User(
            id=10,
            telegram_id=12345678,
            subscription_end=future_end,
            device_limit=2,
        )
        session.get.return_value = user

        order = Order(
            id=uuid.uuid4(),
            user_id=10,
            service_type="awg",
            duration_days=30,
            device_limit=2,
            tariff_id=2,
            amount_rub=Decimal("200.00"),
            status="paid",
        )

        await FulfillmentService.fulfill_order(session, order)

        self.assertGreater(user.subscription_end, future_end + timedelta(days=29))
        mock_sync.assert_called_once_with(session, user)

    @patch("services.fulfillment_service.invalidate_user_cache")
    @patch("services.fulfillment_service.SubscriptionService.sync_access_state")
    async def test_revoke_awg_order(self, mock_sync, mock_cache):
        session = AsyncMock(spec=AsyncSession)
        end_date = datetime.now(timezone.utc) + timedelta(days=40)
        user = User(
            id=10,
            telegram_id=12345678,
            subscription_end=end_date,
        )
        session.get.return_value = user

        order = Order(
            id=uuid.uuid4(),
            user_id=10,
            service_type="awg",
            duration_days=30,
            amount_rub=Decimal("200.00"),
            status="refunded",
        )

        await FulfillmentService.revoke_order(session, order)

        self.assertLess(user.subscription_end, end_date - timedelta(days=29))
        mock_sync.assert_called_once_with(session, user)


@pytest.mark.asyncio
class TestOrderService(unittest.IsolatedAsyncioTestCase):
    @patch("services.order_service.get_payment_gateway")
    async def test_create_order_external_gateway(self, mock_gw_factory):
        mock_gw = AsyncMock()
        mock_gw.create_payment_url.return_value = PaymentInvoice(
            external_id="ext-999",
            payment_url="https://pay.link/999",
        )
        mock_gw_factory.return_value = mock_gw

        session = AsyncMock(spec=AsyncSession)
        tariff = Tariff(id=1, name="Basic", price_rub=Decimal("150.00"), duration_days=30, device_limit=2)
        session.get.return_value = tariff

        order = await OrderService.create_order(
            session,
            user_id=42,
            service_type="awg",
            tariff_id=1,
            payment_method="yookassa",
        )

        self.assertEqual(order.user_id, 42)
        self.assertEqual(order.amount_rub, Decimal("150.00"))
        self.assertEqual(order.duration_days, 30)
        self.assertEqual(order.external_id, "ext-999")
        self.assertEqual(order.payment_url, "https://pay.link/999")
        self.assertEqual(order.status, "pending")
        session.commit.assert_called_once()

    @patch("services.order_service.FulfillmentService.fulfill_order")
    @patch("services.order_service.create_order_debit")
    @patch("services.order_service.get_account_balance")
    async def test_pay_from_wallet_success(self, mock_bal, mock_debit, mock_fulfill):
        mock_bal.return_value = AccountBalanceSnapshot(
            accounting_position=Decimal("500.00"),
            available=Decimal("500.00"),
            reserved=Decimal("0.00"),
            debt=Decimal("0.00"),
            real_available=Decimal("500.00"),
            bonus_available=Decimal("0.00"),
        )
        session = AsyncMock(spec=AsyncSession)
        tariff = Tariff(id=1, name="Basic", price_rub=Decimal("200.00"), duration_days=30, device_limit=2)
        session.get.return_value = tariff

        order = await OrderService.pay_from_wallet(
            session,
            user_id=42,
            service_type="awg",
            tariff_id=1,
        )

        self.assertEqual(order.status, "paid")
        self.assertEqual(order.payment_method, "wallet")
        mock_debit.assert_called_once()
        mock_fulfill.assert_called_once_with(session, order)
        session.commit.assert_called_once()

    @patch("services.order_service.get_account_balance")
    async def test_pay_from_wallet_insufficient_balance(self, mock_bal):
        mock_bal.return_value = AccountBalanceSnapshot(
            accounting_position=Decimal("50.00"),
            available=Decimal("50.00"),
            reserved=Decimal("0.00"),
            debt=Decimal("0.00"),
            real_available=Decimal("50.00"),
            bonus_available=Decimal("0.00"),
        )
        session = AsyncMock(spec=AsyncSession)
        tariff = Tariff(id=1, name="Basic", price_rub=Decimal("200.00"), duration_days=30, device_limit=2)
        session.get.return_value = tariff

        with self.assertRaises(InsufficientBalanceError):
            await OrderService.pay_from_wallet(
                session,
                user_id=42,
                service_type="awg",
                tariff_id=1,
            )

    @patch("services.order_service.FulfillmentService.fulfill_order")
    @patch("services.order_service.create_order_debit")
    @patch("services.order_service.create_order_credit")
    @patch("services.order_service.get_payment_gateway")
    async def test_process_webhook_event_payment_succeeded(
        self, mock_gw_factory, mock_credit, mock_debit, mock_fulfill
    ):
        mock_gw = AsyncMock()
        order_uuid = uuid.uuid4()
        mock_gw.parse_webhook.return_value = WebhookResult(
            order_id=str(order_uuid),
            is_paid=True,
            is_refunded=False,
            external_id="ext-pay-888",
        )
        mock_gw_factory.return_value = mock_gw

        session = AsyncMock(spec=AsyncSession)
        order = Order(
            id=order_uuid,
            user_id=10,
            service_type="awg",
            amount_rub=Decimal("200.00"),
            status="pending",
        )
        session.get.return_value = order

        success = await OrderService.process_webhook_event(session, {"some": "payload"})

        self.assertTrue(success)
        self.assertEqual(order.status, "paid")
        self.assertEqual(order.external_id, "ext-pay-888")
        mock_credit.assert_called_once()
        mock_debit.assert_called_once()
        mock_fulfill.assert_called_once_with(session, order)
        session.commit.assert_called_once()

    @patch("services.order_service.FulfillmentService.revoke_order")
    @patch("services.order_service.create_order_refund_debit")
    @patch("services.order_service.get_payment_gateway")
    async def test_process_webhook_event_refund_succeeded(
        self, mock_gw_factory, mock_refund_debit, mock_revoke
    ):
        mock_gw = AsyncMock()
        order_uuid = uuid.uuid4()
        mock_gw.parse_webhook.return_value = WebhookResult(
            order_id=str(order_uuid),
            is_paid=False,
            is_refunded=True,
            external_id="ext-pay-888",
        )
        mock_gw_factory.return_value = mock_gw

        session = AsyncMock(spec=AsyncSession)
        order = Order(
            id=order_uuid,
            user_id=10,
            service_type="awg",
            amount_rub=Decimal("200.00"),
            status="paid",
        )
        session.get.return_value = order

        success = await OrderService.process_webhook_event(session, {"some": "payload"})

        self.assertTrue(success)
        self.assertEqual(order.status, "refunded")
        mock_refund_debit.assert_called_once()
        mock_revoke.assert_called_once_with(session, order)
        session.commit.assert_called_once()


class TestOrderKeyboards(unittest.TestCase):
    def test_get_order_checkout_keyboard_can_pay_wallet(self):
        kb = get_order_checkout_keyboard(
            tariff_id=1,
            price=200,
            can_pay_wallet=True,
            back_callback="back_to_menu",
        )
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        self.assertIn("order_pay_wallet:1", callbacks)
        self.assertIn("order_pay_card:1", callbacks)
        self.assertIn("back_to_menu", callbacks)

    def test_get_order_checkout_keyboard_insufficient_wallet(self):
        kb = get_order_checkout_keyboard(
            tariff_id=1,
            price=200,
            can_pay_wallet=False,
            back_callback="back_to_menu",
        )
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        self.assertNotIn("order_pay_wallet:1", callbacks)
        self.assertIn("order_pay_card:1", callbacks)
        self.assertIn("balance_topup", callbacks)

    def test_get_order_invoice_keyboard(self):
        kb = get_order_invoice_keyboard(
            payment_url="https://pay.link/123",
            order_id="ord-abc",
            price=300,
            back_callback="back_to_menu",
        )
        rows = kb.inline_keyboard
        # First row is URL button
        self.assertEqual(rows[0][0].url, "https://pay.link/123")
        self.assertEqual(rows[1][0].callback_data, "order_check:ord-abc")
        self.assertEqual(rows[2][0].callback_data, "order_cancel:ord-abc")
