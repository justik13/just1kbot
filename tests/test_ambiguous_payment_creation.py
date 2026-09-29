"""Ambiguous payment creation must never destroy the local order.

YooKassa documents HTTP 500 / network failure as "result unknown, check
first", and keeps an Idempotence-Key valid for 24h. The payment may exist
while our response was lost. Rolling the order back drops the only local
reference, so the follow-up `payment.succeeded` webhook finds nothing and
YooKassa retries for 24h against a dead `order_id` — money taken, nothing
granted. The order must survive the unknown result and be flagged instead.
"""
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from integrations.payment_gateways.base import PaymentCreationAmbiguousError
from services.yookassa_service import YooKassaErrorKind, YooKassaResult


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


def _make_user():
    user = MagicMock()
    user.id = 1
    user.telegram_id = 111
    user.topup_blocked = False
    user.financial_hold = False
    return user


_FAKE_CFG = SimpleNamespace(BALANCE_MAX_UNFINISHED_TOPUPS=3)


class GatewayAmbiguousClassificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_ambiguous_failure_raises_specific_error(self):
        from integrations.payment_gateways.yookassa import YooKassaGateway

        with patch(
            "integrations.payment_gateways.yookassa.YooKassaService.create_payment_result",
            new=AsyncMock(
                return_value=YooKassaResult(
                    ok=False,
                    error_kind=YooKassaErrorKind.TIMEOUT,
                    status_code=None,
                    retryable=True,
                    ambiguous=True,
                )
            ),
        ):
            with self.assertRaises(PaymentCreationAmbiguousError):
                await YooKassaGateway().create_payment_url(
                    order_id=str(uuid4()),
                    amount_rub=Decimal("500"),
                    description="x",
                    bot_username="testbot",
                )

    async def test_definitive_failure_still_raises_plain_runtime_error(self):
        from integrations.payment_gateways.yookassa import YooKassaGateway

        with patch(
            "integrations.payment_gateways.yookassa.YooKassaService.create_payment_result",
            new=AsyncMock(
                return_value=YooKassaResult(
                    ok=False,
                    error_kind=YooKassaErrorKind.VALIDATION_FAILED,
                    status_code=400,
                    retryable=False,
                    ambiguous=False,
                )
            ),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                await YooKassaGateway().create_payment_url(
                    order_id=str(uuid4()),
                    amount_rub=Decimal("500"),
                    description="x",
                    bot_username="testbot",
                )
        self.assertNotIsInstance(
            ctx.exception, PaymentCreationAmbiguousError
        )


class AmbiguousClassificationByHttpCodeTests(unittest.IsolatedAsyncioTestCase):
    """YooKassa: 4xx means rejected, only 5xx/unreachable is unknown."""

    async def _result(self, status, json_side_effect):
        from unittest.mock import AsyncMock as _AsyncMock, patch as _patch

        from services import yookassa_service

        resp = MagicMock()
        resp.status = status
        resp.json = _AsyncMock(side_effect=json_side_effect)
        fake_settings = SimpleNamespace(
            YOOKASSA_SHOP_ID="shop", YOOKASSA_SECRET_KEY="secret"
        )
        with (
            _patch("aiohttp.ClientSession.request") as mock_req,
            _patch(
                "services.yookassa_service.get_settings",
                return_value=fake_settings,
            ),
        ):
            mock_req.return_value.__aenter__.return_value = resp
            return await yookassa_service.YooKassaService.create_payment_result(
                {"amount": {"value": "500.00", "currency": "RUB"}},
                idempotency_key="k",
            )

    async def test_400_with_malformed_body_is_not_ambiguous(self):
        result = await self._result(400, ValueError("bad json"))
        self.assertFalse(result.ok)
        self.assertFalse(result.ambiguous)
        self.assertEqual(result.status_code, 400)

    async def test_400_with_non_dict_body_is_not_ambiguous(self):
        result = await self._result(400, ["unexpected"])
        self.assertFalse(result.ok)
        self.assertFalse(result.ambiguous)

    async def test_500_with_malformed_body_is_ambiguous(self):
        result = await self._result(500, ValueError("bad json"))
        self.assertFalse(result.ok)
        self.assertTrue(result.ambiguous)

    async def test_200_with_malformed_body_is_ambiguous(self):
        # Server accepted the request and sent something unreadable: the
        # payment may well exist, so the outcome is unknown.
        result = await self._result(200, ValueError("bad json"))
        self.assertFalse(result.ok)
        self.assertTrue(result.ambiguous)


class CreateOrderKeepsOrderTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, gateway_error):
        from services.order_service import OrderService

        session = MagicMock()
        session.flush = AsyncMock()
        session.rollback = AsyncMock()
        session.commit = AsyncMock()
        session.scalar = AsyncMock(return_value=None)  # no dedup hit
        session.get = AsyncMock(return_value=None)
        created: list = []
        session.add = MagicMock(side_effect=created.append)
        gateway = MagicMock()
        gateway.create_payment_url = AsyncMock(side_effect=gateway_error)
        with patch(
            "services.order_service.get_payment_gateway",
            return_value=gateway,
        ):
            with self.assertRaises(type(gateway_error)):
                await OrderService.create_order(
                    session,
                    user_id=3,
                    service_type="topup",
                    amount_rub=Decimal("500"),
                    payment_method="yookassa",
                )
        self.assertTrue(created, "order must be flushed before gateway call")
        return created[0], session

    async def test_ambiguous_keeps_order_and_flags_it(self):
        order, session = await self._run(
            PaymentCreationAmbiguousError("unknown")
        )
        session.rollback.assert_not_awaited()
        session.flush.assert_awaited()
        # Durable before the caller performs Telegram I/O: a failure there
        # must not be able to roll the order back and lose the payment.
        session.commit.assert_awaited_once()
        self.assertTrue(order.metadata_["payment_creation_ambiguous"])
        # No invoice means the funnel keeps ignoring the order: it must not
        # be counted as an unfinished topup and must not be shown to the user.
        self.assertIsNone(order.payment_url)

    async def test_definitive_failure_rolls_back(self):
        order, session = await self._run(RuntimeError("validation_failed"))
        session.rollback.assert_awaited_once()
        session.commit.assert_not_awaited()


