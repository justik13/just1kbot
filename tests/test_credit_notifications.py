"""Late credit-push notifications: settlement debt, delivery, worker backstop.

Covers the scenario the production owner cares about: the user paid, the
money was held (or the Telegram send failed), the user left the payment
screen — and the credit lands later. The push must still arrive.

All tests are mock-only (no database); Postgres execution of the new
pre-flight blocks is covered by tests/test_preflight_command.py in CI.
"""

import unittest
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Order, User
from services.order_notifications import (
    mark_notified,
    mark_notify_pending,
)


def _topup_order(**kwargs) -> Order:
    params = {
        "id": uuid.uuid4(),
        "user_id": 10,
        "service_type": "topup",
        "amount_rub": Decimal("200.00"),
        "status": "paid",
    }
    params.update(kwargs)
    return Order(**params)


def _user(**kwargs) -> User:
    params = {"id": 10, "telegram_id": 123456789}
    params.update(kwargs)
    return User(**params)


class TestNotifyDebtFlags(unittest.TestCase):
    def test_mark_pending_sets_debt(self):
        order = _topup_order(metadata_={})
        mark_notify_pending(order)
        self.assertTrue(order.metadata_.get("late_notify_pending"))

    def test_mark_notified_clears_debt_and_attempts(self):
        order = _topup_order(
            metadata_={"late_notify_pending": True, "late_notify_attempts": 7}
        )
        mark_notified(order)
        self.assertNotIn("late_notify_pending", order.metadata_)
        self.assertNotIn("late_notify_attempts", order.metadata_)


class TestSettlementCreatesDebt(unittest.IsolatedAsyncioTestCase):
    @patch("services.order_service.FulfillmentService")
    @patch("services.referral_bonus.grant_referral_bonus_for_topup")
    @patch("services.order_service.create_order_credit")
    async def test_settle_benefits_arms_late_notify(
        self, mock_credit, mock_bonus, mock_fulfill
    ):
        from services.order_service import OrderService

        mock_credit.return_value = AsyncMock()
        mock_bonus.return_value = 0
        mock_fulfill.fulfill_order = AsyncMock()

        session = AsyncMock(spec=AsyncSession)
        order = _topup_order(metadata_={})
        await OrderService._settle_benefits(session, order, was_canceled=False)

        self.assertTrue(order.metadata_.get("late_notify_pending"))
        self.assertNotIn("late_notify_attempts", order.metadata_)


class TestCreditNotifySender(unittest.IsolatedAsyncioTestCase):
    async def test_topup_delivered_on_success(self):
        from bot.handlers.payment.credit_notify import notify_order_credited

        bot = AsyncMock()
        session = AsyncMock(spec=AsyncSession)
        order = _topup_order()
        user = _user()
        with patch(
            "bot.handlers.payment.balance_routes._render_balance",
            new=AsyncMock(),
        ) as mock_render:
            self.assertTrue(
                await notify_order_credited(bot, session, order, user)
            )
            mock_render.assert_awaited_once()

    async def test_returns_false_on_send_failure(self):
        from bot.handlers.payment.credit_notify import notify_order_credited

        bot = AsyncMock()
        session = AsyncMock(spec=AsyncSession)
        order = _topup_order()
        user = _user()
        with patch(
            "bot.handlers.payment.balance_routes._render_balance",
            new=AsyncMock(side_effect=RuntimeError("telegram down")),
        ):
            self.assertFalse(
                await notify_order_credited(bot, session, order, user)
            )

    async def test_returns_false_without_telegram_id(self):
        from bot.handlers.payment.credit_notify import notify_order_credited

        bot = AsyncMock()
        session = AsyncMock(spec=AsyncSession)
        order = _topup_order()
        user = _user(telegram_id=None)
        self.assertFalse(await notify_order_credited(bot, session, order, user))


def _scope_with(session):
    scope = MagicMock()
    scope.__aenter__ = AsyncMock(return_value=session)
    scope.__aexit__ = AsyncMock(return_value=False)
    return scope


