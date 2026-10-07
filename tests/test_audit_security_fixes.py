from datetime import timedelta
from decimal import Decimal
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Order, User, WhiteInternetSubscription
from services.fulfillment_service import FulfillmentService
from services.order_service import OrderService
from services.white_internet_service import WhiteInternetService
from utils.datetime_helpers import now_utc


class TestAuditSecurityFixes(unittest.IsolatedAsyncioTestCase):
    async def test_h1_white_internet_checkout_wallet_order_blocks_financial_hold_and_topup_blocked(self):
        """H1: _checkout_wallet_order must block users with financial_hold or topup_blocked."""
        session = AsyncMock(spec=AsyncSession)
        user_hold = User(id=1, financial_hold=True, topup_blocked=False)
        user_blocked = User(id=2, financial_hold=False, topup_blocked=True)

        order_hold, failure_hold = await WhiteInternetService._checkout_wallet_order(
            session,
            user=user_hold,
            service_type="white_internet",
            tariff_id=1,
            amount_due=Decimal("100.00"),
            duration_days=30,
            operation="purchase",
        )
        self.assertIsNone(order_hold)
        self.assertIsNotNone(failure_hold)
        self.assertFalse(failure_hold[0])

        order_blocked, failure_blocked = await WhiteInternetService._checkout_wallet_order(
            session,
            user=user_blocked,
            service_type="white_internet",
            tariff_id=1,
            amount_due=Decimal("100.00"),
            duration_days=30,
            operation="purchase",
        )
        self.assertIsNone(order_blocked)
        self.assertIsNotNone(failure_blocked)
        self.assertFalse(failure_blocked[0])

    async def test_h2_awg_tariff_change_refund_restores_previous_subscription_end(self):
        """H2: Revoking an AWG tariff change order restores previous_subscription_end."""
        session = AsyncMock(spec=AsyncSession)
        now = now_utc()
        prev_end = now + timedelta(days=20)
        user = User(id=42, telegram_id=1001, subscription_end=now + timedelta(days=15), current_tariff_id=2)
        session.get.return_value = user

        order = Order(
            id=uuid.uuid4(),
            user_id=42,
            service_type="awg",
            amount_rub=Decimal("300.00"),
            duration_days=15,
            metadata_={
                "is_tariff_change": True,
                "previous_subscription_end": prev_end.isoformat(),
                "previous_tariff_id": 1,
            },
        )

        with patch("services.subscription.SubscriptionService.sync_access_state", new=AsyncMock()):
            await FulfillmentService.revoke_order(session, order)

        self.assertEqual(user.subscription_end, prev_end)
        self.assertEqual(user.current_tariff_id, 1)

    async def test_h6_extend_subscription_uses_locked_user(self):
        """H6: extend_subscription reads subscription_end from the locked database row."""
        from database.repositories.users_repo import extend_subscription

        session = AsyncMock(spec=AsyncSession)
        now = now_utc()
        stale_user = User(id=10, telegram_id=100, subscription_end=now + timedelta(days=5))
        fresh_locked_user = User(id=10, telegram_id=100, subscription_end=now + timedelta(days=15))

        session.scalar.return_value = fresh_locked_user

        with patch("database.repositories.users_repo.update_user", new=AsyncMock()) as mock_update:
            mock_update.side_effect = lambda s, u, **kwargs: u
            await extend_subscription(session, stale_user, days=10)

            # Invariant: extend was based on fresh_locked_user (15 + 10 = 25 days), NOT stale_user (5 + 10 = 15)
            call_kwargs = mock_update.call_args[1]
            expected_min = now + timedelta(days=24)
            self.assertGreaterEqual(call_kwargs["subscription_end"], expected_min)

    async def test_h7_white_internet_fulfillment_records_subscription_id_in_order_metadata(self):
        """H7: Fulfilling White Internet records created subscription ID into order metadata."""
        session = AsyncMock(spec=AsyncSession)
        user = User(id=42, telegram_id=1001)
        session.get.return_value = user

        mock_sub = WhiteInternetSubscription(id=999, user_id=42, status="ACTIVE")

        order = Order(
            id=uuid.uuid4(),
            user_id=42,
            service_type="white_internet",
            amount_rub=Decimal("200.00"),
            duration_days=30,
            metadata_={},
        )

        with patch("services.white_internet_service.WhiteInternetService.purchase_subscription", new=AsyncMock(return_value=(True, "OK", mock_sub))):
            with patch("database.repositories.white_internet_repo.get_subscription_by_user_id", new=AsyncMock(return_value=None)):
                await FulfillmentService.fulfill_order(session, order)

        self.assertEqual(order.metadata_.get("subscription_id"), 999)

    async def test_h8_underpayment_marks_manual_review_and_persists_external_id(self):
        """H8: mark_order_paid underpayment records underpaid, manual_review and persists external_id."""
        session = AsyncMock(spec=AsyncSession)
        order = Order(
            id=uuid.uuid4(),
            user_id=42,
            service_type="awg",
            amount_rub=Decimal("500.00"),
            status="pending",
            metadata_={},
        )
        session.scalar.return_value = order

        result = await OrderService.mark_order_paid(
            session,
            order.id,
            paid_amount_rub=Decimal("300.00"),
            external_id="ext-underpaid-123",
        )

        self.assertIsNone(result)
        self.assertEqual(order.external_id, "ext-underpaid-123")
        self.assertTrue(order.metadata_.get("underpaid"))
        self.assertEqual(order.metadata_.get("underpaid_expected"), "500.00")
        self.assertEqual(order.metadata_.get("underpaid_received"), "300.00")
        self.assertTrue(order.metadata_.get("manual_review"))
        self.assertEqual(order.metadata_.get("manual_review_reason"), "underpayment")

    async def test_h9_healthcheck_detects_worker_heartbeat_status(self):
        """H9: Webhook /health endpoint verifies worker heartbeat file status."""
        from aiohttp.test_utils import make_mocked_request
        from bot.handlers.webhook import healthcheck_handler
        import tempfile

        with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
            tf.write("STOPPED\n")
            tf.flush()
            temp_path = tf.name

        mock_redis = MagicMock()
        mock_redis.ping = AsyncMock(return_value=True)

        try:
            with patch.dict(os.environ, {"JUST1KBOT_HEARTBEAT_FILE": temp_path}):
                with patch("bot.handlers.webhook.session_scope"):
                    with patch("bot.handlers.webhook._get_healthcheck_redis", return_value=mock_redis):
                        import bot.handlers.webhook as wh
                        wh._healthcheck_cache = None
                        req = make_mocked_request("GET", "/health")
                        resp = await healthcheck_handler(req)
                        self.assertEqual(resp.status, 503)
                        self.assertIn("Workers", resp.text)
        finally:
            import asyncio
            def _cleanup():
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            await asyncio.to_thread(_cleanup)

    async def test_h9_healthcheck_detects_stale_worker_heartbeat_timestamp(self):
        """H9: Webhook /health endpoint verifies worker heartbeat file timestamp against wall clock."""
        from aiohttp.test_utils import make_mocked_request
        from bot.handlers.webhook import healthcheck_handler
        import tempfile
        import time

        # Stale timestamp: 300 seconds ago
        stale_ts = int(time.time()) - 300
        with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
            tf.write(f"{stale_ts}\n")
            tf.flush()
            temp_path = tf.name

        mock_redis = MagicMock()
        mock_redis.ping = AsyncMock(return_value=True)

        try:
            with patch.dict(os.environ, {"JUST1KBOT_HEARTBEAT_FILE": temp_path}):
                with patch("bot.handlers.webhook.session_scope"):
                    with patch("bot.handlers.webhook._get_healthcheck_redis", return_value=mock_redis):
                        import bot.handlers.webhook as wh
                        wh._healthcheck_cache = None
                        req = make_mocked_request("GET", "/health")
                        resp = await healthcheck_handler(req)
                        self.assertEqual(resp.status, 503)
                        self.assertIn("Workers Stale", resp.text)
        finally:
            import asyncio
            def _cleanup():
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            await asyncio.to_thread(_cleanup)

    async def test_revoke_order_skips_white_internet_auxiliary_operations(self):
        """WI: revoking topup or add_device_slot order does not deactivate user subscriptions."""
        session = AsyncMock(spec=AsyncSession)
        user = User(id=42, telegram_id=1001)
        session.get.return_value = user

        topup_order = Order(
            id=uuid.uuid4(),
            user_id=42,
            service_type="white_internet",
            amount_rub=Decimal("150.00"),
            metadata_={"operation": "topup"},
        )

        with patch("services.white_internet_service.WhiteInternetService.deactivate_user_subscriptions", new=AsyncMock()) as mock_deact:
            await FulfillmentService.revoke_order(session, topup_order)
            mock_deact.assert_not_called()

        slot_order = Order(
            id=uuid.uuid4(),
            user_id=42,
            service_type="white_internet",
            amount_rub=Decimal("100.00"),
            metadata_={"operation": "add_device_slot"},
        )

        with patch("services.white_internet_service.WhiteInternetService.deactivate_user_subscriptions", new=AsyncMock()) as mock_deact:
            await FulfillmentService.revoke_order(session, slot_order)
            mock_deact.assert_not_called()