class HandlerAmbiguousNoticeTests(unittest.IsolatedAsyncioTestCase):
    async def test_topup_shows_wait_not_failure(self):
        from bot import texts
        from bot.handlers.payment.balance_routes import _create_and_render_topup

        cb = _make_callback()
        session = MagicMock()
        session.scalar = AsyncMock(return_value=0)
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
                "bot.handlers.payment.balance_routes.OrderService.create_order",
                new=AsyncMock(
                    side_effect=PaymentCreationAmbiguousError("timeout")
                ),
            ),
            patch(
                "bot.handlers.payment.balance_routes.render_hub",
                new=AsyncMock(),
            ) as mock_hub,
        ):
            await _create_and_render_topup(cb, session, _make_user(), 500)
        self.assertTrue(mock_hub.awaited)
        self.assertEqual(
            mock_hub.await_args.args[2], texts.PAYMENT_CREATION_STATUS_UNKNOWN
        )

    async def test_card_checkout_shows_wait_not_failure(self):
        from bot import texts
        from bot.handlers.payment import purchase_routes

        cb = _make_callback("order_pay_card:2")
        cb.data = "order_pay_card:2"
        session = MagicMock()
        db_user = _make_user()
        db_user.current_tariff_id = None
        db_user.subscription_end = None
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
                "bot.handlers.payment.purchase_routes.OrderService.create_order",
                new=AsyncMock(
                    side_effect=PaymentCreationAmbiguousError("timeout")
                ),
            ),
        ):
            await purchase_routes.handle_order_pay_card(
                cb, session, db_user=db_user
            )
        cb.answer.assert_awaited_with(
            texts.PAYMENT_CREATION_STATUS_UNKNOWN, show_alert=True
        )


if __name__ == "__main__":
    unittest.main()
