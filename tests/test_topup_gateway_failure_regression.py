"""Regression: YooKassa gateway failure must not raise MissingGreenlet.

Prod 2026-09-29: POST /payments TIMEOUT -> OrderService.create_order()
did session.rollback() -> expired User -> balance_routes logger.exception
touched user.id -> MissingGreenlet -> false CRITICAL Database unavailable.
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.exc import MissingGreenlet


class _ExpiredAfterGatewayUser:
    """Mimics SQLAlchemy expired ORM: id works until rollback, then raises."""

    def __init__(self, user_id: int, telegram_id: int):
        self._user_id = user_id
        self._telegram_id = telegram_id
        self._expired = False

    @property
    def id(self):
        if self._expired:
            raise MissingGreenlet("greenlet_spawn has not been called")
        return self._user_id

    @property
    def telegram_id(self):
        if self._expired:
            raise MissingGreenlet("greenlet_spawn has not been called")
        return self._telegram_id


def _make_callback():
    cb = MagicMock()
    cb.bot = MagicMock()
    cb.bot._me = MagicMock(username="testbot")
    cb.message = MagicMock()
    cb.message.chat = MagicMock(id=123)
    cb.message.message_id = 1
    cb.chat = MagicMock(id=123)
    cb.from_user = MagicMock(id=872658825)
    cb.data = "balance_create:500"
    cb.answer = AsyncMock()
    return cb


class TopupGatewayFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_and_render_topup_timeout_renders_payment_error(self):
        from bot.handlers.payment.balance_routes import _create_and_render_topup

        user = _ExpiredAfterGatewayUser(user_id=1, telegram_id=872658825)
        session = MagicMock()
        cb = _make_callback()

        async def _failing_create_order(*args, **kwargs):
            user._expired = True  # simulate session.rollback() expiry
            raise RuntimeError("YooKassa payment creation error: timeout")

        with (
            patch(
                "bot.handlers.payment.balance_routes.MaintenanceService.can_user_perform_action",
                new=AsyncMock(return_value=True),
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
            await _create_and_render_topup(cb, session, user, 500)

        self.assertTrue(mock_hub.awaited)
        # Second positional arg is text; must be payment-service error, not technical.
        rendered_text = mock_hub.await_args.args[2]
        self.assertIn("платёж", rendered_text.lower())

    async def test_preset_topup_rejects_out_of_range_amount(self):
        from types import SimpleNamespace

        from bot.handlers.payment.balance_routes import create_preset_topup

        cb = _make_callback()
        cb.data = "balance_create:99999999"
        session = MagicMock()
        db_user = MagicMock()
        db_user.id = 1
        fake_cfg = SimpleNamespace(
            BALANCE_MIN_TOPUP_RUB=10,
            BALANCE_MAX_CUSTOM_TOPUP_RUB=5000,
        )

        with (
            patch(
                "bot.handlers.payment.balance_routes.OrderService.create_order",
                new=AsyncMock(),
            ) as mock_create,
            patch(
                "bot.handlers.payment.balance_routes.MaintenanceService.can_user_perform_action",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "bot.handlers.payment.balance_routes.get_settings",
                return_value=fake_cfg,
            ),
        ):
            await create_preset_topup(cb, session, db_user=db_user)

        mock_create.assert_not_awaited()
        cb.answer.assert_awaited()

    async def test_order_pay_card_gateway_failure_uses_captured_id(self):
        from bot.handlers.payment.purchase_routes import handle_order_pay_card

        cb = MagicMock()
        cb.from_user = MagicMock(id=872658825)
        cb.data = "order_pay_card:5"
        cb.bot = MagicMock()
        cb.message = MagicMock()
        cb.message.chat = MagicMock(id=123)
        cb.answer = AsyncMock()
        session = MagicMock()
        user = _ExpiredAfterGatewayUser(user_id=7, telegram_id=872658825)

        tariff = MagicMock()
        tariff.id = 5
        tariff.is_active = True

        async def _failing_create_order(*args, **kwargs):
            user._expired = True
            raise RuntimeError("YooKassa payment creation error: timeout")

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
                "bot.handlers.payment.purchase_routes.OrderService.create_order",
                new=AsyncMock(side_effect=_failing_create_order),
            ),
        ):
            await handle_order_pay_card(cb, session, db_user=user)

        # Must answer payment-service error, not raise MissingGreenlet.
        cb.answer.assert_awaited()
        answered_text = cb.answer.await_args.args[0]
        self.assertIn("платёж", answered_text.lower())


if __name__ == "__main__":
    unittest.main()
