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
        from services.referral_bonus import ReferralBonusGrantResult

        mock_credit.return_value = AsyncMock()
        mock_bonus.return_value = ReferralBonusGrantResult()
        mock_fulfill.fulfill_order = AsyncMock()

        session = AsyncMock(spec=AsyncSession)
        order = _topup_order(metadata_={})
        await OrderService._settle_benefits(session, order, was_canceled=False)

        self.assertTrue(order.metadata_.get("late_notify_pending"))
        self.assertNotIn("late_notify_attempts", order.metadata_)

    @patch("services.order_service.FulfillmentService")
    @patch("services.referral_bonus.grant_referral_bonus_for_topup")
    @patch("services.order_service.create_order_credit")
    async def test_settle_benefits_arms_referrer_debt_on_grant(
        self, mock_credit, mock_bonus, mock_fulfill
    ):
        from services.order_service import OrderService
        from services.referral_bonus import ReferralBonusGrantResult

        mock_credit.return_value = AsyncMock()
        mock_bonus.return_value = ReferralBonusGrantResult(
            referrer_bonus=Decimal(15),
            referrer_user_id=7,
            referrer_telegram_id=777,
        )
        mock_fulfill.fulfill_order = AsyncMock()

        session = AsyncMock(spec=AsyncSession)
        order = _topup_order(metadata_={})
        await OrderService._settle_benefits(session, order, was_canceled=False)

        debt = order.metadata_.get("referrer_notify_pending")
        self.assertEqual(
            debt,
            {
                "user_id": 7,
                "telegram_id": 777,
                "bonus": "15",
                "bonus_rate_pct": 15,
                "tier_upgraded": False,
                "new_tier_name": None,
                "new_rate_pct": None,
            },
        )

    @patch("services.order_service.FulfillmentService")
    @patch("services.referral_bonus.grant_referral_bonus_for_topup")
    @patch("services.order_service.create_order_credit")
    async def test_settle_benefits_skips_referrer_debt_without_grant(
        self, mock_credit, mock_bonus, mock_fulfill
    ):
        from services.order_service import OrderService
        from services.referral_bonus import ReferralBonusGrantResult

        mock_credit.return_value = AsyncMock()
        mock_bonus.return_value = ReferralBonusGrantResult()
        mock_fulfill.fulfill_order = AsyncMock()

        session = AsyncMock(spec=AsyncSession)
        order = _topup_order(metadata_={})
        await OrderService._settle_benefits(session, order, was_canceled=False)

        self.assertTrue(order.metadata_.get("late_notify_pending"))
        self.assertNotIn("referrer_notify_pending", order.metadata_)


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

    async def test_tariff_uses_shared_card_builder(self):
        from bot.formatters import get_tariff_display_name
        from bot.handlers.payment.credit_notify import notify_order_credited

        bot = AsyncMock()
        session = AsyncMock(spec=AsyncSession)
        order = _topup_order(service_type="awg", device_limit=2)
        user = _user()
        snapshot = MagicMock(
            real_available=Decimal("100"), bonus_available=Decimal("0")
        )
        with (
            patch(
                "database.repositories.account_ledger_repo.get_account_balance",
                new=AsyncMock(return_value=snapshot),
            ),
            patch(
                "utils.telegram.render_hub", new=AsyncMock()
            ) as mock_render,
        ):
            self.assertTrue(
                await notify_order_credited(bot, session, order, user)
            )
            text = mock_render.await_args[0][2]
        self.assertIn(get_tariff_display_name(2), text)


def _scope_with(session):
    scope = MagicMock()
    scope.__aenter__ = AsyncMock(return_value=session)
    scope.__aexit__ = AsyncMock(return_value=False)
    return scope


def _empty_rows():
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    return result


