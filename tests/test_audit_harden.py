"""Regression tests for audit-hardening PR: topup funnel + safety fixes."""
import unittest
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4


def _make_callback(data="balance_create:500"):
    cb = MagicMock()
    cb.bot = MagicMock()
    cb.bot._me = MagicMock(username="testbot")
    cb.message = MagicMock()
    cb.message.chat = MagicMock(id=123)
    cb.chat = MagicMock(id=123)
    cb.from_user = MagicMock(id=111)
    cb.data = data
    cb.answer = AsyncMock()
    return cb


def _make_user(**overrides):
    user = MagicMock()
    user.id = 1
    user.telegram_id = 111
    user.topup_blocked = False
    user.financial_hold = False
    user.is_deleted = False
    user.is_banned = False
    for key, value in overrides.items():
        setattr(user, key, value)
    return user


_FAKE_CFG = SimpleNamespace(
    BALANCE_MIN_TOPUP_RUB=10,
    BALANCE_MAX_CUSTOM_TOPUP_RUB=5000,
    BALANCE_MAX_UNFINISHED_TOPUPS=3,
)


class PendingCountTTLTests(unittest.IsolatedAsyncioTestCase):
    async def test_count_filters_fresh_with_url_only(self):
        from bot.handlers.payment.balance_routes import _count_pending_topup_orders

        session = MagicMock()
        seen = []

        async def fake_scalar(stmt):
            seen.append(stmt)
            return 0

        session.scalar = fake_scalar
        result = await _count_pending_topup_orders(session, 7)
        self.assertEqual(result, 0)
        compiled = str(seen[0].compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("payment_url IS NOT NULL", compiled)
        self.assertIn("created_at >=", compiled)
        self.assertIn("status = 'pending'", compiled)
        self.assertIn("service_type = 'topup'", compiled)
        self.assertIn("user_id = 7", compiled)

    async def test_lookup_uses_same_fresh_pending_filter(self):
        # Same behavioral contract as the count: fresh (15-min window),
        # topup, pending, with payment URL, scoped to the user.
        from bot.handlers.payment import balance_routes

        session = MagicMock()
        seen = []

        async def _capture(stmt):
            seen.append(stmt)
            return None

        session.scalar = AsyncMock(side_effect=_capture)
        await balance_routes._get_pending_topup_order(session, 7)
        compiled = str(seen[0].compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("payment_url IS NOT NULL", compiled)
        self.assertIn("created_at >=", compiled)
        self.assertIn("status = 'pending'", compiled)
        self.assertIn("service_type = 'topup'", compiled)
        self.assertIn("user_id = 7", compiled)


class TopupBackToTests(unittest.IsolatedAsyncioTestCase):
    async def test_gateway_failure_keeps_white_internet_back(self):
        from bot.handlers.payment.balance_routes import _create_and_render_topup

        user = _make_user()
        session = MagicMock()
        session.scalar = AsyncMock(return_value=0)
        cb = _make_callback()
        fake_cfg = SimpleNamespace(BALANCE_MAX_UNFINISHED_TOPUPS=3)

        async def _failing_create_order(*args, **kwargs):
            raise TimeoutError("gateway down")

        with (
            patch(
                "bot.handlers.payment.balance_routes.MaintenanceService.can_user_perform_action",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "bot.handlers.payment.balance_routes.get_settings",
                return_value=fake_cfg,
            ),
            patch(
                "bot.handlers.payment.balance_routes.OrderService.create_order",
                new=AsyncMock(side_effect=_failing_create_order),
            ),
            patch(
                "bot.handlers.payment.balance_routes.render_hub",
                new=AsyncMock(),
            ) as mock_hub,
        ):
            await _create_and_render_topup(
                cb, session, user, 500, context={"source": "white_internet"}
            )
        self.assertTrue(mock_hub.awaited)
        keyboard = mock_hub.await_args.args[3]
        callbacks = [
            b.callback_data
            for row in keyboard.inline_keyboard
            for b in row
        ]
        self.assertIn("white_internet", callbacks)


class ChooseTopupFinancialHoldTests(unittest.IsolatedAsyncioTestCase):
    async def test_entry_blocked_on_financial_hold(self):
        from bot import texts
        from bot.handlers.payment.balance_routes import choose_topup_amount

        cb = _make_callback("balance_topup")
        state = MagicMock()
        state.clear = AsyncMock()
        session = MagicMock()
        db_user = _make_user(financial_hold=True)
        with (
            patch(
                "bot.handlers.payment.balance_routes.MaintenanceService.can_user_perform_action",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "bot.handlers.payment.balance_routes.render_hub",
                new=AsyncMock(),
            ) as mock_hub,
        ):
            await choose_topup_amount(cb, state, session, db_user=db_user)
        self.assertTrue(mock_hub.awaited)
        self.assertEqual(
            mock_hub.await_args.args[2], texts.PAYMENT_DISPUTE_BLOCKED_NOTICE
        )


class SyncDeletedTests(unittest.IsolatedAsyncioTestCase):
    async def test_deleted_user_deactivates_profiles(self):
        from utils.datetime_helpers import now_utc
        from services.subscription import SubscriptionService

        user = SimpleNamespace(
            id=7,
            telegram_id=777,
            subscription_end=now_utc() + timedelta(days=5),
            is_banned=False,
            financial_hold=False,
            is_deleted=True,
        )
        profile = SimpleNamespace(
            id=11,
            server_id=3,
            server=None,
            peer_id="peer-x",
            client_name="client-x",
            provisioning_status="active",
            desired_version=1,
            desired_is_active=True,
            desired_expires_at=None,
            is_active=True,
        )
        session = MagicMock()
        session.flush = AsyncMock()
        with (
            patch(
                "services.subscription.get_user_profiles",
                new=AsyncMock(return_value=[profile]),
            ),
            patch(
                "services.subscription.enqueue_api_operation",
                new=AsyncMock(),
            ) as mock_enqueue,
        ):
            await SubscriptionService._sync_access_state(session, user)
        self.assertFalse(profile.desired_is_active)
        self.assertFalse(profile.is_active)
        self.assertIsNone(profile.desired_expires_at)
        mock_enqueue.assert_awaited_once()
        self.assertEqual(
            mock_enqueue.await_args.kwargs["payload"]["status"], "disabled"
        )


class ZeroCostGuardTests(unittest.IsolatedAsyncioTestCase):
    async def _run_card(self, side_effect, expected_text):
        from utils.datetime_helpers import now_utc
        from bot.handlers.payment import purchase_routes

        cb = MagicMock()
        cb.from_user = MagicMock(id=111)
        cb.data = "order_pay_card:2"
        cb.bot = MagicMock()
        cb.message = MagicMock()
        cb.message.chat = MagicMock(id=123)
        cb.answer = AsyncMock()
        session = MagicMock()
        db_user = MagicMock()
        db_user.id = 1
        db_user.financial_hold = False
        db_user.topup_blocked = False
        db_user.current_tariff_id = 1
        db_user.subscription_end = now_utc() + timedelta(days=5)

        tariff = MagicMock(id=2, is_active=True)
        current = MagicMock(id=1)
        with (
            patch(
                "bot.handlers.payment.purchase_routes.MaintenanceService.can_user_perform_action",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "bot.handlers.payment.purchase_routes.get_tariff_by_id",
                new=AsyncMock(side_effect=[tariff, current]),
            ),
            patch(
                "bot.handlers.payment.purchase_routes.OrderService.calculate_tariff_change",
                return_value=(Decimal("0.00"), 10),
            ),
            patch(
                "bot.handlers.payment.purchase_routes.OrderService.pay_from_wallet",
                new=AsyncMock(side_effect=side_effect),
            ),
        ):
            await purchase_routes.handle_order_pay_card(
                cb, session, db_user=db_user
            )
        cb.answer.assert_awaited_once_with(expected_text, show_alert=True)

    async def test_zero_cost_financial_hold(self):
        from bot import texts
        from services.order_service import FinancialHoldBlockedError

        await self._run_card(
            FinancialHoldBlockedError("hold"),
            texts.PAYMENT_DISPUTE_BLOCKED_NOTICE,
        )

    async def test_zero_cost_debt(self):
        from bot import texts
        from services.order_service import AccountDebtBlockedError

        await self._run_card(
            AccountDebtBlockedError("debt"),
            texts.PAYMENT_INSUFFICIENT_FUNDS_ALERT,
        )


class RenewFallbackNameTests(unittest.IsolatedAsyncioTestCase):
    async def test_header_uses_fallback_tier(self):
        from bot.handlers.payment import showcase_routes

        bot = MagicMock()
        chat_id = 123
        session = MagicMock()
        db_user = MagicMock()
        db_user.id = 1
        db_user.current_tariff_id = None
        db_user.subscription_end = None

        tariff5 = MagicMock()
        tariff5.id = 5
        tariff5.device_limit = 5
        tariff5.is_active = True
        tariff5.price_rub = Decimal("480.00")
        tariff5.duration_days = 30
        tariff5.name = "Standard-30"

        with (
            patch(
                "bot.handlers.payment.showcase_routes.MaintenanceService.can_user_perform_action",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "bot.handlers.payment.showcase_routes._get_effective_device_limit",
                new=AsyncMock(return_value=4),
            ),
            patch(
                "bot.handlers.payment.showcase_routes.get_active_tariffs",
                new=AsyncMock(return_value=[tariff5]),
            ),
            patch(
                "bot.handlers.payment.showcase_routes.get_tariff_by_id",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "bot.handlers.payment.showcase_routes.get_tariff_display_name",
                side_effect=lambda limit: f"TIER-{limit}",
            ),
            patch(
                "bot.handlers.payment.showcase_routes.render_hub",
                new=AsyncMock(),
            ) as mock_hub,
        ):
            await showcase_routes.render_quick_renew(bot, chat_id, session, db_user)
        self.assertTrue(mock_hub.awaited)
        self.assertIn("TIER-5", mock_hub.await_args.args[2])
        self.assertNotIn("TIER-4", mock_hub.await_args.args[2])


class SettlementHoldTests(unittest.IsolatedAsyncioTestCase):
    async def _run_mark_paid(self, *, hold=False, blocked=False):
        from services.order_service import OrderService

        order = MagicMock()
        order.id = uuid4()
        order.user_id = 9
        order.service_type = "topup"
        order.status = "pending"
        order.amount_rub = Decimal("500")
        order.payment_method = "yookassa"
        order.metadata_ = {}
        order.external_id = None
        user = SimpleNamespace(
            id=9, financial_hold=hold, topup_blocked=blocked
        )
        session = MagicMock()
        # Production settlement reads the order first, then the user via an
        # explicit SELECT ... FOR UPDATE (never session.get, which can hit
        # the identity map). Mirror that call order exactly.
        session.scalar = AsyncMock(side_effect=[order, user])
        session.flush = AsyncMock()
        with (
            patch(
                "services.order_service.create_order_credit",
                new=AsyncMock(
                    side_effect=AssertionError("credit must not happen under hold")
                ),
            ),
            patch(
                "services.referral_bonus.grant_referral_bonus_for_topup",
                new=AsyncMock(
                    side_effect=AssertionError("bonus must not happen under hold")
                ),
            ),
            patch(
                "services.order_service.FulfillmentService.fulfill_order",
                new=AsyncMock(
                    side_effect=AssertionError("fulfill must not happen under hold")
                ),
            ),
        ):
            result = await OrderService.mark_order_paid(session, order.id)
        return result, order

    async def test_topup_hold_withholds_credit(self):
        result, order = await self._run_mark_paid(hold=True)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "paid")
        self.assertTrue((result.metadata_ or {}).get("settlement_held"))
        # Webhook keys its "credited" notice off _newly_paid: held
        # settlement must stay silent.
        self.assertFalse(result._newly_paid)

    async def test_topup_blocked_withholds_credit(self):
        result, order = await self._run_mark_paid(blocked=True)
        self.assertIsNotNone(result)
        self.assertTrue((result.metadata_ or {}).get("settlement_held"))
        self.assertFalse(result._newly_paid)


class OrderPayCardHoldTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_button_blocked_under_hold(self):
        from bot import texts
        from bot.handlers.payment import purchase_routes

        cb = MagicMock()
        cb.from_user = MagicMock(id=111)
        cb.data = "order_pay_card:2"
        cb.bot = MagicMock()
        cb.message = MagicMock()
        cb.message.chat = MagicMock(id=123)
        cb.answer = AsyncMock()
        session = MagicMock()
        db_user = _make_user(financial_hold=True)
        with (
            patch(
                "bot.handlers.payment.purchase_routes.MaintenanceService.can_user_perform_action",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "bot.handlers.payment.purchase_routes.OrderService.create_order",
                new=AsyncMock(
                    side_effect=AssertionError("must not create under hold")
                ),
            ),
        ):
            await purchase_routes.handle_order_pay_card(
                cb, session, db_user=db_user
            )
        cb.answer.assert_awaited_once_with(
            texts.PAYMENT_DISPUTE_BLOCKED_NOTICE, show_alert=True
        )


class ResumeHoldTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_hidden_under_hold(self):
        from bot import texts
        from bot.handlers.payment import balance_routes

        cb = _make_callback("balance_resume_topup")
        session = MagicMock()
        db_user = _make_user(financial_hold=True)
        pending = MagicMock(payment_url="https://pay.test/x")
        with (
            patch(
                "bot.handlers.payment.balance_routes._get_pending_topup_order",
                new=AsyncMock(return_value=pending),
            ),
            patch(
                "bot.handlers.payment.balance_routes.render_hub",
                new=AsyncMock(),
            ) as mock_hub,
        ):
            await balance_routes.resume_topup(cb, session, db_user=db_user)
        self.assertTrue(mock_hub.awaited)
        self.assertEqual(
            mock_hub.await_args.args[2], texts.PAYMENT_DISPUTE_BLOCKED_NOTICE
        )


class SettlementReleaseTests(unittest.IsolatedAsyncioTestCase):
    async def _run_release(self, *, hold=False):
        from services.order_service import OrderService
        from services.referral_bonus import ReferralBonusGrantResult

        order = MagicMock()
        order.id = uuid4()
        order.user_id = 12
        order.service_type = "topup"
        order.status = "paid"
        order.amount_rub = Decimal("500")
        order.payment_method = "yookassa"
        order.metadata_ = {"settlement_held": True}
        order.external_id = None
        user = SimpleNamespace(id=12, financial_hold=hold, topup_blocked=False)
        session = MagicMock()
        session.scalar = AsyncMock(side_effect=[order, user])
        session.flush = AsyncMock()
        with (
            patch(
                "services.order_service.create_order_credit",
                new=AsyncMock(),
            ) as mock_credit,
            patch(
                "services.referral_bonus.grant_referral_bonus_for_topup",
                new=AsyncMock(return_value=ReferralBonusGrantResult()),
            ) as mock_grant,
            patch(
                "services.order_service.FulfillmentService.fulfill_order",
                new=AsyncMock(),
            ) as mock_fulfill,
        ):
            result = await OrderService.mark_order_paid(session, order.id)
        return result, mock_credit, mock_grant, mock_fulfill

    async def test_release_after_hold_lifted_settles(self):
        result, mock_credit, mock_grant, mock_fulfill = await self._run_release(
            hold=False
        )
        self.assertTrue(result._newly_paid)
        self.assertNotIn("settlement_held", result.metadata_ or {})
        mock_credit.assert_awaited_once()
        mock_grant.assert_awaited_once()
        mock_fulfill.assert_awaited_once()

    async def test_still_held_stays_withheld(self):
        result, mock_credit, mock_grant, mock_fulfill = await self._run_release(
            hold=True
        )
        self.assertFalse(result._newly_paid)
        self.assertTrue((result.metadata_ or {}).get("settlement_held"))
        mock_credit.assert_not_awaited()
        mock_grant.assert_not_awaited()
        mock_fulfill.assert_not_awaited()


class UnexpectedFailureRollbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_wallet_unexpected_rolls_back(self):
        from bot import texts
        from bot.handlers.payment import purchase_routes

        cb = MagicMock()
        cb.from_user = MagicMock(id=111)
        cb.data = "order_pay_wallet:2"
        cb.bot = MagicMock()
        cb.message = MagicMock()
        cb.message.chat = MagicMock(id=123)
        cb.answer = AsyncMock()
        session = MagicMock()
        session.rollback = AsyncMock()
        db_user = _make_user()
        tariff = MagicMock(id=2, is_active=True)
        with (
            patch(
                "bot.handlers.payment.purchase_routes.MaintenanceService.can_user_perform_action",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "bot.handlers.payment.purchase_routes.get_tariff_by_id",
                new=AsyncMock(return_value=tariff),
            ),
            patch(
                "bot.handlers.payment.purchase_routes.OrderService.pay_from_wallet",
                new=AsyncMock(side_effect=RuntimeError("fulfill boom")),
            ),
        ):
            await purchase_routes.handle_order_pay_wallet(
                cb, session, db_user=db_user
            )
        session.rollback.assert_awaited_once()
        # First answer is the processing notice, last must be the failure.
        cb.answer.assert_awaited_with(
            texts.PAYMENT_PURCHASE_OPEN_FAILED, show_alert=True
        )

    async def test_zero_cost_unexpected_rolls_back(self):
        from decimal import Decimal as _Decimal

        from bot import texts
        from bot.handlers.payment import purchase_routes
        from utils.datetime_helpers import now_utc

        cb = MagicMock()
        cb.from_user = MagicMock(id=111)
        cb.data = "order_pay_card:2"
        cb.bot = MagicMock()
        cb.message = MagicMock()
        cb.message.chat = MagicMock(id=123)
        cb.answer = AsyncMock()
        session = MagicMock()
        session.rollback = AsyncMock()
        db_user = _make_user()
        db_user.current_tariff_id = 1
        db_user.subscription_end = now_utc() + timedelta(days=5)
        tariff = MagicMock(id=2, is_active=True)
        current = MagicMock(id=1)
        with (
            patch(
                "bot.handlers.payment.purchase_routes.MaintenanceService.can_user_perform_action",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "bot.handlers.payment.purchase_routes.get_tariff_by_id",
                new=AsyncMock(side_effect=[tariff, current]),
            ),
            patch(
                "bot.handlers.payment.purchase_routes.OrderService.calculate_tariff_change",
                return_value=(_Decimal("0.00"), 10),
            ),
            patch(
                "bot.handlers.payment.purchase_routes.OrderService.pay_from_wallet",
                new=AsyncMock(side_effect=RuntimeError("fulfill boom")),
            ),
        ):
            await purchase_routes.handle_order_pay_card(
                cb, session, db_user=db_user
            )
        session.rollback.assert_awaited_once()
        cb.answer.assert_awaited_once_with(
            texts.PAYMENT_PURCHASE_OPEN_FAILED, show_alert=True
        )


class OrderCheckHeldTests(unittest.IsolatedAsyncioTestCase):
    async def _run_check(self, settled_order):
        from bot.handlers.payment import purchase_routes

        cb = MagicMock()
        cb.from_user = MagicMock(id=111)
        cb.data = f"order_check:{uuid4()}"
        cb.bot = MagicMock()
        cb.message = MagicMock()
        cb.message.chat = MagicMock(id=123)
        cb.answer = AsyncMock()
        session = MagicMock()
        db_user = _make_user()
        held_order = MagicMock()
        held_order.user_id = db_user.id
        held_order.status = "paid"
        held_order.service_type = "topup"
        held_order.metadata_ = {"settlement_held": True}
        session.get = AsyncMock(return_value=held_order)
        with (
            patch(
                "bot.handlers.payment.purchase_routes.OrderService.mark_order_paid",
                new=AsyncMock(return_value=settled_order),
            ),
            patch(
                "bot.handlers.payment.balance_routes._render_balance",
                new=AsyncMock(),
            ) as mock_balance,
        ):
            await purchase_routes.handle_order_check(
                cb, session, db_user=db_user
            )
        return cb, mock_balance

    async def test_early_paid_held_shows_dispute(self):
        from bot import texts

        still_held = MagicMock(metadata_={"settlement_held": True})
        cb, mock_balance = await self._run_check(still_held)
        mock_balance.assert_not_awaited()
        cb.answer.assert_awaited_once_with(
            texts.PAYMENT_DISPUTE_BLOCKED_NOTICE, show_alert=True
        )

    async def test_early_paid_released_renders_credited(self):
        from bot import texts

        released = MagicMock(
            service_type="topup", metadata_={}, amount_rub=Decimal("500")
        )
        cb, mock_balance = await self._run_check(released)
        mock_balance.assert_awaited_once()
        _, kwargs = mock_balance.await_args
        self.assertEqual(kwargs.get("notice"), texts.TOPUP_CREDITED_NOTICE)


class AdminHeldCardTests(unittest.IsolatedAsyncioTestCase):
    async def test_held_order_maps_to_manual_review(self):
        from bot.handlers.admin.payments import order_display_status

        held = MagicMock(status="paid", metadata_={"settlement_held": True})
        self.assertEqual(order_display_status(held), "requires_manual_review")
        clean = MagicMock(status="paid", metadata_={})
        self.assertEqual(order_display_status(clean), "completed")

    async def test_held_order_card_shows_reason(self):
        from bot.handlers.admin import payments as admin_payments
        from utils.datetime_helpers import now_utc

        order_id = uuid4()
        order = MagicMock()
        order.id = order_id
        order.user_id = 5
        order.service_type = "topup"
        order.status = "paid"
        order.amount_rub = Decimal("500")
        order.payment_method = "yookassa"
        order.created_at = now_utc()
        order.paid_at = now_utc()
        order.refunded_at = None
        order.external_id = None
        order.description = None
        order.metadata_ = {
            "settlement_held": True,
            "settlement_hold_reason": "financial_hold",
        }
        order.tariff = None
        user = MagicMock(username=None, telegram_id=555)
        order.user = user
        session = MagicMock()
        session.scalar = AsyncMock(return_value=order)
        cb = MagicMock()
        cb.from_user = MagicMock(id=1)
        cb.data = f"admin_order_card:{order_id}"
        cb.message = MagicMock()
        cb.message.edit_text = AsyncMock()
        cb.answer = AsyncMock()
        state = MagicMock()
        state.clear = AsyncMock()
        with patch(
            "bot.handlers.admin.payments.is_admin", return_value=True
        ):
            await admin_payments.show_order_card(cb, state, session)
        text = cb.message.edit_text.await_args.args[0]
        self.assertIn("Ручная проверка", text)
        self.assertIn("financial_hold", text)


class WebhookHeldNotificationTests(unittest.IsolatedAsyncioTestCase):
    """Direct regression: a held settlement must never notify 'credited'."""

    async def test_webhook_held_topup_sends_no_success_notice(self):
        from bot.handlers.webhook import yookassa_webhook_handler
        from database.models import User
        from unittest.mock import AsyncMock as _AsyncMock

        order = MagicMock()
        order.id = uuid4()
        order.user_id = 21
        order.service_type = "topup"
        order.status = "pending"
        order.amount_rub = Decimal("500")
        order.payment_method = "yookassa"
        order.metadata_ = {}
        order.external_id = None
        order._newly_paid = False
        held_user = User(
            id=21, telegram_id=111, financial_hold=True, topup_blocked=False
        )

        session = MagicMock()
        # Real call order: inbox lookup -> None; process_webhook_event loads
        # the order; mark_order_paid re-reads it under lock, then loads the
        # settlement user with a fresh SELECT ... FOR UPDATE.
        session.scalar = _AsyncMock(side_effect=[None, order, order, held_user])
        session.execute = _AsyncMock()
        session.flush = _AsyncMock()

        scope_ctx = MagicMock()
        scope_ctx.__aenter__ = _AsyncMock(return_value=session)
        scope_ctx.__aexit__ = _AsyncMock(return_value=False)

        request = MagicMock()
        request.content_length = 300
        request.app = {"bot": MagicMock()}
        request.json = _AsyncMock(
            return_value={
                "type": "notification",
                "event": "payment.succeeded",
                "object": {
                    "id": "pay-held-1",
                    "status": "succeeded",
                    "metadata": {"order_id": str(order.id)},
                },
            }
        )

        with (
            patch("bot.handlers.webhook.session_scope", return_value=scope_ctx),
            patch(
                "bot.handlers.webhook._get_real_ip", return_value="185.71.76.1"
            ),
            patch("bot.handlers.webhook._is_yookassa_ip", return_value=True),
            patch(
                "bot.handlers.payment.balance_routes._render_balance",
                new=_AsyncMock(),
            ) as mock_render,
        ):
            response = await yookassa_webhook_handler(request)

        self.assertEqual(response.status, 200)
        self.assertTrue((order.metadata_ or {}).get("settlement_held"))
        self.assertFalse(order._newly_paid)
        mock_render.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