class TestCreditNotifyWorker(unittest.IsolatedAsyncioTestCase):
    def _session(self, order, user, has_credit=1):
        session = AsyncMock(spec=AsyncSession)
        session.get = AsyncMock(side_effect=[order, user])
        session.scalar = AsyncMock(return_value=has_credit)
        return session

    async def test_delivers_and_clears_debt(self):
        from services.workers import credit_notifications as worker

        order = _topup_order(metadata_={"late_notify_pending": True})
        session = self._session(order, _user())
        with (
            patch.object(
                worker, "session_scope", return_value=_scope_with(session)
            ),
            patch.object(
                worker, "_send_late_push", new=AsyncMock(return_value=True)
            ) as mock_send,
        ):
            self.assertTrue(await worker._deliver_one(AsyncMock(), order.id))
            mock_send.assert_awaited_once()
        self.assertNotIn("late_notify_pending", order.metadata_)

    async def test_skips_still_held_without_consuming_attempts(self):
        from services.workers import credit_notifications as worker

        order = _topup_order(
            metadata_={"late_notify_pending": True, "settlement_held": True}
        )
        session = self._session(order, _user())
        with (
            patch.object(
                worker, "session_scope", return_value=_scope_with(session)
            ),
            patch.object(
                worker, "_send_late_push", new=AsyncMock(return_value=True)
            ) as mock_send,
        ):
            self.assertFalse(await worker._deliver_one(AsyncMock(), order.id))
            mock_send.assert_not_called()
        self.assertTrue(order.metadata_.get("late_notify_pending"))
        self.assertNotIn("late_notify_attempts", order.metadata_)

    async def test_skips_uncredited_order(self):
        from services.workers import credit_notifications as worker

        order = _topup_order(metadata_={"late_notify_pending": True})
        session = self._session(order, _user(), has_credit=0)
        with (
            patch.object(
                worker, "session_scope", return_value=_scope_with(session)
            ),
            patch.object(
                worker, "_send_late_push", new=AsyncMock(return_value=True)
            ) as mock_send,
        ):
            self.assertFalse(await worker._deliver_one(AsyncMock(), order.id))
            mock_send.assert_not_called()
        self.assertTrue(order.metadata_.get("late_notify_pending"))

    async def test_gives_up_after_max_attempts(self):
        from services.workers import credit_notifications as worker

        order = _topup_order(
            metadata_={
                "late_notify_pending": True,
                "late_notify_attempts": worker.MAX_NOTIFY_ATTEMPTS - 1,
            }
        )
        session = self._session(order, _user())
        with (
            patch.object(
                worker, "session_scope", return_value=_scope_with(session)
            ),
            patch.object(
                worker, "_send_late_push", new=AsyncMock(return_value=False)
            ),
        ):
            self.assertFalse(await worker._deliver_one(AsyncMock(), order.id))
        self.assertNotIn("late_notify_pending", order.metadata_)

    async def test_drops_debt_when_user_gone(self):
        from services.workers import credit_notifications as worker

        order = _topup_order(metadata_={"late_notify_pending": True})
        session = AsyncMock(spec=AsyncSession)
        session.get = AsyncMock(side_effect=[order, None])
        session.scalar = AsyncMock(return_value=1)
        with patch.object(
            worker, "session_scope", return_value=_scope_with(session)
        ):
            self.assertFalse(await worker._deliver_one(AsyncMock(), order.id))
        self.assertNotIn("late_notify_pending", order.metadata_)

    async def test_batch_counts_deliveries(self):
        from services.workers import credit_notifications as worker

        order_ids = [uuid.uuid4(), uuid.uuid4()]
        session = AsyncMock(spec=AsyncSession)
        result = MagicMock()
        result.scalars.return_value.all.return_value = order_ids
        session.execute = AsyncMock(return_value=result)
        with (
            patch.object(
                worker, "session_scope", return_value=_scope_with(session)
            ),
            patch.object(
                worker, "_deliver_one", new=AsyncMock(side_effect=[True, False])
            ),
        ):
            self.assertEqual(
                await worker.deliver_pending_credit_notifications(AsyncMock()), 1
            )


class TestWebhookNotifyWiring(unittest.IsolatedAsyncioTestCase):
    async def _run_webhook(self, order, user, send_ok: bool):
        from bot.handlers.webhook import yookassa_webhook_handler

        session = AsyncMock(spec=AsyncSession)
        session.scalar = AsyncMock(return_value=None)
        session.get = AsyncMock(return_value=user)
        scope = MagicMock()
        scope.__aenter__ = AsyncMock(return_value=session)
        scope.__aexit__ = AsyncMock(return_value=False)

        bot = AsyncMock()
        request = AsyncMock()
        request.content_length = 200
        request.app = {"bot": bot}
        request.json = AsyncMock(
            return_value={
                "type": "notification",
                "event": "payment.succeeded",
                "object": {
                    "id": "pay-777",
                    "status": "succeeded",
                    "metadata": {"order_id": str(order.id)},
                    "amount": {"value": "200.00", "currency": "RUB"},
                },
            }
        )

        with (
            patch("bot.handlers.webhook.session_scope", return_value=scope),
            patch("bot.handlers.webhook._get_real_ip", return_value="185.71.76.1"),
            patch("bot.handlers.webhook._is_yookassa_ip", return_value=True),
            patch(
                "services.order_service.OrderService.process_webhook_event",
                new=AsyncMock(return_value=order),
            ),
            patch(
                "bot.handlers.payment.credit_notify.notify_order_credited",
                new=AsyncMock(return_value=send_ok),
            ),
        ):
            response = await yookassa_webhook_handler(request)
        return response

    async def test_webhook_clears_debt_on_delivery(self):
        order = _topup_order(metadata_={"late_notify_pending": True})
        order._newly_paid = True
        response = await self._run_webhook(order, _user(), send_ok=True)
        self.assertEqual(response.status, 200)
        self.assertNotIn("late_notify_pending", order.metadata_)

    async def test_webhook_keeps_debt_on_send_failure(self):
        order = _topup_order(metadata_={"late_notify_pending": True})
        order._newly_paid = True
        response = await self._run_webhook(order, _user(), send_ok=False)
        self.assertEqual(response.status, 200)
        self.assertTrue(order.metadata_.get("late_notify_pending"))


if __name__ == "__main__":
    unittest.main()