class TestCreditNotifyWorker(unittest.IsolatedAsyncioTestCase):
    def _session(self, order, user, has_credit=1):
        session = AsyncMock(spec=AsyncSession)
        session.get = AsyncMock(side_effect=[order, user, order, order])
        session.scalar = AsyncMock(return_value=has_credit)
        session.execute = AsyncMock(return_value=_empty_rows())
        return session

    def _balance(self, worker):
        return patch.object(
            worker,
            "get_account_balance",
            new=AsyncMock(
                return_value=MagicMock(real_available=150, bonus_available=0)
            ),
        )

    async def test_delivers_and_clears_debt(self):
        from services.workers import credit_notifications as worker

        order = _topup_order(metadata_={"late_notify_pending": True})
        session = self._session(order, _user())
        with (
            patch.object(
                worker, "session_scope", return_value=_scope_with(session)
            ),
            self._balance(worker),
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

    async def test_skips_uncredited_order_with_bounded_attempts(self):
        from services.workers import credit_notifications as worker

        order = _topup_order(metadata_={"late_notify_pending": True})
        session = AsyncMock(spec=AsyncSession)
        # Anomaly path loads no user: snapshot order, then finalize order.
        session.get = AsyncMock(side_effect=[order, order])
        session.scalar = AsyncMock(return_value=0)
        session.execute = AsyncMock(return_value=_empty_rows())
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
        self.assertEqual(order.metadata_.get("late_notify_attempts"), 1)
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
            self._balance(worker),
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
        session.get = AsyncMock(side_effect=[order, None, order])
        session.scalar = AsyncMock(return_value=1)
        session.execute = AsyncMock(return_value=_empty_rows())
        with patch.object(
            worker, "session_scope", return_value=_scope_with(session)
        ):
            self.assertFalse(await worker._deliver_one(AsyncMock(), order.id))
        self.assertNotIn("late_notify_pending", order.metadata_)

    async def test_drops_debt_when_user_blocked(self):
        from services.workers import credit_notifications as worker

        order = _topup_order(metadata_={"late_notify_pending": True})
        session = AsyncMock(spec=AsyncSession)
        session.get = AsyncMock(
            side_effect=[order, _user(is_bot_blocked=True), order]
        )
        session.scalar = AsyncMock(return_value=1)
        session.execute = AsyncMock(return_value=_empty_rows())
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
        self.assertNotIn("late_notify_pending", order.metadata_)

    async def test_drops_debt_when_no_longer_paid(self):
        """A refund landed after settlement: never send a 'credited' push."""
        from services.workers import credit_notifications as worker

        order = _topup_order(
            status="refunded", metadata_={"late_notify_pending": True}
        )
        session = AsyncMock(spec=AsyncSession)
        session.get = AsyncMock(side_effect=[order, order])
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
        self.assertNotIn("late_notify_pending", order.metadata_)

    async def test_referrer_push_sent_and_debt_cleared(self):
        from services.workers import credit_notifications as worker

        order = _topup_order(
            metadata_={
                "late_notify_pending": True,
                "referrer_notify_pending": {
                    "user_id": 20,
                    "telegram_id": 555,
                    "bonus": "15",
                },
            }
        )
        session = AsyncMock(spec=AsyncSession)
        session.get = AsyncMock(
            side_effect=[order, _user(), _user(id=20, telegram_id=555), order]
        )
        session.scalar = AsyncMock(return_value=1)
        session.execute = AsyncMock(return_value=_empty_rows())
        with (
            patch.object(
                worker, "session_scope", return_value=_scope_with(session)
            ),
            self._balance(worker),
            patch.object(
                worker, "_send_late_push", new=AsyncMock(return_value=True)
            ),
            patch.object(
                worker, "_send_referrer_push", new=AsyncMock(return_value=True)
            ) as mock_ref,
        ):
            self.assertTrue(await worker._deliver_one(AsyncMock(), order.id))
            mock_ref.assert_awaited_once()
        self.assertNotIn("late_notify_pending", order.metadata_)
        self.assertNotIn("referrer_notify_pending", order.metadata_)

    async def test_referrer_skipped_without_debt(self):
        from services.workers import credit_notifications as worker

        order = _topup_order(metadata_={"late_notify_pending": True})
        session = self._session(order, _user())
        with (
            patch.object(
                worker, "session_scope", return_value=_scope_with(session)
            ),
            self._balance(worker),
            patch.object(
                worker, "_send_late_push", new=AsyncMock(return_value=True)
            ),
            patch.object(
                worker, "_send_referrer_push", new=AsyncMock(return_value=True)
            ) as mock_ref,
        ):
            self.assertTrue(await worker._deliver_one(AsyncMock(), order.id))
            mock_ref.assert_not_called()
        self.assertNotIn("late_notify_pending", order.metadata_)

    async def test_referrer_debt_survives_owner_delivery(self):
        """The reported loss: webhook clears the owner debt, but the
        referrer debt persists for the worker."""
        from services.workers import credit_notifications as worker

        order = _topup_order(
            metadata_={
                "referrer_notify_pending": {
                    "user_id": 20,
                    "telegram_id": 555,
                    "bonus": "15",
                }
            }
        )
        session = AsyncMock(spec=AsyncSession)
        session.get = AsyncMock(
            side_effect=[order, _user(id=20, telegram_id=555), order]
        )
        session.scalar = AsyncMock(return_value=1)
        with (
            patch.object(
                worker, "session_scope", return_value=_scope_with(session)
            ),
            self._balance(worker),
            patch.object(
                worker, "_send_late_push", new=AsyncMock(return_value=True)
            ) as mock_owner,
            patch.object(
                worker, "_send_referrer_push", new=AsyncMock(return_value=True)
            ) as mock_ref,
        ):
            self.assertTrue(await worker._deliver_one(AsyncMock(), order.id))
            mock_owner.assert_not_called()
            mock_ref.assert_awaited_once()
        self.assertNotIn("referrer_notify_pending", order.metadata_)

    async def test_late_push_topup_names_credited_notice(self):
        from services.workers import credit_notifications as worker

        with patch.object(
            worker, "safe_send_message", new=AsyncMock(return_value=99)
        ) as mock_send:
            self.assertTrue(
                await worker._send_late_push(
                    AsyncMock(), "topup",
                    {"context": None}, 123, 150, 0,
                )
            )
            text = mock_send.await_args[0][2]
        from bot import texts

        self.assertIn(texts.TOPUP_CREDITED_NOTICE, text)

    async def test_late_push_tariff_uses_tier_label(self):
        """Worker card must name the tariff exactly like the request path."""
        from bot.formatters import get_tariff_display_name
        from services.workers import credit_notifications as worker

        fields = {
            "context": None,
            "device_limit": 2,
            "is_tariff_change": False,
            "duration_days": 30,
            "amount_rub": Decimal("200"),
        }
        with patch.object(
            worker, "safe_send_message", new=AsyncMock(return_value=99)
        ) as mock_send:
            self.assertTrue(
                await worker._send_late_push(
                    AsyncMock(), "awg", fields, 123, 100, 0
                )
            )
            text = mock_send.await_args[0][2]
        self.assertIn(get_tariff_display_name(2), text)

    async def test_referrer_push_names_bonus(self):
        from services.workers import credit_notifications as worker

        with patch.object(
            worker, "safe_send_message", new=AsyncMock(return_value=99)
        ) as mock_send:
            self.assertTrue(
                await worker._send_referrer_push(
                    AsyncMock(), {"telegram_id": 555, "bonus": Decimal("15")}
                )
            )
            text = mock_send.await_args[0][2]
        from bot import texts

        self.assertIn("15", text)
        self.assertTrue(len(texts.REFERRAL_BONUS_ACCREDITED) > 0)

    async def test_referrer_push_formats_detailed_amount_and_balance(self):
        from services.workers import credit_notifications as worker

        with patch.object(
            worker, "safe_send_message", new=AsyncMock(return_value=99)
        ) as mock_send:
            self.assertTrue(
                await worker._send_referrer_push(
                    AsyncMock(),
                    {
                        "telegram_id": 555,
                        "bonus": Decimal("15"),
                        "bonus_rate_pct": 15,
                        "bonus_balance": 45,
                    },
                )
            )
            text = mock_send.await_args[0][2]
        self.assertIn("+15 ₽", text)
        self.assertIn("15%", text)
        self.assertIn("45 ₽", text)

    async def test_referrer_push_sends_tier_upgrade(self):
        from services.workers import credit_notifications as worker

        with patch.object(
            worker, "safe_send_message", new=AsyncMock(return_value=99)
        ) as mock_send:
            self.assertTrue(
                await worker._send_referrer_push(
                    AsyncMock(),
                    {
                        "telegram_id": 555,
                        "bonus": Decimal("20"),
                        "bonus_rate_pct": 20,
                        "bonus_balance": 100,
                        "tier_upgraded": True,
                        "new_tier_name": "Silver",
                        "new_rate_pct": 20,
                    },
                )
            )
            self.assertEqual(mock_send.await_count, 1)
            text = mock_send.await_args[0][2]
            self.assertIn("+20 ₽", text)
            self.assertIn("Silver", text)
            self.assertIn("20%", text)
            self.assertIn("100 ₽", text)

    async def test_referrer_failure_does_not_resurrect_cleared_owner_debt(self):
        from services.workers import credit_notifications as worker

        order = _topup_order(
            metadata_={
                "late_notify_pending": True,
                "referrer_notify_pending": {"user_id": 7, "telegram_id": 777, "bonus": "15"},
            }
        )
        session = AsyncMock(spec=AsyncSession)
        session.get = AsyncMock(return_value=order)
        with patch.object(worker, "session_scope", return_value=_scope_with(session)):
            delivered = await worker._finalize_delivery(order.id, owner_ok=True, ref_ok=False)

        self.assertTrue(delivered)
        self.assertNotIn("late_notify_pending", order.metadata_)
        self.assertIn("referrer_notify_pending", order.metadata_)
        self.assertEqual(order.metadata_.get("referrer_notify_attempts"), 1)
        self.assertNotIn("late_notify_attempts", order.metadata_)

    async def test_owner_failure_does_not_clear_or_corrupt_referrer_debt(self):
        from services.workers import credit_notifications as worker

        order = _topup_order(
            metadata_={
                "late_notify_pending": True,
                "referrer_notify_pending": {"user_id": 7, "telegram_id": 777, "bonus": "15"},
            }
        )
        session = AsyncMock(spec=AsyncSession)
        session.get = AsyncMock(return_value=order)
        with patch.object(worker, "session_scope", return_value=_scope_with(session)):
            delivered = await worker._finalize_delivery(order.id, owner_ok=False, ref_ok=True)

        self.assertTrue(delivered)
        self.assertTrue(order.metadata_.get("late_notify_pending"))
        self.assertEqual(order.metadata_.get("late_notify_attempts"), 1)
        self.assertNotIn("referrer_notify_pending", order.metadata_)
        self.assertNotIn("referrer_notify_attempts", order.metadata_)

    async def test_non_paid_order_drops_both_debts(self):
        from services.workers import credit_notifications as worker

        order = _topup_order(
            status="refunded",
            metadata_={
                "late_notify_pending": True,
                "referrer_notify_pending": {"user_id": 7, "telegram_id": 777, "bonus": "15"},
            },
        )
        session = AsyncMock(spec=AsyncSession)
        session.get = AsyncMock(return_value=order)
        with patch.object(worker, "session_scope", return_value=_scope_with(session)):
            delivered = await worker._finalize_delivery(order.id, owner_ok=True, ref_ok=True)

        self.assertFalse(delivered)
        self.assertNotIn("late_notify_pending", order.metadata_)
        self.assertNotIn("referrer_notify_pending", order.metadata_)

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
