"""Regression tests for audit-hardening PR: topup funnel + safety fixes."""
import unittest
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


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
        result = await _count_pending_topup_orders(session, 1)
        self.assertEqual(result, 0)
        compiled = str(seen[0].compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("payment_url IS NOT NULL", compiled)
        self.assertIn("created_at", compiled)
        self.assertIn("pending", compiled)


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
            ),
        ):
            await SubscriptionService._sync_access_state(session, user)
        self.assertFalse(profile.desired_is_active)
        self.assertFalse(profile.is_active)


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


if __name__ == "__main__":
    unittest.main()
