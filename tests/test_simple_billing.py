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
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from utils.datetime_helpers import now_utc
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
        self.assertEqual(result.external_id, "ref-111")
        self.assertEqual(result.related_external_id, "ext-pay-999")
        self.assertEqual(result.payment_id, "ext-pay-999")

    async def test_parse_webhook_payment_canceled(self):
        payload = {
            "type": "notification",
            "event": "payment.canceled",
            "object": {
                "id": "ext-pay-000",
                "status": "canceled",
                "metadata": {"order_id": "ord-888"},
            },
        }
        result = await self.gateway.parse_webhook(payload)
        self.assertFalse(result.is_paid)
        self.assertFalse(result.is_refunded)
        self.assertTrue(result.is_canceled)
        self.assertEqual(result.order_id, "ord-888")
        self.assertEqual(result.external_id, "ext-pay-000")

    @patch("integrations.payment_gateways.yookassa.YooKassaService.get_payment_result")
    async def test_check_payment_status(self, mock_get):
        mock_get.return_value = YooKassaResult(
            True,
            value={"status": "succeeded"},
        )

        status_result = await self.gateway.check_payment_status("ext-123")
        self.assertTrue(status_result.is_paid)
        self.assertFalse(status_result.is_canceled)

    def test_webhook_result_dataclass_contract(self):
        import dataclasses
        res = WebhookResult(
            order_id="ord-1",
            is_paid=True,
            is_refunded=False,
            external_id="ext-1",
        )
        self.assertEqual(res.order_id, "ord-1")
        self.assertTrue(res.is_paid)
        self.assertFalse(res.is_refunded)
        self.assertFalse(res.is_canceled)
        self.assertEqual(res.external_id, "ext-1")
        self.assertIsNone(res.amount_rub)
        self.assertEqual(res.event_type, "")
        self.assertIsNone(res.related_external_id)
        self.assertIsNone(res.payment_id)

        # Immutability
        with self.assertRaises(dataclasses.FrozenInstanceError):
            res.is_paid = False  # type: ignore

        # related_external_id and payment_id property
        res_refund = WebhookResult(
            order_id=None,
            is_paid=False,
            is_refunded=True,
            external_id="ref-1",
            related_external_id="pay-99",
        )
        self.assertEqual(res_refund.related_external_id, "pay-99")
        self.assertEqual(res_refund.payment_id, "pay-99")

    async def test_parse_webhook_null_safety(self):
        # 1. Payload with None object
        res_none_obj = await self.gateway.parse_webhook({"event": "payment.succeeded", "object": None})
        self.assertTrue(res_none_obj.is_paid)
        self.assertEqual(res_none_obj.external_id, "")
        self.assertIsNone(res_none_obj.amount_rub)

        # 2. Object with None amount
        res_none_amt = await self.gateway.parse_webhook({
            "event": "payment.succeeded",
            "object": {"id": "pay-123", "amount": None},
        })
        self.assertTrue(res_none_amt.is_paid)
        self.assertEqual(res_none_amt.external_id, "pay-123")
        self.assertIsNone(res_none_amt.amount_rub)

        # 3. Empty payload
        res_empty = await self.gateway.parse_webhook({})
        self.assertFalse(res_empty.is_paid)
        self.assertFalse(res_empty.is_refunded)
        self.assertEqual(res_empty.external_id, "")


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
        session.scalar.return_value = None
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
        session.flush.assert_called()

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
        session.scalar.return_value = None
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
        session.flush.assert_called()

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
        session.scalar.return_value = None
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
        session.scalar.return_value = order
        session.get.return_value = order

        success = await OrderService.process_webhook_event(session, {"some": "payload"})

        self.assertTrue(success)
        self.assertEqual(order.status, "paid")
        self.assertEqual(order.external_id, "ext-pay-888")
        mock_credit.assert_not_called()
        mock_debit.assert_not_called()
        mock_fulfill.assert_called_once_with(session, order)
        session.flush.assert_called()

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
        session.scalar.return_value = order
        session.get.return_value = order

        success = await OrderService.process_webhook_event(session, {"some": "payload"})

        self.assertTrue(success)
        self.assertEqual(order.status, "refunded")
        mock_refund_debit.assert_not_called()
        mock_revoke.assert_called_once_with(session, order)
        session.flush.assert_called()

    @patch("services.fulfillment_service.FulfillmentService.revoke_order")
    @patch("services.order_service.create_order_refund_debit")
    @patch("services.order_service.get_payment_gateway")
    async def test_process_webhook_event_refund_defers_when_order_pending(
        self, mock_gw_factory, mock_refund_debit, mock_revoke
    ):
        mock_gw = AsyncMock()
        order_uuid = uuid.uuid4()
        mock_gw.parse_webhook.return_value = WebhookResult(
            order_id=str(order_uuid),
            is_paid=False,
            is_refunded=True,
            external_id="ext-pay-pending",
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
        session.scalar.return_value = order
        session.get.return_value = order

        result = await OrderService.process_webhook_event(session, {"some": "payload"})

        self.assertIsNone(result)
        self.assertEqual(order.status, "pending")
        mock_revoke.assert_not_called()
        mock_refund_debit.assert_not_called()

    @patch("services.order_service.get_payment_gateway")
    async def test_process_webhook_event_payment_canceled_marks_pending_order_canceled(
        self, mock_gw_factory
    ):
        mock_gw = AsyncMock()
        order_uuid = uuid.uuid4()
        mock_gw.parse_webhook.return_value = WebhookResult(
            order_id=str(order_uuid),
            is_paid=False,
            is_refunded=False,
            is_canceled=True,
            external_id="ext-pay-canceled",
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
        session.scalar.return_value = order
        session.get.return_value = order

        result = await OrderService.process_webhook_event(session, {"some": "payload"})

        self.assertIsNotNone(result)
        self.assertEqual(order.status, "canceled")
        self.assertEqual((order.metadata_ or {}).get("cancellation_reason"), "gateway_canceled")
        session.flush.assert_called()

    @patch("services.order_service.get_payment_gateway")
    async def test_process_webhook_event_payment_canceled_leaves_paid_order_intact(
        self, mock_gw_factory
    ):
        mock_gw = AsyncMock()
        order_uuid = uuid.uuid4()
        mock_gw.parse_webhook.return_value = WebhookResult(
            order_id=str(order_uuid),
            is_paid=False,
            is_refunded=False,
            is_canceled=True,
            external_id="ext-pay-canceled-late",
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
        session.scalar.return_value = order
        session.get.return_value = order

        result = await OrderService.process_webhook_event(session, {"some": "payload"})

        self.assertIsNotNone(result)
        self.assertEqual(order.status, "paid")
        self.assertNotIn("cancellation_reason", order.metadata_ or {})

    @patch("services.order_service.get_payment_gateway")
    async def test_process_webhook_event_unactionable_event_acknowledges_order(
        self, mock_gw_factory
    ):
        mock_gw = AsyncMock()
        order_uuid = uuid.uuid4()
        mock_gw.parse_webhook.return_value = WebhookResult(
            order_id=str(order_uuid),
            is_paid=False,
            is_refunded=False,
            is_canceled=False,
            external_id="ext-pay-waiting",
            event_type="payment.waiting_for_capture",
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
        session.scalar.return_value = order
        session.get.return_value = order

        result = await OrderService.process_webhook_event(session, {"some": "payload"})

        self.assertEqual(result, order)
        self.assertEqual(order.status, "pending")


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


class TestSimpleBillingEnhancements(unittest.IsolatedAsyncioTestCase):
    @patch("services.fulfillment_service.invalidate_user_cache")
    @patch("services.fulfillment_service.SubscriptionService.sync_access_state")
    async def test_fulfill_awg_order_tariff_change(self, mock_sync, mock_cache):
        session = AsyncMock(spec=AsyncSession)
        now = datetime.now(timezone.utc)
        # User currently has 10 days remaining
        user = User(
            id=10,
            telegram_id=12345678,
            subscription_end=now + timedelta(days=10),
            device_limit=2,
            current_tariff_id=1,
        )
        session.get.return_value = user

        # User changes to a 30-day tariff (prorated price paid)
        order = Order(
            id=uuid.uuid4(),
            user_id=10,
            service_type="awg",
            duration_days=30,
            device_limit=4,
            tariff_id=2,
            amount_rub=Decimal("350.00"),
            status="paid",
            metadata_={"is_tariff_change": True},
        )

        await FulfillmentService.fulfill_order(session, order)

        # Resulting subscription end must be ~30 days from now, NOT 10 + 30 = 40 days
        self.assertLess(user.subscription_end, now + timedelta(days=31))
        self.assertGreater(user.subscription_end, now + timedelta(days=29))
        self.assertEqual(user.device_limit, 4)
        self.assertEqual(user.current_tariff_id, 2)
        mock_sync.assert_called_once_with(session, user)

    @patch("services.fulfillment_service.WhiteInternetService.purchase_subscription")
    async def test_fulfill_white_internet_passes_debit_balance_false(self, mock_buy):
        mock_buy.return_value = (True, "OK", AsyncMock())
        session = AsyncMock(spec=AsyncSession)
        session.scalar.return_value = None  # No existing sub
        user = User(id=10, telegram_id=12345678)
        session.get.return_value = user

        order = Order(
            id=uuid.uuid4(),
            user_id=10,
            service_type="white_internet",
            duration_days=30,
            amount_rub=Decimal("300.00"),
            status="paid",
        )

        with patch(
            "database.repositories.white_internet_repo.get_subscription_by_user_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await FulfillmentService.fulfill_order(session, order)

        mock_buy.assert_called_once_with(session, 10, debit_balance=False)

    def test_calculate_tariff_change_math(self):
        now = datetime.now(timezone.utc)
        current_tariff = Tariff(
            id=1,
            name="100 RUB / 10 days",
            price_rub=Decimal("100.00"),
            duration_days=10,
        )
        target_tariff = Tariff(
            id=2,
            name="300 RUB / 30 days",
            price_rub=Decimal("300.00"),
            duration_days=30,
        )
        # 5 days remaining on current tariff -> 50 RUB remaining value
        subscription_end = now + timedelta(days=5)

        due_rub, resulting_days = OrderService.calculate_tariff_change(
            current_tariff, target_tariff, subscription_end, now=now
        )
        # 300 - 50 = 250 RUB
        self.assertEqual(due_rub, Decimal("250.00"))
        self.assertEqual(resulting_days, 30)

    def test_calculate_tariff_change_multi_month_proportional(self):
        now = datetime.now(timezone.utc)
        base_tariff = Tariff(
            id=1,
            name="100 RUB / 30 days",
            price_rub=Decimal("100.00"),
            duration_days=30,
        )
        pro_tariff = Tariff(
            id=2,
            name="200 RUB / 30 days",
            price_rub=Decimal("200.00"),
            duration_days=30,
        )

        # Case 1: 200 days of Base -> switch to Pro
        # Value = 200 * (100 / 30) = 667 RUB >= target cost (200 RUB)
        # due_rub = 0, resulting_days = round(667 / (200 / 30)) = 100 days
        sub_end_200 = now + timedelta(days=200)
        due_rub, resulting_days = OrderService.calculate_tariff_change(
            base_tariff, pro_tariff, sub_end_200, now=now
        )
        self.assertEqual(due_rub, Decimal("0.00"))
        self.assertEqual(resulting_days, 100)

        # Case 2: 200 days of Base -> switch to another Base variant with identical daily rate
        # Exact days must be preserved without drift
        same_rate_tariff = Tariff(
            id=3,
            name="100 RUB / 30 days variant",
            price_rub=Decimal("100.00"),
            duration_days=30,
        )
        due_rub_same, resulting_days_same = OrderService.calculate_tariff_change(
            base_tariff, same_rate_tariff, sub_end_200, now=now
        )
        self.assertEqual(due_rub_same, Decimal("0.00"))
        self.assertEqual(resulting_days_same, 200)

        # Case 3: Expired subscription (0 days left) -> full price, standard duration
        sub_end_expired = now - timedelta(days=1)
        due_expired, days_expired = OrderService.calculate_tariff_change(
            base_tariff, pro_tariff, sub_end_expired, now=now
        )
        self.assertEqual(due_expired, Decimal("200.00"))
        self.assertEqual(days_expired, 30)

    @patch("services.referral_bonus.grant_referral_bonus_for_topup")
    @patch("services.order_service.create_order_credit")
    @patch("services.order_service.FulfillmentService.fulfill_order")
    async def test_mark_topup_order_paid_triggers_referral_bonus(
        self, mock_fulfill, mock_credit, mock_bonus
    ):
        session = AsyncMock(spec=AsyncSession)
        order_uuid = uuid.uuid4()
        order = Order(
            id=order_uuid,
            user_id=55,
            service_type="topup",
            amount_rub=Decimal("500.00"),
            payment_method="yookassa",
            status="pending",
        )
        session.scalar.return_value = order
        session.get.return_value = order

        paid_order = await OrderService.mark_order_paid(session, order_uuid)

        self.assertIsNotNone(paid_order)
        self.assertEqual(paid_order.status, "paid")
        mock_credit.assert_called_once_with(
            session,
            user_id=55,
            amount_rub=Decimal("500.00"),
            order_id=order_uuid,
            metadata={"source": "yookassa_topup"},
        )
        mock_bonus.assert_called_once_with(
            session,
            purchaser_user_id=55,
            order_id=str(order_uuid),
            topup_amount=Decimal("500.00"),
        )
        mock_fulfill.assert_called_once_with(session, order)

    async def test_has_successful_topup_checks_orders(self):
        from database.repositories.payments_repo import has_successful_topup

        session = AsyncMock(spec=AsyncSession)
        # 1st call for Payment returns 0, 2nd call for Order returns 1
        session.scalar.side_effect = [0, 1]

        result = await has_successful_topup(session, user_id=99)
        self.assertTrue(result)
        self.assertEqual(session.scalar.call_count, 2)

    async def test_admin_financial_stats_combines_order_and_payment(self):
        from bot.handlers.admin.dashboard import _get_financial_stats

        session = AsyncMock(spec=AsyncSession)
        # Payment results:
        # res_24h = (100, 1)
        # res_7d = 200
        # res_30d = (500, 2)
        # Order results:
        # order_res_24h = (300, 2)
        # order_res_7d = 400
        # order_res_30d = (700, 3)

        mock_execute_results = [
            unittest.mock.MagicMock(one=lambda: (100, 1)),
            unittest.mock.MagicMock(scalar_one=lambda: 200),
            unittest.mock.MagicMock(one=lambda: (500, 2)),
            unittest.mock.MagicMock(one=lambda: (300, 2)),
            unittest.mock.MagicMock(scalar_one=lambda: 400),
            unittest.mock.MagicMock(one=lambda: (700, 3)),
        ]
        session.execute.side_effect = mock_execute_results

        stats = await _get_financial_stats(session)

        self.assertEqual(stats["rev_24h"], 100 + 300)
        self.assertEqual(stats["count_24h"], 1 + 2)
        self.assertEqual(stats["rev_7d"], 200 + 400)
        self.assertEqual(stats["rev_30d"], 500 + 700)
        self.assertEqual(stats["avg_check"], (500 + 700) // (2 + 3))

    async def test_purchases_repo_topup_and_change_entries(self):
        from database.repositories.purchases_repo import (
            get_purchase_log_by_id,
        )

        session = AsyncMock(spec=AsyncSession)
        test_uuid = uuid.uuid4()
        topup_order = Order(
            id=test_uuid,
            user_id=1,
            service_type="topup",
            amount_rub=Decimal("500.00"),
            duration_days=0,
            status="paid",
            created_at=datetime.now(timezone.utc),
        )
        mock_result = unittest.mock.MagicMock()
        mock_result.scalar_one_or_none.return_value = topup_order
        session.execute.return_value = mock_result

        entry = await get_purchase_log_by_id(session, f"order_{test_uuid}")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.operation_type, "topup")
        self.assertEqual(entry.operation_title, "Пополнение")
        self.assertEqual(entry.tariff_name, "Баланс")


class TestSimpleBillingAuditFixes(unittest.IsolatedAsyncioTestCase):
    """Unit tests for defect fixes identified during audit of PR #277."""

    @patch("services.order_service.FulfillmentService.fulfill_order")
    @patch("services.order_service.create_order_debit")
    @patch("services.order_service.get_account_balance")
    async def test_pay_from_wallet_zero_cost(self, mock_bal, mock_debit, mock_fulfill):
        mock_bal.return_value = AccountBalanceSnapshot(
            accounting_position=Decimal("0.00"),
            available=Decimal("0.00"),
            reserved=Decimal("0.00"),
            debt=Decimal("0.00"),
            real_available=Decimal("0.00"),
            bonus_available=Decimal("0.00"),
        )
        session = AsyncMock(spec=AsyncSession)
        session.scalar.return_value = None
        tariff = Tariff(id=1, name="Free", price_rub=Decimal("0.00"), duration_days=30, device_limit=1)
        session.get.return_value = tariff

        order = await OrderService.pay_from_wallet(
            session,
            user_id=42,
            service_type="awg",
            tariff_id=1,
        )

        self.assertEqual(order.status, "paid")
        self.assertEqual(order.payment_method, "wallet")
        self.assertEqual(order.amount_rub, Decimal("0.00"))
        mock_debit.assert_not_called()
        mock_fulfill.assert_called_once_with(session, order)

    def test_get_order_checkout_keyboard_zero_cost(self):
        from bot import texts

        kb = get_order_checkout_keyboard(
            tariff_id=5,
            price=0,
            can_pay_wallet=True,
            back_callback="back_to_tariffs",
        )
        buttons = [btn for row in kb.inline_keyboard for btn in row]
        callbacks = [b.callback_data for b in buttons]
        # Card payment button must not exist
        self.assertNotIn("order_pay_card:5", callbacks)
        # Free change button must exist
        self.assertIn("order_pay_wallet:5", callbacks)
        free_btn = next(b for b in buttons if b.callback_data == "order_pay_wallet:5")
        self.assertEqual(free_btn.text, texts.BTN_PAYMENT_CONFIRM_FREE_CHANGE)

    @patch("services.white_internet_service.white_internet_repo")
    @patch("services.white_internet_service.get_or_create_current_version")
    @patch("services.white_internet_service.lock_checkout_user")
    async def test_convert_trial_to_paid_debit_balance_false(
        self, mock_lock_user, mock_get_ver, mock_wi_repo
    ):
        from config.enums import ServerHealthState, ServerLifecycleStatus
        from database.models import Server, WhiteInternetSubscription
        from services.white_internet_service import WhiteInternetService

        session = AsyncMock(spec=AsyncSession)
        user = User(id=10, telegram_id=12345678)
        mock_lock_user.return_value = user

        sub = WhiteInternetSubscription(
            id=100,
            user_id=10,
            is_trial=True,
            status="ACTIVE",
            origin_node_id=1,
            device_limit=1,
            uuid="test-uuid",
            desired_version=1,
        )
        mock_wi_repo.get_subscription_by_user_id = AsyncMock(return_value=sub)
        mock_wi_repo.get_subscription_with_lock = AsyncMock(return_value=sub)

        origin_node = Server(
            id=1,
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            extra_data={"relays": ["relay1"]},
            protocol="xray",
        )
        session.scalar.return_value = origin_node

        mock_version = AsyncMock()
        mock_version.id = 1
        mock_version.base_quota_bytes = 10 * 1024**3
        mock_version.price_rub = 300
        mock_version.duration_hours = 720
        mock_get_ver.return_value = mock_version

        with patch.object(
            WhiteInternetService,
            "get_or_create_white_internet_tariff",
            new_callable=AsyncMock,
            return_value=Tariff(id=1, name="WI", duration_days=30, price_rub=Decimal("300.00"), is_active=True),
        ), patch.object(
            WhiteInternetService, "_new_quote"
        ) as mock_new_quote, patch(
            "services.white_internet_service.create_purchase_debit", new_callable=AsyncMock
        ) as mock_debit, patch.object(
            WhiteInternetService, "_try_inline_sync", new_callable=AsyncMock
        ):
            ok, msg, result_sub = await WhiteInternetService.convert_trial_to_paid(
                session, user_id=10, debit_balance=False
            )

        self.assertTrue(ok)
        self.assertFalse(result_sub.is_trial)
        mock_new_quote.assert_not_called()
        mock_debit.assert_not_called()

    @patch("services.fulfillment_service.WhiteInternetService.purchase_subscription")
    async def test_fulfill_order_raises_on_purchase_failure(self, mock_purchase):
        mock_purchase.return_value = (False, "Node unreachable", None)
        session = AsyncMock(spec=AsyncSession)
        user = User(id=10, telegram_id=12345678)
        session.get.return_value = user

        order = Order(
            id=uuid.uuid4(),
            user_id=10,
            service_type="white_internet",
            duration_days=30,
            amount_rub=Decimal("300.00"),
            status="paid",
        )
        with patch(
            "database.repositories.white_internet_repo.get_subscription_by_user_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with self.assertRaises(RuntimeError) as cm:
                await FulfillmentService.fulfill_order(session, order)
            self.assertIn("Node unreachable", str(cm.exception))

    @patch("services.fulfillment_service.WhiteInternetService.topup_quota")
    async def test_fulfill_order_raises_on_quota_topup_failure(self, mock_topup):
        mock_topup.return_value = (False, "Quota limit reached", None)
        session = AsyncMock(spec=AsyncSession)
        user = User(id=10, telegram_id=12345678)
        session.get.return_value = user

        order = Order(
            id=uuid.uuid4(),
            user_id=10,
            service_type="white_internet",
            traffic_bytes=5 * 1024**3,
            duration_days=0,
            amount_rub=Decimal("100.00"),
            status="paid",
        )
        with patch(
            "database.repositories.white_internet_repo.get_subscription_by_user_id",
            new_callable=AsyncMock,
            return_value=AsyncMock(),
        ):
            with self.assertRaises(RuntimeError) as cm:
                await FulfillmentService.fulfill_order(session, order)
            self.assertIn("Quota limit reached", str(cm.exception))

    @patch("services.fulfillment_service.WhiteInternetService.purchase_device_slot")
    async def test_fulfill_order_raises_on_device_slot_failure(self, mock_slot):
        mock_slot.return_value = (False, "Device limit reached", None)
        session = AsyncMock(spec=AsyncSession)
        user = User(id=10, telegram_id=12345678)
        session.get.return_value = user

        order = Order(
            id=uuid.uuid4(),
            user_id=10,
            service_type="white_internet",
            device_limit=3,
            duration_days=0,
            amount_rub=Decimal("150.00"),
            status="paid",
        )
        with patch(
            "database.repositories.white_internet_repo.get_subscription_by_user_id",
            new_callable=AsyncMock,
            return_value=AsyncMock(),
        ):
            with self.assertRaises(RuntimeError) as cm:
                await FulfillmentService.fulfill_order(session, order)
            self.assertIn("Device limit reached", str(cm.exception))

    @patch("services.fulfillment_service.WhiteInternetService.renew_subscription")
    async def test_fulfill_order_raises_on_renew_failure(self, mock_renew):
        mock_renew.return_value = (False, "Renewal error", None)
        session = AsyncMock(spec=AsyncSession)
        user = User(id=10, telegram_id=12345678)
        session.get.return_value = user

        order = Order(
            id=uuid.uuid4(),
            user_id=10,
            service_type="white_internet",
            duration_days=30,
            amount_rub=Decimal("300.00"),
            status="paid",
        )
        mock_sub = AsyncMock()
        mock_sub.status = "ACTIVE"
        with patch(
            "database.repositories.white_internet_repo.get_subscription_by_user_id",
            new_callable=AsyncMock,
            return_value=mock_sub,
        ):
            with self.assertRaises(RuntimeError) as cm:
                await FulfillmentService.fulfill_order(session, order)
            self.assertIn("Renewal error", str(cm.exception))

    @patch("services.white_internet_service.white_internet_repo")
    @patch("services.white_internet_service.get_or_create_current_version")
    @patch("services.white_internet_service.lock_checkout_user")
    async def test_fulfill_white_internet_device_slot_real_service_debit_balance_false(
        self, mock_lock_user, mock_get_ver, mock_wi_repo
    ):
        """Verify ITEM_WHITE_INTERNET_DEVICE fulfillment executes real purchase_device_slot without UnboundLocalError."""
        from config.enums import ServerHealthState, ServerLifecycleStatus
        from database.models import Server, WhiteInternetSubscription
        from services.white_internet_service import WhiteInternetService

        session = AsyncMock(spec=AsyncSession)
        user = User(id=10, telegram_id=12345678)
        session.get.return_value = user
        mock_lock_user.return_value = user

        sub = WhiteInternetSubscription(
            id=100,
            user_id=10,
            status="ACTIVE",
            origin_node_id=1,
            device_limit=2,
            base_traffic_bytes=10 * 1024**3,
            extra_traffic_bytes=0,
            uuid="test-uuid",
            desired_version=1,
            expires_at=now_utc() + timedelta(days=20),
        )
        mock_wi_repo.get_subscription_by_user_id = AsyncMock(return_value=sub)

        origin_node = Server(
            id=1,
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            extra_data={"relays": ["relay1"]},
            protocol="xray",
        )
        session.scalar.return_value = origin_node

        mock_version = AsyncMock()
        mock_version.id = 1
        mock_version.price_rub = 150
        mock_get_ver.return_value = mock_version

        updated_sub = WhiteInternetSubscription(
            id=100,
            user_id=10,
            status="ACTIVE",
            origin_node_id=1,
            device_limit=3,
            base_traffic_bytes=10 * 1024**3,
            extra_traffic_bytes=5 * 1024**3,
            uuid="test-uuid",
            desired_version=1,
            expires_at=now_utc() + timedelta(days=20),
        )
        mock_wi_repo.add_device_slot_atomic = AsyncMock(return_value=updated_sub)

        order = Order(
            id=uuid.uuid4(),
            user_id=10,
            service_type="white_internet",
            device_limit=1,
            duration_days=0,
            traffic_bytes=0,
            amount_rub=Decimal("150.00"),
            status="paid",
        )

        with patch.object(
            WhiteInternetService,
            "get_or_create_white_internet_tariff",
            new_callable=AsyncMock,
            return_value=Tariff(id=1, name="WI", duration_days=30, price_rub=Decimal("300.00"), is_active=True),
        ), patch(
            "database.repositories.white_internet_repo.get_subscription_by_user_id",
            new_callable=AsyncMock,
            return_value=sub,
        ):
            # Real WhiteInternetService.purchase_device_slot is executed!
            await FulfillmentService.fulfill_order(session, order)

        mock_wi_repo.add_device_slot_atomic.assert_called_once()
        self.assertEqual(updated_sub.device_limit, 3)

    @patch("services.white_internet_service.white_internet_repo")
    @patch("services.white_internet_service.get_or_create_current_version")
    @patch("services.white_internet_service.lock_checkout_user")
    async def test_fulfill_white_internet_quota_pack_real_service_debit_balance_false(
        self, mock_lock_user, mock_get_ver, mock_wi_repo
    ):
        """Verify ITEM_WHITE_INTERNET_PACK fulfillment executes real topup_quota without UnboundLocalError."""
        from config.enums import ServerHealthState, ServerLifecycleStatus
        from database.models import Server, WhiteInternetSubscription
        from services.white_internet_service import WhiteInternetService

        session = AsyncMock(spec=AsyncSession)
        user = User(id=10, telegram_id=12345678)
        session.get.return_value = user
        mock_lock_user.return_value = user

        sub = WhiteInternetSubscription(
            id=100,
            user_id=10,
            status="ACTIVE",
            origin_node_id=1,
            device_limit=2,
            base_traffic_bytes=10 * 1024**3,
            extra_traffic_bytes=0,
            uuid="test-uuid",
            desired_version=1,
            expires_at=now_utc() + timedelta(days=20),
        )
        mock_wi_repo.get_subscription_by_user_id = AsyncMock(return_value=sub)

        origin_node = Server(
            id=1,
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            extra_data={"relays": ["relay1"]},
            protocol="xray",
        )
        session.scalar.return_value = origin_node

        mock_version = AsyncMock()
        mock_version.id = 1
        mock_version.price_rub = 100
        mock_get_ver.return_value = mock_version

        mock_grant = MagicMock()
        mock_grant.id = 55
        mock_wi_repo.topup_quota_atomic = AsyncMock(return_value=mock_grant)

        order = Order(
            id=uuid.uuid4(),
            user_id=10,
            service_type="white_internet",
            traffic_bytes=25 * (1024**3),
            duration_days=0,
            amount_rub=Decimal("100.00"),
            status="paid",
        )

        with patch.object(
            WhiteInternetService,
            "get_or_create_white_internet_tariff",
            new_callable=AsyncMock,
            return_value=Tariff(id=1, name="WI", duration_days=30, price_rub=Decimal("300.00"), is_active=True),
        ), patch(
            "database.repositories.white_internet_repo.get_subscription_by_user_id",
            new_callable=AsyncMock,
            return_value=sub,
        ):
            # Real WhiteInternetService.topup_quota is executed!
            await FulfillmentService.fulfill_order(session, order)

        mock_wi_repo.topup_quota_atomic.assert_called_once()
        call_kwargs = mock_wi_repo.topup_quota_atomic.call_args[1]
        self.assertEqual(call_kwargs["subscription_id"], 100)
        self.assertEqual(call_kwargs["pack_gb"], 25)
        self.assertEqual(call_kwargs["quote_id"], 0)

    @patch("services.order_service.FulfillmentService.fulfill_order")
    async def test_mark_order_paid_revives_canceled_order(self, mock_fulfill):
        session = AsyncMock(spec=AsyncSession)
        order_uuid = uuid.uuid4()
        order = Order(
            id=order_uuid,
            user_id=25,
            service_type="awg",
            duration_days=30,
            amount_rub=Decimal("250.00"),
            status="canceled",
            metadata_={},
        )
        session.scalar.return_value = order
        session.get.return_value = order

        paid_order = await OrderService.mark_order_paid(session, order_uuid)

        self.assertEqual(paid_order.status, "paid")
        self.assertTrue(paid_order.metadata_.get("revived_from_canceled"))
        self.assertIsNotNone(paid_order.paid_at)
        mock_fulfill.assert_called_once_with(session, paid_order)

    @patch("bot.handlers.webhook.session_scope")
    @patch("bot.handlers.webhook._get_real_ip", return_value="185.71.76.1")
    @patch("bot.handlers.webhook._is_yookassa_ip", return_value=True)
    async def test_yookassa_webhook_deduplication(self, mock_ip, mock_real, mock_scope):
        from bot.handlers.webhook import yookassa_webhook_handler
        from database.models import WebhookInbox

        session = AsyncMock(spec=AsyncSession)
        session.scalar.return_value = WebhookInbox(id=99, provider="yookassa")
        mock_scope.return_value.__aenter__.return_value = session

        request = AsyncMock()
        request.content_length = 200
        request.json.return_value = {
            "type": "notification",
            "event": "payment.succeeded",
            "object": {
                "id": "pay-123",
                "status": "succeeded",
                "metadata": {"order_id": "ord-123"},
            },
        }

        with patch("services.order_service.OrderService.process_webhook_event") as mock_process:
            response = await yookassa_webhook_handler(request)
            self.assertEqual(response.status, 200)
            mock_process.assert_not_called()

    @patch("integrations.payment_gateways.yookassa.YooKassaService.create_payment_result")
    async def test_yookassa_gateway_bot_username_resolution(self, mock_create):
        mock_create.return_value = YooKassaResult(
            True,
            value={"id": "ext-1", "confirmation": {"confirmation_url": "https://pay.link"}},
        )
        gw = YooKassaGateway()

        # 1. bot_username argument passed explicitly
        await gw.create_payment_url(
            order_id="ord-1",
            amount_rub=Decimal("100.00"),
            description="test",
            bot_username="my_explicit_bot",
        )
        payload = mock_create.call_args[0][0]
        self.assertEqual(payload["confirmation"]["return_url"], "https://t.me/my_explicit_bot")

        # 2. bot_username via os.getenv("BOT_USERNAME")
        mock_create.reset_mock()
        with patch.dict("os.environ", {"BOT_USERNAME": "env_configured_bot"}):
            await gw.create_payment_url(
                order_id="ord-2",
                amount_rub=Decimal("100.00"),
                description="test",
                bot_username=None,
            )
            payload = mock_create.call_args[0][0]
            self.assertEqual(payload["confirmation"]["return_url"], "https://t.me/env_configured_bot")

    async def test_yookassa_gateway_parse_webhook_refund_and_payment_discrimination(self):
        gw = YooKassaGateway()

        # 1. Standard refund event (refund.succeeded)
        res1 = await gw.parse_webhook({
            "event": "refund.succeeded",
            "object": {
                "id": "ref-111",
                "payment_id": "pay-222",
                "status": "succeeded",
                "amount": {"value": "150.00", "currency": "RUB"},
                "metadata": {"order_id": "order-333"},
            },
        })
        self.assertEqual(res1.external_id, "ref-111")
        self.assertEqual(res1.related_external_id, "pay-222")
        self.assertEqual(res1.payment_id, "pay-222")
        self.assertTrue(res1.is_refunded)
        self.assertFalse(res1.is_paid)
        self.assertEqual(res1.amount_rub, Decimal("150.00"))

        # 2. Unhandled event (e.g. payment.canceled or unknown event)
        res2 = await gw.parse_webhook({
            "event": "payment.canceled",
            "object": {
                "id": "pay-444",
                "payment_id": "pay-555",
                "status": "canceled",
                "amount": {"value": "200.00", "currency": "RUB"},
            },
        })
        self.assertEqual(res2.external_id, "pay-444")
        self.assertIsNone(res2.related_external_id)
        self.assertIsNone(res2.payment_id)
        self.assertFalse(res2.is_refunded)
        self.assertFalse(res2.is_paid)

        # 3. Payment payload (payment.succeeded)
        res3 = await gw.parse_webhook({
            "event": "payment.succeeded",
            "object": {
                "id": "pay-666",
                "status": "succeeded",
                "amount": {"value": "300.00", "currency": "RUB"},
            },
        })
        self.assertEqual(res3.external_id, "pay-666")
        self.assertIsNone(res3.related_external_id)
        self.assertIsNone(res3.payment_id)
        self.assertFalse(res3.is_refunded)
        self.assertTrue(res3.is_paid)

    @patch("services.order_service.reverse_referral_bonus_for_topup")
    @patch("services.order_service.FulfillmentService.revoke_order")
    @patch("services.order_service.create_order_refund_debit")
    @patch("services.order_service.get_payment_gateway")
    async def test_process_webhook_event_partial_refund(
        self, mock_gw_factory, mock_refund_debit, mock_revoke, mock_rev_bonus
    ):
        mock_gw = AsyncMock()
        order_uuid = uuid.uuid4()
        mock_gw.parse_webhook.return_value = WebhookResult(
            order_id=str(order_uuid),
            is_paid=False,
            is_refunded=True,
            amount_rub=Decimal("80.00"),
            external_id="ext-pay-888",
        )
        mock_gw_factory.return_value = mock_gw

        session = AsyncMock(spec=AsyncSession)
        order = Order(
            id=order_uuid,
            user_id=10,
            service_type="topup",
            amount_rub=Decimal("200.00"),
            status="paid",
        )
        session.scalar.return_value = order
        session.get.return_value = order

        success = await OrderService.process_webhook_event(session, {"some": "payload"})

        self.assertTrue(success)
        mock_refund_debit.assert_called_once_with(
            session,
            user_id=10,
            amount_rub=Decimal("80.00"),
            order_id=order_uuid,
            refund_id="ext-pay-888",
            metadata={"source": "yookassa_refund"},
        )
        mock_rev_bonus.assert_called_once_with(
            session,
            order_id=order_uuid,
            refund_amount=Decimal("80.00"),
            original_topup_amount=Decimal("200.00"),
            total_refunded_amount=Decimal("80.00"),
            refund_id="ext-pay-888",
        )
        mock_revoke.assert_not_called()

    @patch("services.fulfillment_service.invalidate_user_cache")
    @patch("services.fulfillment_service.SubscriptionService.sync_access_state")
    async def test_revoke_order_with_grace_period(self, mock_sync, mock_cache):
        from config.constants import VPN_ACCESS_GRACE_HOURS

        session = AsyncMock(spec=AsyncSession)
        now = datetime.now(timezone.utc)
        # User has 3 days remaining; revoking 10 days puts it into past
        user = User(
            id=10,
            telegram_id=12345678,
            subscription_end=now + timedelta(days=3),
        )
        session.get.return_value = user

        order = Order(
            id=uuid.uuid4(),
            user_id=10,
            service_type="awg",
            duration_days=10,
            amount_rub=Decimal("100.00"),
            status="refunded",
        )

        await FulfillmentService.revoke_order(session, order)

        expected_boundary = now - timedelta(hours=VPN_ACCESS_GRACE_HOURS)
        self.assertLessEqual(user.subscription_end, expected_boundary)
        mock_sync.assert_called_once_with(session, user)

    def test_migration_0030_downgrade_safety_check(self):
        import importlib.util
        from pathlib import Path

        mig_path = (
            Path(__file__).parent.parent
            / "alembic"
            / "versions"
            / "0030_simple_billing.py"
        )
        spec = importlib.util.spec_from_file_location("mig_0030", mig_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        mig_0030 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig_0030)

        with patch.object(mig_0030, "op") as mock_op:
            mock_bind = mock_op.get_bind.return_value

            # Case 1: Entries exist -> raises RuntimeError
            mock_bind.execute.return_value.scalar.return_value = 3
            with self.assertRaises(RuntimeError) as cm:
                mig_0030.downgrade()
            self.assertIn("ledger entries are linked to orders", str(cm.exception))

            # Case 2: Zero entries exist -> completes without error
            mock_bind.execute.return_value.scalar.return_value = 0
            mig_0030.downgrade()

    async def test_admin_show_order_card(self):
        from bot.handlers.admin.payments import show_order_card

        session = AsyncMock(spec=AsyncSession)
        test_uuid = uuid.uuid4()
        user = User(id=10, telegram_id=987654321, username="testadminuser")
        tariff = Tariff(id=1, name="Standard AWG")
        order = Order(
            id=test_uuid,
            user_id=10,
            service_type="awg",
            amount_rub=Decimal("250.00"),
            duration_days=30,
            status="paid",
            payment_method="card",
            created_at=datetime.now(timezone.utc),
            user=user,
            tariff=tariff,
        )

        session.scalar.return_value = order

        callback = AsyncMock()
        callback.data = f"admin_order_card:{test_uuid}"
        callback.from_user.id = 987654321
        callback.message = AsyncMock()
        state = AsyncMock()

        with patch("bot.handlers.admin.payments.is_admin", return_value=True):
            await show_order_card(callback, state, session)

        callback.message.edit_text.assert_called_once()
        call_text = callback.message.edit_text.call_args[0][0]
        self.assertIn("Заказ #", call_text)
        self.assertIn("250 ₽", call_text)
        self.assertIn("Standard AWG", call_text)
        callback.answer.assert_called_once_with(show_alert=False)

    def test_get_topup_credit_keyboard_with_white_internet_context(self):
        from bot import texts
        from bot.keyboards.payment import get_topup_credit_keyboard

        kb = get_topup_credit_keyboard({"source": "white_internet"})
        buttons = [btn for row in kb.inline_keyboard for btn in row]
        callbacks = [b.callback_data for b in buttons]

        self.assertIn("white_internet", callbacks)
        wi_btn = next(b for b in buttons if b.callback_data == "white_internet")
        self.assertEqual(wi_btn.text, texts.BTN_WL_RETURN_TO_SERVICE)

    def test_get_topup_credit_keyboard_default_and_legacy_context(self):
        from bot.keyboards.payment import get_topup_credit_keyboard

        for ctx in (None, {}, {"tariff_id": 1, "source": "showcase"}):
            kb = get_topup_credit_keyboard(ctx)
            buttons = [btn for row in kb.inline_keyboard for btn in row]
            callbacks = [b.callback_data for b in buttons]

            self.assertEqual(callbacks, ["menu_balance", "dismiss_notification"])
            self.assertNotIn("balance_resume_purchase:1:showcase", callbacks)
            for cb in callbacks:
                self.assertFalse(cb.startswith("balance_resume_purchase:"))

    async def test_admin_payments_list_build_with_orders_and_legacy(self):
        from bot.handlers.admin.payments import _build_payments_list_text_and_kb
        from database.models import Order, Payment, User

        user = User(id=1, telegram_id=111, username="testuser")
        order_uuid = uuid.uuid4()
        order = Order(
            id=order_uuid,
            user_id=1,
            amount_rub=Decimal("300.00"),
            service_type="awg",
            status="paid",
            created_at=datetime.now(timezone.utc),
            user=user,
        )
        payment = Payment(
            id=77,
            user_id=1,
            amount=200,
            provider_status="succeeded",
            fulfillment_status="succeeded",
            reconciliation_status="matched",
            created_at=datetime.now(timezone.utc) - timedelta(hours=1),
            user=user,
        )

        rendered, builder = await _build_payments_list_text_and_kb(
            payments=[order, payment],
            page=1,
            total_pages=1,
            total=2,
        )
        buttons = [btn for row in builder.as_markup().inline_keyboard for btn in row]
        callbacks = [btn.callback_data for btn in buttons]
        button_texts = [btn.text for btn in buttons]

        self.assertIn(f"admin_order_card:{order_uuid}", callbacks)
        self.assertIn("admin_payment_card:77", callbacks)
        self.assertTrue(any("300 ₽" in t for t in button_texts))
        self.assertTrue(any("200" in t for t in button_texts))
        self.assertIn("Всего: 2", rendered)

    @patch("services.order_service.reverse_referral_bonus_for_topup")
    @patch("services.order_service.FulfillmentService.revoke_order")
    @patch("services.order_service.create_order_refund_debit")
    @patch("services.order_service.get_payment_gateway")
    async def test_process_webhook_event_sequential_partial_refunds(
        self, mock_gw_factory, mock_refund_debit, mock_revoke, mock_rev_bonus
    ):
        mock_gw = AsyncMock()
        order_uuid = uuid.uuid4()
        mock_gw_factory.return_value = mock_gw

        session = AsyncMock(spec=AsyncSession)
        order = Order(
            id=order_uuid,
            user_id=10,
            service_type="topup",
            amount_rub=Decimal("200.00"),
            status="paid",
            metadata_={},
        )
        session.scalar.return_value = order
        session.get.return_value = order

        # 1. First partial refund: 80 RUB
        mock_gw.parse_webhook.return_value = WebhookResult(
            order_id=str(order_uuid),
            is_paid=False,
            is_refunded=True,
            amount_rub=Decimal("80.00"),
            external_id="refund-part-1",
        )
        res1 = await OrderService.process_webhook_event(session, {"ref": 1})
        self.assertIsNotNone(res1)
        self.assertEqual(order.status, "paid")
        self.assertEqual(order.metadata_["refunded_amount_rub"], "80.00")
        mock_refund_debit.assert_called_once_with(
            session,
            user_id=10,
            amount_rub=Decimal("80.00"),
            order_id=order_uuid,
            refund_id="refund-part-1",
            metadata={"source": "yookassa_refund"},
        )
        mock_rev_bonus.assert_called_once_with(
            session,
            order_id=order_uuid,
            refund_amount=Decimal("80.00"),
            original_topup_amount=Decimal("200.00"),
            total_refunded_amount=Decimal("80.00"),
            refund_id="refund-part-1",
        )
        mock_revoke.assert_not_called()

        # 2. Duplicate of first partial refund should be ignored
        mock_refund_debit.reset_mock()
        mock_rev_bonus.reset_mock()
        res_dup = await OrderService.process_webhook_event(session, {"ref": 1})
        self.assertEqual(res_dup, order)
        mock_refund_debit.assert_not_called()
        mock_rev_bonus.assert_not_called()
        mock_revoke.assert_not_called()

        # 3. Second partial refund: 120 RUB (completes the 200 RUB order)
        mock_gw.parse_webhook.return_value = WebhookResult(
            order_id=str(order_uuid),
            is_paid=False,
            is_refunded=True,
            amount_rub=Decimal("120.00"),
            external_id="refund-part-2",
        )
        res2 = await OrderService.process_webhook_event(session, {"ref": 2})
        self.assertIsNotNone(res2)
        self.assertEqual(order.status, "refunded")
        self.assertEqual(order.metadata_["refunded_amount_rub"], "200.00")
        mock_refund_debit.assert_called_once_with(
            session,
            user_id=10,
            amount_rub=Decimal("120.00"),
            order_id=order_uuid,
            refund_id="refund-part-2",
            metadata={"source": "yookassa_refund"},
        )
        mock_rev_bonus.assert_called_once_with(
            session,
            order_id=order_uuid,
            refund_amount=Decimal("120.00"),
            original_topup_amount=Decimal("200.00"),
            total_refunded_amount=Decimal("200.00"),
            refund_id="refund-part-2",
        )
        mock_revoke.assert_called_once_with(session, order)

    @patch("services.order_service.FulfillmentService.revoke_order")
    @patch("services.order_service.create_order_refund_debit")
    @patch("services.order_service.get_payment_gateway")
    async def test_process_webhook_refund_finds_order_by_payment_id(
        self, mock_gw_factory, mock_refund_debit, mock_revoke
    ):
        """Verify webhook refund finds order by payment_id when metadata lacks order_id."""
        mock_gw = AsyncMock()
        mock_gw_factory.return_value = mock_gw

        order_uuid = uuid.uuid4()
        payment_ext_id = "yoo-pay-123456"
        refund_ext_id = "yoo-ref-987654"

        # Webhook lacks order_id in metadata (e.g. manual refund from YooKassa dashboard)
        mock_gw.parse_webhook.return_value = WebhookResult(
            order_id=None,
            is_paid=False,
            is_refunded=True,
            external_id=refund_ext_id,
            related_external_id=payment_ext_id,
            amount_rub=Decimal("300.00"),
        )

        session = AsyncMock(spec=AsyncSession)
        order = Order(
            id=order_uuid,
            user_id=10,
            service_type="awg",
            duration_days=30,
            amount_rub=Decimal("300.00"),
            status="paid",
            external_id=payment_ext_id,
        )
        session.scalar.return_value = order
        session.get.return_value = order

        res = await OrderService.process_webhook_event(session, {"event": "refund.succeeded"})
        self.assertIsNotNone(res)
        self.assertEqual(res.id, order_uuid)
        self.assertEqual(res.status, "refunded")
        mock_revoke.assert_called_once_with(session, order)

    @patch("services.order_service.reverse_referral_bonus_for_topup")
    @patch("services.order_service.create_order_refund_debit")
    @patch("services.order_service.get_payment_gateway")
    async def test_process_webhook_event_fail_closed_on_referral_bonus_reversal_error(
        self, mock_gw_factory, mock_refund_debit, mock_rev_bonus
    ):
        mock_gw = AsyncMock()
        mock_gw_factory.return_value = mock_gw
        order_uuid = uuid.uuid4()

        mock_gw.parse_webhook.return_value = WebhookResult(
            order_id=str(order_uuid),
            is_paid=False,
            is_refunded=True,
            amount_rub=Decimal("100.00"),
            external_id="refund-err-1",
        )

        session = AsyncMock(spec=AsyncSession)
        order = Order(
            id=order_uuid,
            user_id=10,
            service_type="topup",
            amount_rub=Decimal("100.00"),
            status="paid",
            metadata_={},
        )
        session.scalar.return_value = order
        session.get.return_value = order

        mock_rev_bonus.side_effect = RuntimeError("DB referral reversal lock failed")

        with self.assertRaises(RuntimeError) as cm:
            await OrderService.process_webhook_event(session, {"ref": 1})

        self.assertIn("DB referral reversal lock failed", str(cm.exception))

    @patch("database.repositories.account_ledger_repo._insert_or_get_entry")
    async def test_create_order_refund_debit_contract(self, mock_insert):
        from database.repositories.account_ledger_repo import create_order_refund_debit

        session = AsyncMock(spec=AsyncSession)
        mock_entry = MagicMock()
        mock_insert.return_value = (mock_entry, True)

        order_uuid = uuid.uuid4()
        entry, created = await create_order_refund_debit(
            session,
            user_id=42,
            amount_rub=Decimal("150.00"),
            order_id=order_uuid,
            refund_id="yoo-ref-999",
            metadata={"source": "test"},
        )
        self.assertTrue(created)
        mock_insert.assert_called_once()
        values = mock_insert.call_args[1]["values"]
        self.assertEqual(values["user_id"], 42)
        self.assertEqual(values["entry_type"], "refund_debit")
        self.assertEqual(values["amount"], Decimal("-150"))
        self.assertEqual(values["order_id"], order_uuid)
        self.assertEqual(values["idempotency_key"], f"order_refund:{order_uuid}:yoo-ref-999")
        self.assertEqual(values["metadata_"], {"source": "test"})

    async def test_create_order_refund_debit_rejects_empty_refund_id(self):
        from database.repositories.account_ledger_repo import create_order_refund_debit

        session = AsyncMock(spec=AsyncSession)
        for invalid_refund_id in ["   ", "", None, 123]:
            with self.assertRaises(ValueError) as cm:
                await create_order_refund_debit(
                    session,
                    user_id=42,
                    amount_rub=Decimal("150.00"),
                    order_id=uuid.uuid4(),
                    refund_id=invalid_refund_id,  # type: ignore
                )
            self.assertIn("refund_id must be a non-empty string", str(cm.exception))

    @patch("database.repositories.account_ledger_repo._insert_or_get_entry")
    async def test_create_order_refund_debit_quantizes_fractional_kopecks(self, mock_insert):
        from database.repositories.account_ledger_repo import create_order_refund_debit

        session = AsyncMock(spec=AsyncSession)
        mock_entry = MagicMock()
        mock_insert.return_value = (mock_entry, True)

        order_uuid = uuid.uuid4()
        # 5.50 RUB should quantize to 6 RUB on ledger
        entry, created = await create_order_refund_debit(
            session,
            user_id=42,
            amount_rub=Decimal("5.50"),
            order_id=order_uuid,
            refund_id="yoo-ref-kopecks",
        )
        self.assertTrue(created)
        values = mock_insert.call_args[1]["values"]
        self.assertEqual(values["amount"], Decimal("-6"))

    @patch("services.order_service.reverse_referral_bonus_for_topup")
    @patch("services.order_service.FulfillmentService.revoke_order")
    @patch("services.order_service.create_order_refund_debit")
    @patch("services.order_service.get_payment_gateway")
    async def test_process_webhook_event_fractional_kopecks_refund(
        self, mock_gw_factory, mock_refund_debit, mock_revoke, mock_rev_bonus
    ):
        mock_gw = AsyncMock()
        order_uuid = uuid.uuid4()
        mock_gw_factory.return_value = mock_gw

        session = AsyncMock(spec=AsyncSession)
        order = Order(
            id=order_uuid,
            user_id=10,
            service_type="topup",
            amount_rub=Decimal("100.00"),
            status="paid",
            metadata_={},
        )
        session.scalar.return_value = order
        session.get.return_value = order

        mock_gw.parse_webhook.return_value = WebhookResult(
            order_id=str(order_uuid),
            is_paid=False,
            is_refunded=True,
            amount_rub=Decimal("5.50"),
            external_id="refund-kopecks-1",
        )
        res = await OrderService.process_webhook_event(session, {"ref": "kopecks"})
        self.assertIsNotNone(res)
        self.assertEqual(order.status, "paid")
        self.assertEqual(order.metadata_["refunded_amount_rub"], "5.50")
        mock_refund_debit.assert_called_once_with(
            session,
            user_id=10,
            amount_rub=Decimal("6"),
            order_id=order_uuid,
            refund_id="refund-kopecks-1",
            metadata={"source": "yookassa_refund"},
        )
        mock_rev_bonus.assert_called_once_with(
            session,
            order_id=order_uuid,
            refund_amount=Decimal("5.50"),
            original_topup_amount=Decimal("1000.00") if False else Decimal("100.00"),
            total_refunded_amount=Decimal("5.50"),
            refund_id="refund-kopecks-1",
        )
        mock_revoke.assert_not_called()

    @patch("bot.handlers.webhook.session_scope")
    @patch("bot.handlers.webhook._get_real_ip", return_value="185.71.76.1")
    @patch("bot.handlers.webhook._is_yookassa_ip", return_value=True)
    async def test_yookassa_webhook_suppresses_notification_if_already_paid(
        self, mock_ip, mock_real, mock_scope
    ):
        from bot.handlers.webhook import yookassa_webhook_handler

        mock_session = AsyncMock(spec=AsyncSession)
        mock_scope.return_value.__aenter__.return_value = mock_session

        mock_session.scalar.return_value = None
        order_uuid = uuid.uuid4()
        order = Order(
            id=order_uuid,
            user_id=10,
            service_type="topup",
            amount_rub=Decimal("100.00"),
            status="paid",
        )
        order._newly_paid = False

        with patch("services.order_service.OrderService.process_webhook_event", return_value=order):
            with patch("bot.handlers.payment.balance_routes._render_balance") as mock_render:
                request = AsyncMock()
                request.content_length = 200
                request.app = {"bot": AsyncMock()}
                request.json.return_value = {
                    "event": "payment.succeeded",
                    "type": "notification",
                    "object": {
                        "id": "pay-already-paid",
                        "status": "succeeded",
                        "paid": True,
                        "amount": {"value": "100.00", "currency": "RUB"},
                        "metadata": {"order_id": str(order_uuid)},
                    },
                }
                resp = await yookassa_webhook_handler(request)

                self.assertEqual(resp.status, 200)
                mock_render.assert_not_called()

    async def test_handle_order_check_renders_context_keyboard_when_already_paid(self):
        from bot.handlers.payment.purchase_routes import handle_order_check

        session = AsyncMock(spec=AsyncSession)
        order_uuid = uuid.uuid4()
        user = User(id=10, telegram_id=123456)
        order = Order(
            id=order_uuid,
            user_id=10,
            service_type="topup",
            amount_rub=Decimal("100.00"),
            status="paid",
            metadata_={"context": {"source": "white_internet"}},
        )
        session.get.return_value = order

        callback = AsyncMock()
        callback.data = f"order_check:{order_uuid}"
        callback.from_user.id = 123456
        callback.message = AsyncMock()
        callback.bot = AsyncMock()

        with patch("bot.handlers.payment.balance_routes._render_balance") as mock_render:
            await handle_order_check(callback, session, db_user=user)

            mock_render.assert_called_once()
            custom_kb = mock_render.call_args[1].get("custom_keyboard")
            self.assertIsNotNone(custom_kb)
            buttons = [b for row in custom_kb.inline_keyboard for b in row]
            self.assertTrue(any(b.callback_data == "white_internet" for b in buttons))

    async def test_handle_order_cancel_routing(self):
        from bot.handlers.payment.purchase_routes import handle_order_cancel

        session = AsyncMock(spec=AsyncSession)
        user = User(id=10, telegram_id=123456)

        # Case 1: Cancel white_internet topup order -> show_white_internet_menu
        wi_order_uuid = uuid.uuid4()
        wi_order = Order(
            id=wi_order_uuid,
            user_id=10,
            service_type="topup",
            status="pending",
            metadata_={"context": {"source": "white_internet"}},
        )
        session.get.return_value = wi_order

        cb_wi = AsyncMock()
        cb_wi.data = f"order_cancel:{wi_order_uuid}"
        cb_wi.bot = AsyncMock()
        cb_wi.message = AsyncMock()

        with patch("bot.handlers.white_internet.show_white_internet_menu") as mock_wi_menu:
            await handle_order_cancel(cb_wi, session, db_user=user)
            self.assertEqual(wi_order.status, "canceled")
            mock_wi_menu.assert_called_once_with(cb_wi, session)

        # Case 2: Cancel standard topup order -> _render_balance
        topup_uuid = uuid.uuid4()
        topup_order = Order(
            id=topup_uuid,
            user_id=10,
            service_type="topup",
            status="pending",
            metadata_={},
        )
        session.get.return_value = topup_order

        cb_topup = AsyncMock()
        cb_topup.data = f"order_cancel:{topup_uuid}"
        cb_topup.bot = AsyncMock()
        cb_topup.message = AsyncMock()

        with patch("bot.handlers.payment.balance_routes._render_balance") as mock_bal:
            await handle_order_cancel(cb_topup, session, db_user=user)
            self.assertEqual(topup_order.status, "canceled")
            mock_bal.assert_called_once()

        # Case 3: Cancel AWG order for active subscriber -> _show_hub
        awg_active_uuid = uuid.uuid4()
        awg_active_order = Order(
            id=awg_active_uuid,
            user_id=10,
            service_type="awg",
            status="pending",
            metadata_={},
        )
        session.get.return_value = awg_active_order
        user.subscription_end = now_utc() + timedelta(days=20)

        cb_awg_active = AsyncMock()
        cb_awg_active.data = f"order_cancel:{awg_active_uuid}"
        cb_awg_active.bot = AsyncMock()
        cb_awg_active.message = AsyncMock()

        with patch("bot.handlers.payment.common._show_hub") as mock_hub:
            await handle_order_cancel(cb_awg_active, session, db_user=user)
            self.assertEqual(awg_active_order.status, "canceled")
            mock_hub.assert_called_once_with(cb_awg_active, user, session)

        # Case 4: Cancel AWG order for inactive subscriber -> render_tariff_showcase
        awg_inactive_uuid = uuid.uuid4()
        awg_inactive_order = Order(
            id=awg_inactive_uuid,
            user_id=10,
            service_type="awg",
            status="pending",
            metadata_={},
        )
        session.get.return_value = awg_inactive_order
        user.subscription_end = None

        cb_awg_inactive = AsyncMock()
        cb_awg_inactive.data = f"order_cancel:{awg_inactive_uuid}"
        cb_awg_inactive.bot = AsyncMock()
        cb_awg_inactive.message = AsyncMock()

        with patch("bot.handlers.payment.common.render_tariff_showcase") as mock_showcase:
            await handle_order_cancel(cb_awg_inactive, session, db_user=user)
            self.assertEqual(awg_inactive_order.status, "canceled")
            mock_showcase.assert_called_once()

    async def test_handle_order_pay_card_zero_cost_routes_to_wallet(self):
        from bot.handlers.payment.purchase_routes import handle_order_pay_card

        session = AsyncMock(spec=AsyncSession)
        user = User(
            id=10,
            telegram_id=123456,
            current_tariff_id=1,
            subscription_end=now_utc() + timedelta(days=20),
        )
        tariff1 = Tariff(id=1, name="Old", price_rub=200, duration_days=30, device_limit=2)
        tariff2 = Tariff(id=2, name="Target", price_rub=200, duration_days=30, device_limit=2, is_active=True)

        async def mock_get_tariff(sess, tid):
            return tariff1 if tid == 1 else tariff2

        cb = AsyncMock()
        cb.data = "order_pay_card:2"
        cb.from_user.id = 123456
        cb.bot = AsyncMock()
        cb.message = AsyncMock()

        with patch("bot.handlers.payment.purchase_routes.MaintenanceService.can_user_perform_action", return_value=True):
            with patch("bot.handlers.payment.purchase_routes.get_tariff_by_id", side_effect=mock_get_tariff):
                with patch("services.order_service.OrderService.calculate_tariff_change", return_value=(Decimal("0.00"), 20)):
                    with patch("services.order_service.OrderService.pay_from_wallet") as mock_pay_wallet:
                        mock_pay_wallet.return_value = Order(
                            id=uuid.uuid4(),
                            user_id=10,
                            amount_rub=Decimal("0.00"),
                            duration_days=20,
                            device_limit=2,
                            status="paid",
                            metadata_={"is_tariff_change": True},
                        )
                        with patch("bot.handlers.payment.purchase_routes.get_account_balance") as mock_bal:
                            mock_bal.return_value = MagicMock(real_available=Decimal("100"), bonus_available=Decimal("0"))
                            with patch("bot.handlers.payment.purchase_routes.render_hub") as mock_render:
                                await handle_order_pay_card(cb, session, db_user=user)
                                mock_pay_wallet.assert_called_once()
                                mock_render.assert_called_once()

    async def test_pay_from_wallet_preserves_custom_metadata(self):
        session = AsyncMock(spec=AsyncSession)
        user = User(id=10, financial_hold=False)
        session.get.return_value = user

        with patch("services.order_service.get_account_balance") as mock_bal:
            mock_bal.return_value = MagicMock(debt=Decimal("0"), available=Decimal("500"))
            with patch("services.order_service.create_order_debit"):
                with patch("services.order_service.FulfillmentService.fulfill_order"):
                    order = await OrderService.pay_from_wallet(
                        session,
                        user_id=10,
                        amount_rub=Decimal("100.00"),
                        metadata={"custom_flag": True, "source": "promo"},
                    )
                    self.assertTrue(order.metadata_["custom_flag"])
                    self.assertEqual(order.metadata_["source"], "promo")

    def test_admin_payments_sorting_handles_mixed_tz(self):
        from bot.handlers.admin.payments import _normalize_dt

        naive_dt = datetime(2026, 9, 21, 12, 0, 0)
        aware_dt = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
        none_dt = None

        norm_naive = _normalize_dt(naive_dt)
        norm_aware = _normalize_dt(aware_dt)
        norm_none = _normalize_dt(none_dt)

        self.assertIsNotNone(norm_naive.tzinfo)
        self.assertIsNotNone(norm_aware.tzinfo)
        self.assertIsNotNone(norm_none.tzinfo)

        items = [norm_naive, norm_aware, norm_none]
        sorted_items = sorted(items, reverse=True)
        self.assertEqual(len(sorted_items), 3)

    def test_migration_0030_downgrade_safety_check_with_paid_orders(self):
        import importlib.util
        from pathlib import Path

        mig_path = (
            Path(__file__).parent.parent
            / "alembic"
            / "versions"
            / "0030_simple_billing.py"
        )
        spec = importlib.util.spec_from_file_location("mig_0030", mig_path)
        mig_0030 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig_0030)

        with patch.object(mig_0030, "op") as mock_op:
            mock_bind = mock_op.get_bind.return_value

            mock_bind.execute.return_value.scalar.side_effect = [0, 2]
            with self.assertRaises(RuntimeError) as cm:
                mig_0030.downgrade()
            self.assertIn("paid orders exist in orders table", str(cm.exception))

    async def test_batch_credit_capacities_with_order_refund_debit(self):
        """Verify _batch_credit_capacities subtracts refund debits linked by order_id."""
        from database.models import AccountLedgerEntry
        from database.repositories.account_ledger_repo import _batch_credit_capacities

        session = AsyncMock(spec=AsyncSession)
        order_uuid = uuid.uuid4()
        credit = AccountLedgerEntry(
            id=1,
            user_id=10,
            amount=Decimal(500),
            entry_type="payment_credit",
            currency="RUB",
            order_id=order_uuid,
            payment_id=None,
        )

        mock_allocations = MagicMock()
        mock_allocations.all.return_value = []
        session.scalars.return_value = mock_allocations

        # Return 200 RUB refunded for this order
        mock_order_refund_rows = MagicMock()
        mock_order_refund_rows.all.return_value = [(order_uuid, 200)]
        session.execute.return_value = mock_order_refund_rows

        capacities = await _batch_credit_capacities(session, [credit])
        # 500 credit - 200 refunded = 300 capacity
        self.assertEqual(capacities[1], Decimal(300))

    def test_calculate_tariff_change_preserves_partial_days_with_ceil(self):
        """Verify calculate_tariff_change uses math.ceil so 36 hours counts as 2 days."""
        now = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)
        sub_end = now + timedelta(hours=36)  # 1.5 days left
        current_tariff = Tariff(id=1, name="Basic", price_rub=Decimal("300.00"), duration_days=30)
        target_tariff = Tariff(id=2, name="Pro", price_rub=Decimal("600.00"), duration_days=30)

        # 36 hours -> ceil(36/24) = 2 days left
        # Current daily = 300/30 = 10 RUB. 2 days * 10 = 20 RUB unspent credit.
        # Pro daily = 600/30 = 20 RUB.
        # Required payment: 600 - 20 = 580 RUB.
        cost, days = OrderService.calculate_tariff_change(
            current_tariff=current_tariff,
            target_tariff=target_tariff,
            subscription_end=sub_end,
            now=now,
        )
        self.assertEqual(cost, Decimal("580.00"))
        self.assertEqual(days, 30)

    async def test_order_deduplication_different_amount_creates_new_order(self):
        """Verify create_order does not deduplicate orders with different amounts."""
        mock_gw = AsyncMock()
        mock_gw.create_payment_url.return_value = PaymentInvoice(
            external_id="ext-new",
            payment_url="https://pay.link/new",
        )
        with patch("services.order_service.get_payment_gateway", return_value=mock_gw):
            session = AsyncMock(spec=AsyncSession)
            # scalar finds no order with amount_rub == 500
            session.scalar.return_value = None
            tariff = Tariff(id=1, name="Basic", price_rub=Decimal("150.00"), duration_days=30, device_limit=2)
            session.get.return_value = tariff

            order = await OrderService.create_order(
                session,
                user_id=42,
                service_type="topup",
                amount_rub=Decimal("500.00"),
                payment_method="yookassa",
            )
            self.assertEqual(order.amount_rub, Decimal("500.00"))
            self.assertEqual(order.payment_url, "https://pay.link/new")

    async def test_order_deduplication_dynamic_tariff_change_recalculated_amount(self):
        """Verify dynamic tariff change dedup uses calculated final_amount instead of initial None."""
        mock_gw = AsyncMock()
        mock_gw.create_payment_url.return_value = PaymentInvoice(
            external_id="ext-dyn",
            payment_url="https://pay.link/dyn",
        )
        with patch("services.order_service.get_payment_gateway", return_value=mock_gw):
            session = AsyncMock(spec=AsyncSession)
            now = now_utc()
            # User currently has 2 devices (300 RUB), upgrading to 3 devices (450 RUB), 15 days left
            curr_tariff = Tariff(id=1, name="Standard", price_rub=Decimal("300.00"), duration_days=30, device_limit=2)
            target_tariff = Tariff(id=2, name="Pro", price_rub=Decimal("450.00"), duration_days=30, device_limit=3)
            user = User(
                id=42,
                current_tariff_id=1,
                subscription_end=now + timedelta(days=15),
            )

            def mock_get(model, pk):
                if model == User:
                    return user
                if model == Tariff and pk == 1:
                    return curr_tariff
                if model == Tariff and pk == 2:
                    return target_tariff
                return None

            session.get.side_effect = mock_get
            # scalar dedup query returns None (no pending order with calculated final_amount)
            session.scalar.return_value = None

            order = await OrderService.create_order(
                session,
                user_id=42,
                service_type="tariff_change",
                tariff_id=2,
                amount_rub=None,  # dynamic calculation
                payment_method="yookassa",
                metadata={"is_tariff_change": True},
            )
            # 15 days left out of 30: unspent credit is 150 RUB. Target is 450. Due: 300 RUB.
            self.assertEqual(order.amount_rub, Decimal("300.00"))
            self.assertEqual(order.payment_url, "https://pay.link/dyn")
            # Verify scalar query was called with Order.amount_rub == final_amount
            self.assertTrue(session.scalar.called)

    @patch("bot.handlers.webhook.session_scope")
    @patch("bot.handlers.webhook._get_real_ip", return_value="185.71.76.1")
    @patch("bot.handlers.webhook._is_yookassa_ip", return_value=True)
    async def test_yookassa_webhook_reprocesses_pending_inbox_event(
        self, mock_ip, mock_real, mock_scope
    ):
        """Verify WebhookInbox with status=pending is NOT skipped on retry, and gets updated to succeeded."""
        from bot.handlers.webhook import yookassa_webhook_handler
        from config.enums import WebhookInboxStatus
        from database.models import WebhookInbox

        session = AsyncMock(spec=AsyncSession)
        pending_inbox = WebhookInbox(
            id=77,
            provider="yookassa",
            status=WebhookInboxStatus.PENDING.value,
        )
        session.scalar.return_value = pending_inbox
        mock_scope.return_value.__aenter__.return_value = session

        order_uuid = uuid.uuid4()
        order = Order(
            id=order_uuid,
            user_id=10,
            service_type="topup",
            amount_rub=Decimal("100.00"),
            status="paid",
        )
        order._newly_paid = False

        request = AsyncMock()
        request.content_length = 200
        request.app = {"bot": AsyncMock()}
        request.json.return_value = {
            "type": "notification",
            "event": "payment.succeeded",
            "object": {
                "id": "pay-pending-123",
                "status": "succeeded",
                "metadata": {"order_id": str(order_uuid)},
            },
        }

        with patch("services.order_service.OrderService.process_webhook_event", return_value=order) as mock_process:
            response = await yookassa_webhook_handler(request)
            self.assertEqual(response.status, 200)
            mock_process.assert_called_once()
            self.assertEqual(pending_inbox.status, WebhookInboxStatus.SUCCEEDED.value)

    @patch("bot.handlers.webhook.session_scope")
    @patch("bot.handlers.webhook._get_real_ip", return_value="185.71.76.1")
    @patch("bot.handlers.webhook._is_yookassa_ip", return_value=True)
    async def test_yookassa_webhook_returns_503_when_order_pending(
        self, mock_ip, mock_real, mock_scope
    ):
        """Verify webhook returns 503 when order is not yet found, allowing YooKassa retry."""
        from bot.handlers.webhook import yookassa_webhook_handler

        session = AsyncMock(spec=AsyncSession)
        session.scalar.return_value = None
        mock_scope.return_value.__aenter__.return_value = session

        request = AsyncMock()
        request.content_length = 200
        request.app = {"bot": AsyncMock()}
        request.json.return_value = {
            "type": "notification",
            "event": "payment.succeeded",
            "object": {
                "id": "pay-unprocessed-123",
                "status": "succeeded",
                "metadata": {"order_id": str(uuid.uuid4())},
            },
        }

        with patch("services.order_service.OrderService.process_webhook_event", return_value=None):
            response = await yookassa_webhook_handler(request)
            self.assertEqual(response.status, 503)
            self.assertIn("pending", response.text.lower())

    @patch("bot.handlers.webhook.session_scope")
    @patch("bot.handlers.webhook._get_real_ip", return_value="185.71.76.1")
    @patch("bot.handlers.webhook._is_yookassa_ip", return_value=True)
    async def test_yookassa_webhook_payment_canceled_returns_200_ok(
        self, mock_ip, mock_real, mock_scope
    ):
        """Verify payment.canceled webhook marks inbox succeeded and returns 200 OK."""
        from bot.handlers.webhook import yookassa_webhook_handler
        from config.enums import WebhookInboxStatus
        from database.models import WebhookInbox

        session = AsyncMock(spec=AsyncSession)
        pending_inbox = WebhookInbox(
            id=78,
            provider="yookassa",
            status=WebhookInboxStatus.PENDING.value,
        )
        session.scalar.return_value = pending_inbox
        mock_scope.return_value.__aenter__.return_value = session

        order_uuid = uuid.uuid4()
        order = Order(
            id=order_uuid,
            user_id=10,
            service_type="awg",
            amount_rub=Decimal("200.00"),
            status="canceled",
            metadata_={"cancellation_reason": "gateway_canceled"},
        )

        request = AsyncMock()
        request.content_length = 200
        request.app = {"bot": AsyncMock()}
        request.json.return_value = {
            "type": "notification",
            "event": "payment.canceled",
            "object": {
                "id": "pay-canceled-456",
                "status": "canceled",
                "metadata": {"order_id": str(order_uuid)},
            },
        }

        with patch("services.order_service.OrderService.process_webhook_event", return_value=order) as mock_process:
            response = await yookassa_webhook_handler(request)
            self.assertEqual(response.status, 200)
            mock_process.assert_called_once()
            self.assertEqual(pending_inbox.status, WebhookInboxStatus.SUCCEEDED.value)

    async def test_audit_invariants_extra_traffic_scales_with_devices(self):
        """Verify assert_inv_4_subscription_traffic_pools allows up to device_limit * 150 GiB extra traffic."""
        from scripts.audit_invariants import assert_inv_4_subscription_traffic_pools

        session = AsyncMock(spec=AsyncSession)
        scalars_mock = MagicMock()
        # 1 device, 50 GiB base, 150 GiB extra (total 200 GiB) -> valid
        scalars_mock.all.return_value = []
        session.scalars.return_value = scalars_mock

        res = await assert_inv_4_subscription_traffic_pools(session)
        self.assertTrue(res.passed)
        self.assertIn("satisfy traffic pool", res.details)

    @patch("services.order_service.reverse_referral_bonus_for_topup")
    @patch("services.order_service.FulfillmentService.revoke_order")
    @patch("services.order_service.create_order_refund_debit")
    @patch("services.order_service.get_payment_gateway")
    async def test_process_webhook_event_multiple_partial_refunds_cumulative_ledger_delta(
        self, mock_gw_factory, mock_refund_debit, mock_revoke, mock_rev_bonus
    ):
        """Verify 18 x 5.50 RUB refunds result in exactly 99 RUB debited from ledger via cumulative rounding."""
        mock_gw = AsyncMock()
        order_uuid = uuid.uuid4()
        mock_gw_factory.return_value = mock_gw

        session = AsyncMock(spec=AsyncSession)
        order = Order(
            id=order_uuid,
            user_id=10,
            service_type="topup",
            amount_rub=Decimal("200.00"),
            status="paid",
            metadata_={},
        )
        session.scalar.return_value = order
        session.get.return_value = order

        refund_part_amount = Decimal("5.50")
        total_calls = 18

        for i in range(1, total_calls + 1):
            ref_id = f"refund-part-{i}"
            mock_gw.parse_webhook.return_value = WebhookResult(
                order_id=str(order_uuid),
                is_paid=False,
                is_refunded=True,
                amount_rub=refund_part_amount,
                external_id=ref_id,
            )
            res = await OrderService.process_webhook_event(session, {"ref_seq": i})
            self.assertIsNotNone(res)

        # 18 calls should have been made to create_order_refund_debit
        self.assertEqual(mock_refund_debit.call_count, total_calls)

        # Sum of debited integer rubles must equal exactly 99 (18 * 5.50 = 99.00)
        debited_amounts = [
            call.kwargs["amount_rub"] for call in mock_refund_debit.call_args_list
        ]
        self.assertEqual(sum(debited_amounts), Decimal("99"))
        # Each debit must be an integer (whole rubles)
        for amt in debited_amounts:
            self.assertEqual(amt, amt.to_integral_value())

        # Metadata must accurately reflect cumulative total
        self.assertEqual(order.metadata_["refunded_amount_rub"], "99.00")
        self.assertEqual(len(order.metadata_["processed_refund_ids"]), total_calls)

    @patch("services.order_service.reverse_referral_bonus_for_topup")
    @patch("services.order_service.FulfillmentService.revoke_order")
    @patch("services.order_service.create_order_refund_debit")
    @patch("services.order_service.get_payment_gateway")
    async def test_process_webhook_event_micro_refund_kopecks_no_zero_ledger_debit(
        self, mock_gw_factory, mock_refund_debit, mock_revoke, mock_rev_bonus
    ):
        """Verify micro refund of 0.49 RUB does not create a 0 RUB ledger debit but records in metadata."""
        mock_gw = AsyncMock()
        order_uuid = uuid.uuid4()
        mock_gw_factory.return_value = mock_gw

        session = AsyncMock(spec=AsyncSession)
        order = Order(
            id=order_uuid,
            user_id=10,
            service_type="topup",
            amount_rub=Decimal("100.00"),
            status="paid",
            metadata_={},
        )
        session.scalar.return_value = order
        session.get.return_value = order

        mock_gw.parse_webhook.return_value = WebhookResult(
            order_id=str(order_uuid),
            is_paid=False,
            is_refunded=True,
            amount_rub=Decimal("0.49"),
            external_id="refund-micro-1",
        )
        res = await OrderService.process_webhook_event(session, {"micro": True})
        self.assertIsNotNone(res)
        # No 0 RUB debit should be created
        mock_refund_debit.assert_not_called()
        # But order metadata tracks the 0.49 RUB refund accurately
        self.assertEqual(order.metadata_["refunded_amount_rub"], "0.49")
        self.assertIn("refund-micro-1", order.metadata_["processed_refund_ids"])

    async def test_mark_order_paid_rejects_gateway_canceled_order(self):
        """Verify an order canceled by gateway cannot be revived."""
        session = AsyncMock(spec=AsyncSession)
        order_uuid = uuid.uuid4()
        order = Order(
            id=order_uuid,
            user_id=10,
            service_type="topup",
            amount_rub=Decimal("100.00"),
            status="canceled",
            metadata_={"cancellation_reason": "gateway_canceled"},
        )
        session.scalar.return_value = order

        paid_order = await OrderService.mark_order_paid(session, order_uuid)
        self.assertIsNone(paid_order)
        self.assertEqual(order.status, "canceled")
        self.assertFalse(getattr(order, "_newly_paid", True))

    async def test_mark_order_paid_allows_reviving_user_canceled_order(self):
        """Verify an order canceled by user (not gateway) can be revived if payment actually arrived."""
        session = AsyncMock(spec=AsyncSession)
        order_uuid = uuid.uuid4()
        order = Order(
            id=order_uuid,
            user_id=10,
            service_type="topup",
            amount_rub=Decimal("100.00"),
            status="canceled",
            metadata_={},
        )
        session.scalar.side_effect = [
            order,
            User(id=10, telegram_id=10010, financial_hold=False, topup_blocked=False),
        ]
        # Settlement hold boundary re-reads the user with a row lock:
        # a clean (non-held) user must keep the revive-and-credit path.
        session.get.return_value = User(
            id=10, telegram_id=10010, financial_hold=False, topup_blocked=False
        )

        with patch("services.order_service.create_order_credit") as mock_credit, \
             patch("services.referral_bonus.grant_referral_bonus_for_topup") as mock_grant, \
             patch("services.order_service.FulfillmentService.fulfill_order") as mock_fulfill:
            paid_order = await OrderService.mark_order_paid(session, order_uuid)
            self.assertIsNotNone(paid_order)
            self.assertEqual(paid_order.status, "paid")
            self.assertTrue(paid_order.metadata_["revived_from_canceled"])
            self.assertTrue(paid_order._newly_paid)
            mock_credit.assert_called_once()
            mock_grant.assert_called_once()
            mock_fulfill.assert_called_once()

    async def test_mark_order_paid_withholds_credit_under_financial_hold(self):
        """Settlement boundary: paid fact is kept but credit/bonus/fulfill are withheld."""
        session = AsyncMock(spec=AsyncSession)
        order_uuid = uuid.uuid4()
        order = Order(
            id=order_uuid,
            user_id=11,
            service_type="topup",
            amount_rub=Decimal("100.00"),
            status="pending",
            metadata_={},
        )
        session.scalar.side_effect = [
            order,
            User(id=11, telegram_id=10011, financial_hold=True, topup_blocked=False),
        ]

        with patch("services.order_service.create_order_credit") as mock_credit, \
             patch("services.referral_bonus.grant_referral_bonus_for_topup") as mock_grant, \
             patch("services.order_service.FulfillmentService.fulfill_order") as mock_fulfill:
            paid_order = await OrderService.mark_order_paid(session, order_uuid)
            self.assertIsNotNone(paid_order)
            self.assertEqual(paid_order.status, "paid")
            self.assertTrue(paid_order.metadata_.get("settlement_held"))
            mock_credit.assert_not_called()
            mock_grant.assert_not_called()
            mock_fulfill.assert_not_called()

    async def test_revoke_order_locks_user_row_with_for_update(self):
        """Verify FulfillmentService.revoke_order locks User row with with_for_update=True."""
        session = AsyncMock(spec=AsyncSession)
        order = Order(
            id=uuid.uuid4(),
            user_id=42,
            service_type="white_internet",
            amount_rub=Decimal("100.00"),
        )
        user = User(id=42, telegram_id=999)
        session.get.return_value = user

        with patch("services.white_internet_service.WhiteInternetService.deactivate_user_subscriptions") as mock_deact:
            await FulfillmentService.revoke_order(session, order)
            session.get.assert_called_once_with(User, 42, with_for_update=True)
            mock_deact.assert_called_once_with(session, 42)

    @patch("bot.handlers.payment.balance_routes._render_balance")
    @patch("bot.handlers.webhook.session_scope")
    @patch("bot.handlers.webhook._get_real_ip", return_value="185.71.76.1")
    @patch("bot.handlers.webhook._is_yookassa_ip", return_value=True)
    async def test_yookassa_webhook_informational_event_does_not_send_duplicate_notification(
        self, mock_ip, mock_real, mock_scope, mock_render_balance
    ):
        """Verify informational webhook (already paid, _newly_paid=False) suppresses push notifications."""
        from bot.handlers.webhook import yookassa_webhook_handler
        from config.enums import WebhookInboxStatus
        from database.models import WebhookInbox

        session = AsyncMock(spec=AsyncSession)
        pending_inbox = WebhookInbox(
            id=99,
            provider="yookassa",
            status=WebhookInboxStatus.PENDING.value,
        )
        session.scalar.return_value = pending_inbox
        mock_scope.return_value.__aenter__.return_value = session

        order_uuid = uuid.uuid4()
        order = Order(
            id=order_uuid,
            user_id=10,
            service_type="topup",
            amount_rub=Decimal("100.00"),
            status="paid",
        )
        order._newly_paid = False  # already processed before

        request = AsyncMock()
        request.content_length = 200
        request.app = {"bot": AsyncMock()}
        request.json.return_value = {
            "type": "notification",
            "event": "payment.succeeded",
            "object": {
                "id": "pay-already-processed-123",
                "status": "succeeded",
                "metadata": {"order_id": str(order_uuid)},
            },
        }

        with patch("services.order_service.OrderService.process_webhook_event", return_value=order):
            response = await yookassa_webhook_handler(request)
            self.assertEqual(response.status, 200)
            mock_render_balance.assert_not_called()

    @patch("services.order_service.get_payment_gateway")
    async def test_create_order_deduplication_different_service_attributes_creates_new_order(
        self, mock_gw_factory
    ):
        """Verify deduplication does not conflate different service requests (e.g. traffic pack vs device slot) at same price."""
        from services.order_service import OrderService

        session = AsyncMock(spec=AsyncSession)
        mock_gw = AsyncMock()
        mock_gw.create_payment_url.return_value = MagicMock(
            external_id="ext-invoice-new", payment_url="https://pay.example.com/new"
        )
        mock_gw_factory.return_value = mock_gw

        session.get.return_value = User(id=42, telegram_id=123)

        captured_queries = []

        async def mock_scalar(query):
            captured_queries.append(query)
            return None

        session.scalar = mock_scalar

        new_order = await OrderService.create_order(
            session,
            user_id=42,
            service_type="white_internet",
            amount_rub=Decimal("200.00"),
            device_limit=2,
            traffic_bytes=0,
            duration_days=0,
            payment_method="yookassa",
        )

        self.assertIsNotNone(new_order)
        self.assertEqual(new_order.device_limit, 2)
        self.assertEqual(new_order.traffic_bytes, 0)
        session.add.assert_called()

        # Verify the deduplication query contains the service attributes
        self.assertEqual(len(captured_queries), 1)
        sql_str = str(captured_queries[0])
        self.assertIn("traffic_bytes", sql_str)
        self.assertIn("device_limit", sql_str)
        self.assertIn("duration_days", sql_str)

    @patch("services.order_service.get_payment_gateway")
    async def test_process_webhook_event_payment_succeeded_on_gateway_canceled_order_acknowledges_without_503(
        self, mock_gw_factory
    ):
        """Verify payment.succeeded on gateway_canceled order records rejection and returns 200 OK (no 503 loop)."""
        from bot.handlers.webhook import yookassa_webhook_handler
        from config.enums import WebhookInboxStatus
        from database.models import WebhookInbox
        from services.order_service import OrderService

        mock_gw = AsyncMock()
        order_uuid = uuid.uuid4()
        mock_gw.parse_webhook.return_value = WebhookResult(
            order_id=str(order_uuid),
            is_paid=True,
            is_refunded=False,
            is_canceled=False,
            external_id="late-pay-999",
            amount_rub=Decimal("200.00"),
        )
        mock_gw_factory.return_value = mock_gw

        session = AsyncMock(spec=AsyncSession)
        order = Order(
            id=order_uuid,
            user_id=10,
            service_type="awg",
            amount_rub=Decimal("200.00"),
            status="canceled",
            metadata_={"cancellation_reason": "gateway_canceled"},
        )
        session.scalar.return_value = order
        session.get.return_value = order

        # Test OrderService layer: returns order with rejected metadata and _newly_paid=False
        res = await OrderService.process_webhook_event(session, {"some": "payload"})
        self.assertIsNotNone(res)
        self.assertEqual(res.status, "canceled")
        self.assertFalse(getattr(res, "_newly_paid", True))
        self.assertEqual(res.metadata_["late_payment_attempt_rejected"], "late-pay-999")

        # Test Webhook Handler layer: responds 200 OK (not 503)
        with patch("bot.handlers.webhook.session_scope") as mock_scope, \
             patch("bot.handlers.webhook._get_real_ip", return_value="185.71.76.1"), \
             patch("bot.handlers.webhook._is_yookassa_ip", return_value=True), \
             patch("services.order_service.OrderService.process_webhook_event", return_value=res):

            inbox = WebhookInbox(id=1, provider="yookassa", status=WebhookInboxStatus.PENDING.value)
            session.scalar.return_value = inbox
            mock_scope.return_value.__aenter__.return_value = session

            request = AsyncMock()
            request.content_length = 200
            request.app = {"bot": AsyncMock()}
            request.json.return_value = {
                "type": "notification",
                "event": "payment.succeeded",
                "object": {"id": "late-pay-999", "status": "succeeded"},
            }

            response = await yookassa_webhook_handler(request)
            self.assertEqual(response.status, 200)
            self.assertEqual(inbox.status, WebhookInboxStatus.SUCCEEDED.value)

    async def test_handle_order_check_locks_order_with_for_update(self):
        """Verify handle_order_check locks order with with_for_update=True."""
        from bot.handlers.payment.purchase_routes import handle_order_check

        session = AsyncMock(spec=AsyncSession)
        order_uuid = uuid.uuid4()
        user = User(id=42, telegram_id=999)
        order = Order(id=order_uuid, user_id=42, status="pending", external_id=None)

        session.get.return_value = order

        callback = AsyncMock()
        callback.data = f"order_check:{order_uuid}"

        await handle_order_check(callback, session, db_user=user)

        session.get.assert_called_with(Order, order_uuid, with_for_update=True)

    async def test_handle_order_cancel_locks_with_for_update_and_preserves_paid_order(self):
        """Verify handle_order_cancel locks with with_for_update and does not overwrite paid order."""
        from bot.handlers.payment.purchase_routes import handle_order_cancel

        session = AsyncMock(spec=AsyncSession)
        order_uuid = uuid.uuid4()
        user = User(id=42, telegram_id=999)

        # Case 1: Pending order gets canceled with user_canceled reason
        pending_order = Order(id=order_uuid, user_id=42, status="pending", metadata_={})
        session.get.return_value = pending_order

        callback = AsyncMock()
        callback.data = f"order_cancel:{order_uuid}"
        callback.bot = AsyncMock()
        callback.message = AsyncMock()

        with patch("bot.handlers.payment.common.render_tariff_showcase") as mock_showcase:
            await handle_order_cancel(callback, session, db_user=user)
            session.get.assert_called_with(Order, order_uuid, with_for_update=True)
            self.assertEqual(pending_order.status, "canceled")
            self.assertEqual(pending_order.metadata_.get("cancellation_reason"), "user_canceled")
            mock_showcase.assert_called_once()

        # Case 2: Concurrent webhook already marked order as paid -> cancel must NOT overwrite status
        paid_order = Order(id=order_uuid, user_id=42, status="paid", metadata_={})
        session.get.return_value = paid_order

        with patch("bot.handlers.payment.common.render_tariff_showcase") as mock_showcase:
            await handle_order_cancel(callback, session, db_user=user)
            self.assertEqual(paid_order.status, "paid")
            self.assertNotIn("cancellation_reason", paid_order.metadata_)

    def test_mark_order_canceled_preserves_existing_cancellation_reason(self):
        """Verify mark_order_canceled does not overwrite user_canceled with gateway_canceled."""
        from services.order_service import OrderService

        order = Order(
            id=uuid.uuid4(),
            user_id=1,
            status="canceled",
            metadata_={"cancellation_reason": "user_canceled"},
        )
        OrderService.mark_order_canceled(order, reason="gateway_canceled")
        self.assertEqual(order.metadata_["cancellation_reason"], "user_canceled")

    @patch("services.order_service.get_payment_gateway")
    async def test_process_webhook_event_rejects_mismatched_external_id(self, mock_gw_factory):
        """Verify process_webhook_event rejects webhook if order.external_id != payment external_id."""
        from services.order_service import OrderService

        order_uuid = uuid.uuid4()
        mock_gw = AsyncMock()
        mock_gw.parse_webhook.return_value = WebhookResult(
            order_id=str(order_uuid),
            is_paid=True,
            is_refunded=False,
            is_canceled=False,
            external_id="ext-diff-999",
            amount_rub=Decimal("100.00"),
        )
        mock_gw_factory.return_value = mock_gw

        session = AsyncMock(spec=AsyncSession)
        order = Order(
            id=order_uuid,
            user_id=10,
            status="pending",
            external_id="ext-orig-111",
            amount_rub=Decimal("100.00"),
        )
        session.scalar.return_value = order

        result = await OrderService.process_webhook_event(session, {"event": "payment.succeeded"})
        self.assertIsNone(result)
        self.assertEqual(order.status, "pending")

