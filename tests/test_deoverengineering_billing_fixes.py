import unittest
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from database.models import Order, Payment, User
from database.repositories.payments_repo import (
    get_pending_payments_count_for_tariff,
    get_user_payments,
    has_successful_topup,
)
from database.repositories.users_repo import search_user_flexible
from bot import texts
from bot.handlers.payment.credit_notify import notify_referrer_bonus
from bot.handlers.payment.purchase_routes import handle_order_pay_card
from bot.handlers.referral import payment_display_status, show_history
from integrations.payment_gateways.base import (
    PaymentCreationAmbiguousError,
    WebhookResult,
)
from integrations.payment_gateways.yookassa import YooKassaGateway
from services.order_service import FinancialHoldBlockedError, OrderService
from services.yookassa_service import YooKassaResult


class TestDeoverengineeringBillingFixes(unittest.IsolatedAsyncioTestCase):
    async def test_get_user_payments_combines_orders_and_legacy_payments(self):
        """get_user_payments returns topup Orders and legacy Payments sorted desc."""
        session = AsyncMock()

        order1 = Order(
            id=uuid.uuid4(),
            user_id=10,
            service_type="topup",
            amount_rub=Decimal("400.00"),
            status="paid",
            created_at=datetime(2026, 10, 11, 10, 0, 0, tzinfo=timezone.utc),
        )
        legacy_pay = Payment(
            id=101,
            user_id=10,
            amount=Decimal("200.00"),
            provider_status="succeeded",
            created_at=datetime(2026, 10, 10, 10, 0, 0, tzinfo=timezone.utc),
        )

        mock_scalars1 = MagicMock()
        mock_scalars1.all.return_value = [order1]
        mock_scalars2 = MagicMock()
        mock_scalars2.all.return_value = [legacy_pay]
        session.scalars = AsyncMock(side_effect=[mock_scalars1, mock_scalars2])

        results = await get_user_payments(session, user_id=10, limit=5)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], order1)
        self.assertEqual(results[1], legacy_pay)

    async def test_has_successful_topup_recognizes_external_gateway_orders(self):
        """has_successful_topup returns True for paid gateway orders (sbp, yookassa, topup)."""
        session = AsyncMock()
        session.scalar = AsyncMock(side_effect=[0, 1])

        has_paid = await has_successful_topup(session, user_id=42)
        self.assertTrue(has_paid)

        call_args = session.scalar.await_args
        self.assertIsNotNone(call_args)
        stmt = str(call_args.args[0].compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("orders.payment_method != 'wallet'", stmt)
        self.assertIn("orders.service_type = 'topup'", stmt)

    async def test_get_pending_payments_count_excludes_wallet(self):
        """get_pending_payments_count_for_tariff filters Order.payment_method != 'wallet'."""
        session = AsyncMock()
        session.scalar = AsyncMock(return_value=1)

        count = await get_pending_payments_count_for_tariff(session, tariff_id=5)
        self.assertEqual(count, 1)

        # Check statement generated
        call_args = session.scalar.await_args
        self.assertIsNotNone(call_args)
        stmt = str(call_args.args[0].compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("orders.payment_method != 'wallet'", stmt)

    async def test_search_user_flexible_finds_user_by_order_uuid(self):
        """search_user_flexible finds user via Order.id UUID."""
        session = AsyncMock()
        user = User(id=42, telegram_id=999888, username="test_client")

        # get_user_by_username execute -> None
        exec_mock_user = MagicMock()
        exec_mock_user.scalar_one_or_none.return_value = None
        # WhiteInternetSubscription scalar -> None, Order.id scalar -> 42
        session.scalar = AsyncMock(side_effect=[None, 42])

        exec_mock_found = MagicMock()
        exec_mock_found.scalar_one_or_none.return_value = user
        session.execute = AsyncMock(side_effect=[exec_mock_user, exec_mock_found])

        found = await search_user_flexible(session, "226ea600-6c00-4bbc-bd77-0773e306357a")
        self.assertIsNotNone(found)
        self.assertEqual(found.id, 42)

    async def test_search_user_flexible_finds_user_by_order_external_id(self):
        """search_user_flexible finds user via Order.external_id."""
        session = AsyncMock()
        user = User(id=42, telegram_id=999888, username="test_client")

        # get_user_by_username execute -> None
        exec_mock_user = MagicMock()
        exec_mock_user.scalar_one_or_none.return_value = None
        # Peer ID -> None, Order.external_id -> 42
        session.scalar = AsyncMock(side_effect=[None, 42])

        exec_mock_found = MagicMock()
        exec_mock_found.scalar_one_or_none.return_value = user
        session.execute = AsyncMock(side_effect=[exec_mock_user, exec_mock_found])

        found = await search_user_flexible(session, "yookassa_payment_ext_id_12345")
        self.assertIsNotNone(found)
        self.assertEqual(found.id, 42)

    def test_payment_display_status_handles_order_and_payment(self):
        """payment_display_status works for both Order and Payment objects."""
        paid_order = Order(
            id=uuid.uuid4(),
            user_id=1,
            service_type="topup",
            amount_rub=Decimal("300"),
            status="paid",
        )
        self.assertEqual(payment_display_status(paid_order), "succeeded")

        canceled_order = Order(
            id=uuid.uuid4(),
            user_id=1,
            service_type="topup",
            amount_rub=Decimal("300"),
            status="canceled",
        )
        self.assertEqual(payment_display_status(canceled_order), "canceled")

        legacy_pay = Payment(
            id=5,
            user_id=1,
            amount=Decimal("150"),
            provider_status="pending",
        )
        self.assertEqual(payment_display_status(legacy_pay), "pending")

    async def test_show_history_renders_orders_and_payments(self):
        """show_history correctly formats amounts from Order.amount_rub and Payment.amount."""
        callback = AsyncMock()
        callback.data = "referral:history:1"
        callback.from_user = MagicMock(id=1001)
        callback.message = MagicMock()
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()
        state = AsyncMock()

        user = User(id=7, telegram_id=1001)
        order = Order(
            id=uuid.uuid4(),
            user_id=7,
            service_type="topup",
            amount_rub=Decimal("500.00"),
            status="paid",
            created_at=datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc),
        )
        payment = Payment(
            id=12,
            user_id=7,
            amount=Decimal("250.00"),
            provider_status="succeeded",
            created_at=datetime(2026, 10, 11, 8, 0, tzinfo=timezone.utc),
        )

        callback.bot = AsyncMock()
        callback.message.chat = MagicMock()
        callback.message.chat.id = 12345
        callback.message.message_id = 999

        session = AsyncMock()
        with patch("bot.handlers.referral.get_user_payments", new=AsyncMock(return_value=[order, payment])), \
             patch("bot.handlers.referral.render_hub", new=AsyncMock()) as mock_hub:
            await show_history(callback, state, session, db_user=user)

        mock_hub.assert_awaited_once()
        text = mock_hub.await_args.args[2]
        self.assertIn("500", text)
        self.assertIn("250", text)

    async def test_notify_referrer_bonus_sends_instant_push(self):
        """notify_referrer_bonus delivers immediate push notification to referrer."""
        bot = AsyncMock()
        session = AsyncMock()

        ref_order = Order(
            id=uuid.uuid4(),
            user_id=99,
            service_type="topup",
            amount_rub=Decimal("1000.00"),
            status="paid",
            metadata_={
                "referrer_notify_pending": {
                    "telegram_id": 777777,
                    "bonus": 100,
                    "tier_upgraded": False,
                }
            },
        )

        with patch("services.workers.credit_notifications._send_referrer_push", new=AsyncMock(return_value=True)) as mock_push:
            sent = await notify_referrer_bonus(bot, session, ref_order)

        self.assertTrue(sent)
        mock_push.assert_awaited_once_with(
            bot,
            {"telegram_id": 777777, "bonus": 100, "tier_upgraded": False},
        )
        self.assertNotIn("referrer_notify_pending", ref_order.metadata_ or {})

    async def test_order_service_create_order_failure_rolls_back_and_raises(self):
        """OrderService.create_order rolls back transaction and re-raises on gateway error."""
        session = AsyncMock()
        session.add = MagicMock()
        session.rollback = AsyncMock()
        session.scalar = AsyncMock(return_value=None)

        with patch("services.order_service.get_payment_gateway") as mock_gw:
            gateway = AsyncMock()
            gateway.create_payment_url.side_effect = RuntimeError("YooKassa gateway offline")
            mock_gw.return_value = gateway

            with self.assertRaises(RuntimeError):
                await OrderService.create_order(
                    session,
                    user_id=1,
                    service_type="topup",
                    amount_rub=Decimal("300"),
                    payment_method="yookassa",
                )

        session.rollback.assert_awaited_once()

    async def test_order_service_process_webhook_refund_immediate_transition(self):
        """Refund webhook immediately sets status='refunded' and debits whole rubles."""
        session = AsyncMock()
        order_uuid = uuid.uuid4()
        topup_order = Order(
            id=order_uuid,
            user_id=15,
            service_type="topup",
            amount_rub=Decimal("400.00"),
            status="paid",
            metadata_={},
        )
        session.scalar = AsyncMock(return_value=topup_order)
        session.get = AsyncMock(return_value=topup_order)

        webhook_res = WebhookResult(
            order_id=str(order_uuid),
            is_paid=False,
            is_refunded=True,
            amount_rub=Decimal("400.00"),
            external_id="rf-12345",
        )

        with (
            patch("services.order_service.get_payment_gateway") as mock_gw,
            patch("services.order_service.create_order_refund_debit", new=AsyncMock()) as mock_debit,
            patch("services.order_service.reverse_referral_bonus_for_topup", new=AsyncMock()) as mock_rev_bonus,
        ):
            gw = AsyncMock()
            gw.parse_webhook.return_value = webhook_res
            mock_gw.return_value = gw

            success = await OrderService.process_webhook_event(session, {"event": "refund.succeeded"})

        self.assertTrue(success)
        self.assertEqual(topup_order.status, "refunded")
        self.assertIsNotNone(topup_order.refunded_at)
        mock_debit.assert_awaited_once_with(
            session,
            user_id=15,
            amount_rub=Decimal("400.00"),
            order_id=order_uuid,
            refund_id="rf-12345",
            metadata={"source": "yookassa_refund"},
        )
        mock_rev_bonus.assert_awaited_once()

    async def test_order_service_create_order_ambiguous_failure_persists_order(self):
        """PaymentCreationAmbiguousError commits order in pending status with flag."""
        session = AsyncMock()
        session.commit = AsyncMock()
        session.rollback = AsyncMock()
        session.scalar = AsyncMock(return_value=None)
        added_orders = []
        session.add = MagicMock(side_effect=lambda o: added_orders.append(o))

        with patch("services.order_service.get_payment_gateway") as mock_gw:
            gw = AsyncMock()
            gw.create_payment_url.side_effect = PaymentCreationAmbiguousError("YooKassa timeout")
            mock_gw.return_value = gw

            with self.assertRaises(PaymentCreationAmbiguousError):
                await OrderService.create_order(
                    session,
                    user_id=1,
                    service_type="topup",
                    amount_rub=Decimal("300"),
                    payment_method="yookassa",
                )

        session.commit.assert_awaited_once()
        session.rollback.assert_not_awaited()
        self.assertEqual(len(added_orders), 1)
        self.assertEqual(added_orders[0].status, "pending")
        self.assertTrue(added_orders[0].metadata_.get("payment_creation_ambiguous"))

    async def test_yookassa_gateway_raises_ambiguous_error_on_result_ambiguous(self):
        """YooKassaGateway raises PaymentCreationAmbiguousError when ambiguous=True."""
        gw = YooKassaGateway()
        fake_res = YooKassaResult(
            ok=False,
            ambiguous=True,
            error_kind="timeout",
            status_code=504,
        )
        with patch("services.yookassa_service.YooKassaService.create_payment_result", new=AsyncMock(return_value=fake_res)):
            with self.assertRaises(PaymentCreationAmbiguousError):
                await gw.create_payment_url(
                    order_id=str(uuid.uuid4()),
                    amount_rub=Decimal("100"),
                    description="test",
                    return_url="https://t.me/testbot",
                    bot_username="testbot",
                )

    async def test_mark_order_paid_releases_legacy_settlement_held(self):
        """mark_order_paid releases settlement_held and invokes _settle_benefits."""
        session = AsyncMock()
        order_uuid = uuid.uuid4()
        held_order = Order(
            id=order_uuid,
            user_id=22,
            service_type="awg",
            amount_rub=Decimal("180.00"),
            status="paid",
            metadata_={"settlement_held": True, "settlement_hold_reason": "hold"},
        )
        session.scalar = AsyncMock(return_value=held_order)
        session.get = AsyncMock(return_value=User(id=22, financial_hold=False, topup_blocked=False))

        with patch.object(OrderService, "_settle_benefits", new=AsyncMock()) as mock_settle:
            res = await OrderService.mark_order_paid(session, order_uuid)

        self.assertIsNotNone(res)
        self.assertNotIn("settlement_held", res.metadata_ or {})
        mock_settle.assert_awaited_once_with(session, held_order, was_canceled=False)
        self.assertTrue(res._newly_paid)

    async def test_mark_order_paid_keeps_settlement_held_if_user_still_under_hold(self):
        """mark_order_paid keeps settlement_held if user is still under financial_hold."""
        session = AsyncMock()
        order_uuid = uuid.uuid4()
        held_order = Order(
            id=order_uuid,
            user_id=22,
            service_type="awg",
            amount_rub=Decimal("180.00"),
            status="paid",
            metadata_={"settlement_held": True, "settlement_hold_reason": "hold"},
        )
        session.scalar = AsyncMock(return_value=held_order)
        session.get = AsyncMock(return_value=User(id=22, financial_hold=True))

        with patch.object(OrderService, "_settle_benefits", new=AsyncMock()) as mock_settle:
            res = await OrderService.mark_order_paid(session, order_uuid)

        self.assertIsNotNone(res)
        self.assertTrue((res.metadata_ or {}).get("settlement_held"))
        mock_settle.assert_not_awaited()

    async def test_mark_order_paid_withholds_new_settlement_under_financial_hold(self):
        """mark_order_paid sets settlement_held and skips fulfillment if fresh payment arrives under hold."""
        session = AsyncMock()
        order_uuid = uuid.uuid4()
        fresh_order = Order(
            id=order_uuid,
            user_id=33,
            service_type="awg",
            amount_rub=Decimal("200.00"),
            status="pending",
        )
        session.scalar = AsyncMock(return_value=fresh_order)
        session.get = AsyncMock(return_value=User(id=33, financial_hold=True))

        with patch.object(OrderService, "_settle_benefits", new=AsyncMock()) as mock_settle:
            res = await OrderService.mark_order_paid(session, order_uuid)

        self.assertIsNotNone(res)
        self.assertEqual(res.status, "paid")
        self.assertTrue((res.metadata_ or {}).get("settlement_held"))
        self.assertEqual((res.metadata_ or {}).get("settlement_hold_reason"), "financial_hold")
        self.assertFalse(res._newly_paid)
        mock_settle.assert_not_awaited()

    async def test_pay_from_wallet_blocks_financial_hold(self):
        """pay_from_wallet raises FinancialHoldBlockedError if user.financial_hold is True."""
        session = AsyncMock()
        user = User(id=5, telegram_id=555, financial_hold=True)
        session.get = AsyncMock(return_value=user)

        with self.assertRaises(FinancialHoldBlockedError):
            await OrderService.pay_from_wallet(session, user_id=5)

    async def test_handle_order_pay_card_blocks_financial_hold(self):
        """handle_order_pay_card answers with PAYMENT_DISPUTE_BLOCKED_NOTICE if financial_hold."""
        cb = AsyncMock()
        cb.from_user = MagicMock(id=999)
        cb.data = "order_pay_card:2"
        session = AsyncMock()
        db_user = User(id=99, telegram_id=999, financial_hold=True)

        with patch("bot.handlers.payment.purchase_routes.MaintenanceService.can_user_perform_action", new=AsyncMock(return_value=True)):
            await handle_order_pay_card(cb, session, db_user=db_user)

        cb.answer.assert_awaited_once_with(texts.PAYMENT_DISPUTE_BLOCKED_NOTICE, show_alert=True)

    async def test_show_order_card_displays_ambiguous_diagnostics(self):
        """show_order_card renders ADMIN_ORDER_DIAGNOSTICS_AMBIGUOUS when order is ambiguous."""
        from bot.handlers.admin.payments import show_order_card

        session = AsyncMock()
        test_uuid = uuid.uuid4()
        user = User(id=30, telegram_id=777888, username="ambiguous_buyer")
        order = Order(
            id=test_uuid,
            user_id=30,
            service_type="topup",
            amount_rub=Decimal("300.00"),
            duration_days=0,
            status="pending",
            payment_method="yookassa",
            payment_url=None,
            created_at=datetime.now(timezone.utc),
            user=user,
            tariff=None,
            metadata_={"payment_creation_ambiguous": True},
        )
        session.scalar = AsyncMock(return_value=order)

        callback = AsyncMock()
        callback.data = f"admin_order_card:{test_uuid}"
        callback.from_user = MagicMock(id=1)
        callback.message = AsyncMock()
        callback.message.edit_text = AsyncMock()
        state = AsyncMock()

        with patch("bot.handlers.admin.payments.is_admin", return_value=True):
            await show_order_card(callback, state, session)

        callback.message.edit_text.assert_awaited_once()
        text = callback.message.edit_text.await_args.args[0]
        self.assertIn("Тайм-аут шлюза при создании", text)

    async def test_balance_routes_blocks_financial_hold(self):
        """choose_topup_amount and resume_topup render PAYMENT_DISPUTE_BLOCKED_NOTICE if financial_hold."""
        from bot.handlers.payment.balance_routes import (
            choose_topup_amount,
            resume_topup,
        )

        session = AsyncMock()
        db_user = User(id=40, telegram_id=888999, financial_hold=True)

        cb1 = AsyncMock()
        cb1.from_user = MagicMock(id=888999)
        cb1.message = MagicMock(chat=MagicMock(id=888999))
        state = AsyncMock()

        with patch("bot.handlers.payment.balance_routes.MaintenanceService.can_user_perform_action", new=AsyncMock(return_value=True)), \
             patch("bot.handlers.payment.balance_routes.render_hub", new=AsyncMock()) as mock_hub:
            await choose_topup_amount(cb1, state, session, db_user=db_user)
            mock_hub.assert_awaited_once()
            self.assertEqual(mock_hub.await_args.args[2], texts.PAYMENT_DISPUTE_BLOCKED_NOTICE)

        cb2 = AsyncMock()
        cb2.from_user = MagicMock(id=888999)
        cb2.message = MagicMock(chat=MagicMock(id=888999))

        with patch("bot.handlers.payment.balance_routes.render_hub", new=AsyncMock()) as mock_hub2:
            await resume_topup(cb2, session, db_user=db_user)
            mock_hub2.assert_awaited_once()
    async def test_load_settlement_user_uses_for_update(self):
        """OrderService._load_settlement_user builds query with with_for_update."""
        session = AsyncMock()
        session.scalar = AsyncMock(return_value=User(id=10, telegram_id=123))

        user = await OrderService._load_settlement_user(session, user_id=10)
        self.assertIsNotNone(user)
        self.assertEqual(user.id, 10)

        call_args = session.scalar.await_args
        self.assertIsNotNone(call_args)
        stmt = str(call_args.args[0].compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("users.id = 10", stmt)
        self.assertIn("FOR UPDATE", stmt)

    async def test_handle_order_check_held_order_answers_blocked_notice(self):
        """handle_order_check for paid held order answers with PAYMENT_DISPUTE_BLOCKED_NOTICE."""
        from bot.handlers.payment.purchase_routes import handle_order_check

        session = AsyncMock()
        db_user = User(id=77, telegram_id=777)
        order_uuid = uuid.uuid4()
        held_order = Order(
            id=order_uuid,
            user_id=77,
            service_type="topup",
            amount_rub=Decimal("300.00"),
            status="paid",
            metadata_={"settlement_held": True, "settlement_hold_reason": "financial_hold"},
        )
        session.get = AsyncMock(return_value=held_order)

        callback = AsyncMock()
        callback.data = f"order_check:{order_uuid}"
        callback.bot = AsyncMock()
        callback.message = MagicMock(chat=MagicMock(id=777))

        with patch.object(OrderService, "mark_order_paid", new=AsyncMock(return_value=held_order)), \
             patch("bot.handlers.payment.balance_routes._render_balance", new=AsyncMock()) as mock_render:
            await handle_order_check(callback, session, db_user=db_user)

        callback.answer.assert_awaited_once_with(
            texts.PAYMENT_DISPUTE_BLOCKED_NOTICE, show_alert=True
        )
        mock_render.assert_not_awaited()

    async def test_handle_order_check_fresh_paid_with_settlement_held_answers_blocked_notice(self):
        """handle_order_check when gateway reports paid but order gets held answers blocked notice."""
        from bot.handlers.payment.purchase_routes import handle_order_check

        session = AsyncMock()
        db_user = User(id=88, telegram_id=888)
        order_uuid = uuid.uuid4()
        pending_order = Order(
            id=order_uuid,
            user_id=88,
            service_type="awg",
            amount_rub=Decimal("200.00"),
            status="pending",
            external_id="gateway_ext_888",
            payment_method="yookassa",
            metadata_={},
        )
        held_order = Order(
            id=order_uuid,
            user_id=88,
            service_type="awg",
            amount_rub=Decimal("200.00"),
            status="paid",
            external_id="gateway_ext_888",
            payment_method="yookassa",
            metadata_={"settlement_held": True, "settlement_hold_reason": "financial_hold"},
        )
        session.get = AsyncMock(return_value=pending_order)

        callback = AsyncMock()
        callback.data = f"order_check:{order_uuid}"
        callback.bot = AsyncMock()
        callback.message = MagicMock(chat=MagicMock(id=888))

        mock_gateway = AsyncMock()
        mock_gateway.check_payment_status = AsyncMock(
            return_value=MagicMock(is_paid=True, amount_rub=Decimal("200.00"))
        )

        with patch("bot.handlers.payment.purchase_routes.get_payment_gateway", return_value=mock_gateway), \
             patch.object(OrderService, "mark_order_paid", new=AsyncMock(return_value=held_order)), \
             patch("bot.handlers.payment.purchase_routes.render_hub", new=AsyncMock()) as mock_hub:
            await handle_order_check(callback, session, db_user=db_user)

        callback.answer.assert_awaited_once_with(
            texts.PAYMENT_DISPUTE_BLOCKED_NOTICE, show_alert=True
        )
        mock_hub.assert_not_awaited()

    async def test_refund_webhook_skips_revocation_if_settlement_held(self):
        """process_webhook_event does not revoke benefits on full refund if settlement was held."""
        session = AsyncMock()
        order_uuid = uuid.uuid4()
        held_order = Order(
            id=order_uuid,
            user_id=50,
            service_type="awg",
            amount_rub=Decimal("300.00"),
            status="paid",
            payment_method="yookassa",
            external_id="ext_refund_held_1",
            metadata_={"settlement_held": True},
        )
        session.scalar = AsyncMock(return_value=held_order)

        result = WebhookResult(
            order_id=None,
            is_paid=False,
            is_refunded=True,
            external_id="refund_ext_1",
            related_external_id="ext_refund_held_1",
            amount_rub=Decimal("300.00"),
            event_type="refund.succeeded",
        )
        mock_gateway = AsyncMock()
        mock_gateway.parse_webhook = AsyncMock(return_value=result)

        with patch("services.order_service.get_payment_gateway", return_value=mock_gateway), \
             patch("services.fulfillment_service.FulfillmentService.revoke_order", new=AsyncMock()) as mock_revoke, \
             patch("services.order_service.create_order_refund_debit", new=AsyncMock()), \
             patch("services.order_service.reverse_referral_bonus_for_topup", new=AsyncMock()):
            order = await OrderService.process_webhook_event(session, {})

        self.assertIsNotNone(order)
        self.assertEqual(order.status, "refunded")
        mock_revoke.assert_not_awaited()

    async def test_settle_benefits_net_topup_after_partial_refund_under_hold(self):
        """_settle_benefits credits only net_topup if partially refunded while held."""
        session = AsyncMock()
        order_uuid = uuid.uuid4()
        partially_refunded_order = Order(
            id=order_uuid,
            user_id=60,
            service_type="topup",
            amount_rub=Decimal("500.00"),
            status="paid",
            payment_method="yookassa",
            metadata_={"refunded_amount_rub": "200.00"},
        )

        with patch("services.order_service.create_order_credit", new=AsyncMock()) as mock_credit, \
             patch("services.order_service.FulfillmentService.fulfill_order", new=AsyncMock()), \
             patch("services.referral_bonus.grant_referral_bonus_for_topup", new=AsyncMock(return_value=MagicMock(referrer_bonus=Decimal("0"), referrer_user_id=None))) as mock_bonus:
            await OrderService._settle_benefits(session, partially_refunded_order, was_canceled=False)

        mock_credit.assert_awaited_once_with(
            session,
            user_id=60,
            amount_rub=Decimal("300.00"),
            order_id=order_uuid,
            metadata={"source": "yookassa_topup"},
        )
        mock_bonus.assert_awaited_once_with(
            session,
            purchaser_user_id=60,
            order_id=str(order_uuid),
            topup_amount=Decimal("300.00"),
        )

    async def test_notify_referrer_bonus_populates_bonus_balance(self):
        """notify_referrer_bonus fetches bonus_balance from ledger before sending push."""
        session = AsyncMock()
        order_uuid = uuid.uuid4()
        order = Order(
            id=order_uuid,
            user_id=10,
            service_type="topup",
            amount_rub=Decimal("500.00"),
            status="paid",
            metadata_={
                "referrer_notify_pending": {
                    "user_id": 99,
                    "telegram_id": 999111,
                    "bonus": "50",
                    "bonus_rate_pct": 10,
                }
            },
        )
        bot = AsyncMock()
        ref_balance = MagicMock(bonus_available=Decimal("150"))

        with patch("database.repositories.account_ledger_repo.get_account_balance", new=AsyncMock(return_value=ref_balance)), \
             patch("services.workers.credit_notifications._send_referrer_push", new=AsyncMock(return_value=True)) as mock_send, \
             patch("services.order_notifications.mark_referrer_notified") as mock_mark:
            sent = await notify_referrer_bonus(bot, session, order)

        self.assertTrue(sent)
        mock_send.assert_awaited_once()
        debt_arg = mock_send.await_args.args[1]
        self.assertEqual(debt_arg["bonus_balance"], 150)
        self.assertEqual(debt_arg["bonus"], "50")
        mock_mark.assert_called_once_with(order)


if __name__ == "__main__":
    unittest.main()

