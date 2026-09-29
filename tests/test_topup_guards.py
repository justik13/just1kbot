"""Funnel guards: blocked / hold / unfinished users must not create topup orders."""
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
    for key, value in overrides.items():
        setattr(user, key, value)
    return user


_FAKE_CFG = SimpleNamespace(
    BALANCE_MIN_TOPUP_RUB=10,
    BALANCE_MAX_CUSTOM_TOPUP_RUB=5000,
    BALANCE_MAX_UNFINISHED_TOPUPS=3,
)


class TopupFunnelGuardTests(unittest.IsolatedAsyncioTestCase):
    async def _run_topup(self, user, pending=0):
        from bot.handlers.payment.balance_routes import _create_and_render_topup

        session = MagicMock()
        session.scalar = AsyncMock(return_value=pending)
        cb = _make_callback()
        with (
            patch(
                "bot.handlers.payment.balance_routes.MaintenanceService.can_user_perform_action",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "bot.handlers.payment.balance_routes.get_settings",
                return_value=_FAKE_CFG,
            ),
            patch(
                "bot.handlers.payment.balance_routes.get_account_balance",
                new=AsyncMock(return_value=MagicMock(available=Decimal("1000"))),
            ),
            patch(
                "bot.handlers.payment.balance_routes.render_hub",
                new=AsyncMock(),
            ) as mock_hub,
            patch(
                "bot.handlers.payment.balance_routes.OrderService.create_order",
                new=AsyncMock(
                    return_value=MagicMock(
                        id=uuid4(),
                        amount_rub=Decimal("500"),
                        payment_url="https://pay.test/x",
                    )
                ),
            ) as mock_create,
        ):
            await _create_and_render_topup(cb, session, user, 500)
        return mock_hub, mock_create

    async def test_blocked_user_gets_blocked_screen(self):
        from bot import texts

        mock_hub, mock_create = await self._run_topup(_make_user(topup_blocked=True))
        mock_create.assert_not_awaited()
        self.assertTrue(mock_hub.awaited)
        self.assertEqual(mock_hub.await_args.args[2], texts.TOPUP_ERROR_BLOCKED)

    async def test_financial_hold_gets_dispute_screen(self):
        from bot import texts

        mock_hub, mock_create = await self._run_topup(_make_user(financial_hold=True))
        mock_create.assert_not_awaited()
        self.assertTrue(mock_hub.awaited)
        self.assertEqual(
            mock_hub.await_args.args[2], texts.PAYMENT_DISPUTE_BLOCKED_NOTICE
        )

    async def test_unfinished_limit_blocks_creation(self):
        from bot import texts

        mock_hub, mock_create = await self._run_topup(_make_user(), pending=3)
        mock_create.assert_not_awaited()
        self.assertTrue(mock_hub.awaited)
        self.assertIn("3", mock_hub.await_args.args[2])
        self.assertIn(
            texts.TOPUP_ERROR_UNFINISHED.split("{limit}")[0].strip()[:20],
            mock_hub.await_args.args[2],
        )

    async def test_below_limit_proceeds(self):
        mock_hub, mock_create = await self._run_topup(_make_user(), pending=2)
        mock_create.assert_awaited_once()
        self.assertTrue(mock_hub.awaited)


class ShowcaseDiscountBadgeTests(unittest.IsolatedAsyncioTestCase):
    async def _render_select_tariff(self, db_user):
        from bot.handlers.payment.showcase_routes import select_tariff

        cb = MagicMock()
        cb.from_user = MagicMock(id=111)
        cb.data = "select_tariff:2"
        cb.bot = MagicMock()
        cb.message = MagicMock()
        cb.message.chat = MagicMock(id=123)
        cb.answer = AsyncMock()
        state = MagicMock()
        state.clear = AsyncMock()
        session = MagicMock()

        tariff = MagicMock()
        tariff.id = 2
        tariff.name = "Standard-90"
        tariff.price_rub = Decimal("100.00")
        tariff.duration_days = 90
        tariff.device_limit = 2
        tariff.is_active = True

        balance = MagicMock(
            available=Decimal("1000"),
            debt=Decimal("0"),
            real_available=Decimal("1000"),
        )

        with (
            patch(
                "bot.handlers.payment.showcase_routes.MaintenanceService.can_user_perform_action",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "bot.handlers.payment.showcase_routes.get_tariff_by_id",
                new=AsyncMock(return_value=tariff),
            ),
            patch(
                "bot.handlers.payment.showcase_routes.get_account_balance",
                new=AsyncMock(return_value=balance),
            ),
            patch(
                "bot.handlers.payment.showcase_routes._check_tariff_change_allowed",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "bot.handlers.payment.showcase_routes._get_effective_device_limit",
                new=AsyncMock(return_value=2),
            ),
            patch(
                "bot.handlers.payment.showcase_routes.is_eligible_for_referral_first_discount",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "bot.handlers.payment.showcase_routes.render_hub",
                new=AsyncMock(),
            ) as mock_hub,
        ):
            await select_tariff(cb, state, session, db_user=db_user)
        self.assertTrue(mock_hub.awaited)
        return mock_hub.await_args.args[2]

    async def test_badge_hidden_on_tariff_change(self):
        from utils.datetime_helpers import now_utc

        db_user = MagicMock()
        db_user.id = 1
        db_user.has_financial_hold = False
        db_user.financial_hold = False
        db_user.current_tariff_id = 1
        db_user.subscription_end = now_utc() + timedelta(days=5)

        text = await self._render_select_tariff(db_user)
        self.assertNotIn("Скидка 25%", text)

    async def test_badge_shown_for_first_purchase(self):
        db_user = MagicMock()
        db_user.id = 1
        db_user.has_financial_hold = False
        db_user.financial_hold = False
        db_user.current_tariff_id = None
        db_user.subscription_end = None

        text = await self._render_select_tariff(db_user)
        self.assertIn("Скидка 25%", text)


if __name__ == "__main__":
    unittest.main()
