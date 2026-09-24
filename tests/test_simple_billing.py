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

    @patch("integrations.payment_gateways.yookassa.YooKassaService.get_payment_result")
    async def test_check_payment_status(self, mock_get):
        mock_get.return_value = YooKassaResult(
            True,
            value={"status": "succeeded"},
        )

        status_result = await self.gateway.check_payment_status("ext-123")
        self.assertTrue(status_result.is_paid)
        self.assertFalse(status_result.is_canceled)


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
        mock_rev_bonus.assert_called_once_with(session, order_id=order_uuid)
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
        mock_rev_bonus.assert_called_once_with(session, order_id=order_uuid)
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
        mock_rev_bonus.assert_called_once_with(session, order_id=order_uuid)
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

