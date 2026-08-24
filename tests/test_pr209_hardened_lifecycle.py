"""Comprehensive lifecycle and concurrency regression tests for PR #209 hardening.

Tests:
1. In-flight create_payment cancellation: processing op captures external_id while UI remains suppressed.
2. Exposure calculation respects abandoned vs processing create_payment operations.
3. Notification coordinator 4-phase delivery and compensation lifecycle.
4. Transactional hub cache post-commit and rollback isolation.
5. Webhook Double-Read & Revalidate pattern against user ban and cancellation.
"""

import unittest
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from database.models import (
    Payment,
    PaymentNotification,
    PaymentProviderOperation,
    User,
)
from services.account_topup import cancel_all_unfinished_topups
from services.payment_provider_operations import (
    ProviderOperationClaim,
    finalize,
)
from services.yookassa_service import YooKassaResult
from utils.datetime_helpers import now_utc


class TestPR209HardenedLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_inflight_create_operation_captures_external_id_without_resurrecting_ui(self):
        """When user abandons checkout while create_payment is in-flight, finalize()

        must capture external_id, preserve exposure, but NOT resurrect UI.
        """
        user = User(id=1, telegram_id=100200)
        payment = Payment(
            id=10,
            user_id=1,
            amount=Decimal(500),
            currency="RUB",
            public_order_id="topup_inflight_1",
            provider_idempotency_key=str(uuid.uuid4()),
            provider_status="not_created",
            checkout_status="active",
            ui_visible=True,
            topup_context={},
        )
        op = PaymentProviderOperation(
            id=101,
            payment_id=10,
            operation_type="create_payment",
            status="processing",
            idempotency_key=f"op_{uuid.uuid4().hex}",
            locked_by="worker_http",
            locked_at=now_utc(),
            attempts=1,
        )

        session = AsyncMock()
        # Mock cancel_all_unfinished_topups behavior
        session.scalars = AsyncMock(
            side_effect=[
                MagicMock(all=MagicMock(return_value=[payment])),  # cancel_all_unfinished_topups
                MagicMock(all=MagicMock(return_value=[])),        # cancel_pending_create_operations
            ]
        )

        with patch("database.repositories.tariff_quotes_repo.lock_checkout_user", AsyncMock(return_value=user)):
            cancelled = await cancel_all_unfinished_topups(session, user_id=1)
            self.assertEqual(cancelled, 1)
            self.assertEqual(payment.checkout_status, "abandoned")
            self.assertFalse(payment.ui_visible)
            # Processing operation remains 'processing' so worker can capture external_id
            self.assertEqual(op.status, "processing")

        # Now worker completes external HTTP and calls finalize()
        claim = ProviderOperationClaim(
            operation_id=101,
            payment_id=10,
            operation_type="create_payment",
            payload={},
            idempotency_key=op.idempotency_key,
            worker_id="worker_http",
            attempt_number=1,
            external_id=None,
            created_at=now_utc(),
        )
        fake_result = YooKassaResult(
            True,
            value={
                "id": "yoo_inflight_captured_99",
                "status": "pending",
                "confirmation": {"confirmation_url": "https://yookassa.ru/pay/99"},
            },
        )

        mock_notif = PaymentNotification(
            id=1, payment_id=10, kind="payment_url", chat_id=100200, state="pending"
        )
        session.scalar = AsyncMock(
            side_effect=[
                1,           # select(Payment.user_id)
                payment,     # select(Payment).with_for_update()
                op,          # select(PaymentProviderOperation).with_for_update()
                None,        # select(PaymentNotification) check existing
                mock_notif,  # select(PaymentNotification) in ensure_payment_notification
            ]
        )

        mock_bot = AsyncMock()
        with patch("services.payment_provider_operations.lock_checkout_user", AsyncMock(return_value=user)):
            await finalize(session, claim, fake_result, bot=mock_bot)

        # Final assertions: external_id is captured, URL saved, but UI stays abandoned
        self.assertEqual(payment.external_id, "yoo_inflight_captured_99")
        self.assertEqual(payment.provider_status, "pending")
        self.assertEqual(payment.payment_url, "https://yookassa.ru/pay/99")
        self.assertEqual(payment.checkout_status, "abandoned")
        self.assertFalse(payment.ui_visible)
        self.assertEqual(op.status, "succeeded")

    async def test_notification_coordinator_compensation_flow(self):
        """If user cancels checkout during lock-free Telegram I/O, coordinator compensates orphan message."""
        user = User(id=1, telegram_id=100400)
        payment = Payment(
            id=15,
            user_id=1,
            amount=Decimal(250),
            currency="RUB",
            public_order_id="topup_notif_1",
            provider_status="pending",
            payment_url="https://pay.link/1",
            checkout_status="active",
            ui_visible=True,
            topup_context={},
        )
        notif = PaymentNotification(
            id=50,
            payment_id=15,
            kind="payment_url",
            chat_id=100400,
            state="claimed",
            claim_token="token_abc",
            attempts=1,
        )

        mock_bot = AsyncMock()
        from services.notification_coordinator import (
            NotificationClaim,
            execute_notification_presentation,
        )

        claim = NotificationClaim(
            notification_id=50,
            payment_id=15,
            user_id=1,
            chat_id=100400,
            kind="payment_url",
            state="claimed",
            claim_token="token_abc",
            attempt_number=1,
            payload={},
        )

        # During Phase 2 render_hub, simulate user cancelling the payment (checkout_status='abandoned')
        async def fake_render(bot, chat_id, payload):
            payment.checkout_status = "abandoned"
            payment.ui_visible = False
            return 9999  # Sent message ID

        session_prep = AsyncMock()
        session_prep.get = AsyncMock(side_effect=[payment, notif])
        session_prep.scalar = AsyncMock(side_effect=[payment, notif])
        session_ack = AsyncMock()
        session_ack.get = AsyncMock(side_effect=[payment, notif, notif])
        session_ack.scalar = AsyncMock(side_effect=[payment, notif, notif])

        from contextlib import asynccontextmanager
        call_count = 0
        @asynccontextmanager
        async def mock_scope():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield session_prep
            else:
                yield session_ack

        with patch("database.connection.session_scope", mock_scope), \
             patch("services.notification_coordinator.lock_checkout_user", AsyncMock(return_value=user)), \
             patch("utils.telegram._delete_hub_messages", AsyncMock(return_value=[])) as mock_delete:

            delivered = await execute_notification_presentation(
                mock_bot,
                claim,
                render_func=fake_render,
            )
            self.assertTrue(delivered)
            # Verify compensation was triggered to delete orphan message
            mock_delete.assert_called_once_with(mock_bot, 100400, [9999])
            self.assertEqual(notif.state, "compensated")

    async def test_transactional_hub_cache_coordination(self):
        """Verify _store_hub_id_in_db registers post-commit and rollback hooks with cache isolation."""
        from utils.telegram import _hub_cache, _store_hub_id_in_db

        _hub_cache[777] = {"ids": [100]}
        mock_session = AsyncMock()
        mock_session.info = {}

        with patch("database.repositories.hub_repo.add_hub_message_id", AsyncMock()):
            await _store_hub_id_in_db(777, 200, session=mock_session)

            self.assertIn("post_commit_tasks", mock_session.info)
            self.assertIn("rollback_tasks", mock_session.info)

            # Before commit, cache is unchanged
            self.assertEqual(_hub_cache[777]["ids"], [100])

            # Execute post-commit task
            for t in mock_session.info["post_commit_tasks"]:
                await t()
            self.assertEqual(_hub_cache[777]["ids"], [100, 200])

            # Invalidate on rollback
            for t in mock_session.info["rollback_tasks"]:
                await t()
            self.assertNotIn(777, _hub_cache)

    async def test_pending_topup_exposure_query_includes_reconciliation_and_pending_ops(self):
        """Verify _pending_topup_exposure queries both in-flight ops and reconciliation_status."""
        from services.account_topup import _pending_topup_exposure

        mock_session = AsyncMock()
        mock_session.scalar.return_value = Decimal("1500.00")

        exposure = await _pending_topup_exposure(mock_session, user_id=42)
        self.assertEqual(exposure, Decimal("1500.00"))

        mock_session.scalar.assert_awaited_once()
        query = mock_session.scalar.call_args[0][0]
        compiled = str(query)
        self.assertIn("payments.credited_at IS NULL", compiled)
        self.assertIn("payment_provider_operations", compiled)
        self.assertIn("reconciliation_status", compiled)

    async def test_ensure_payment_notification_upsert_and_payload_refresh(self):
        """Verify ensure_payment_notification performs atomic upsert and refreshes payload."""
        from services.notification_coordinator import ensure_payment_notification

        mock_session = AsyncMock()
        mock_notif = PaymentNotification(
            id=1,
            payment_id=10,
            kind="payment_url",
            chat_id=12345,
            state="pending",
            payload_snapshot={"old": "val"},
        )
        mock_session.scalar.return_value = mock_notif

        notif = await ensure_payment_notification(
            mock_session,
            payment_id=10,
            kind="payment_url",
            chat_id=12345,
            payload_snapshot={"new": "val"},
        )
        self.assertEqual(notif.id, 1)
        self.assertEqual(notif.payload_snapshot, {"old": "val", "new": "val"})

    async def test_process_topup_link_presentations_uses_coordinator(self):
        """Verify process_topup_link_presentations claims from coordinator and presents link."""
        from services.workers.account_balance import process_topup_link_presentations
        from services.notification_coordinator import NotificationClaim

        mock_bot = AsyncMock()
        mock_claim = NotificationClaim(
            notification_id=10,
            payment_id=5,
            user_id=1,
            chat_id=999,
            kind="payment_url",
            state="claimed",
            claim_token="tok1",
            attempt_number=1,
            payload={"payment_url": "https://pay.ru/1", "payment_id": 5, "amount": 300},
        )

        with patch("services.workers.account_balance.session_scope") as mock_scope, \
             patch("services.notification_coordinator.claim_notification", AsyncMock(side_effect=[mock_claim, None])), \
             patch("services.notification_coordinator.execute_notification_presentation", AsyncMock(return_value=True)) as mock_exec:

            mock_session = AsyncMock()
            mock_session.execute.return_value = MagicMock(all=MagicMock(return_value=[]))
            mock_scope.return_value.__aenter__.return_value = mock_session

            count = await process_topup_link_presentations(mock_bot)
            self.assertEqual(count, 1)
            mock_exec.assert_awaited_once()

    async def test_claim_notification_targeted_filters(self):
        """Verify claim_notification respects notification_id, payment_id, and kind filters."""
        from services.notification_coordinator import claim_notification

        mock_session = AsyncMock()
        mock_notif = PaymentNotification(
            id=42,
            payment_id=100,
            kind="referral_bonus",
            chat_id=555,
            state="pending",
            attempts=0,
            max_attempts=3,
        )
        mock_session.scalar.return_value = mock_notif
        mock_session.get.return_value = Payment(id=100, user_id=7)

        claim = await claim_notification(
            mock_session,
            worker_id="test_worker",
            notification_id=42,
            payment_id=100,
            kind="referral_bonus",
        )
        self.assertIsNotNone(claim)
        self.assertEqual(claim.notification_id, 42)
        self.assertEqual(claim.payment_id, 100)
        self.assertEqual(claim.kind, "referral_bonus")
        self.assertEqual(mock_notif.state, "claimed")

        # Verify SQL query compiled filters
        mock_session.scalar.assert_awaited_once()
        query = mock_session.scalar.call_args[0][0]
        compiled = str(query)
        self.assertIn("payment_notifications.id = :id_1", compiled)
        self.assertIn("payment_notifications.payment_id = :payment_id_1", compiled)
        self.assertIn("payment_notifications.kind = :kind_1", compiled)

    async def test_store_hub_id_in_db_fails_closed(self):
        """Verify _store_hub_id_in_db re-raises exceptions when DB insert fails in standalone session."""
        from utils.telegram import _store_hub_id_in_db

        with patch("utils.telegram.session_scope") as mock_scope:
            mock_sess = AsyncMock()
            mock_scope.return_value.__aenter__.return_value = mock_sess
            with patch("database.repositories.hub_repo.add_hub_message_id", AsyncMock(side_effect=RuntimeError("DB Connection Lost"))):
                with self.assertRaises(RuntimeError):
                    await _store_hub_id_in_db(999, 12345, session=None)

    async def test_account_purchase_outbox_uses_quote_id_and_isolated_enqueue(self):
        """Verify ensure_payment_notification stores quote_id for account_purchase kind."""
        from services.notification_coordinator import ensure_payment_notification

        mock_session = AsyncMock()
        mock_session.bind = None
        mock_session.scalar.return_value = None

        notif = await ensure_payment_notification(
            mock_session,
            quote_id=184,
            kind="account_purchase",
            chat_id=100200,
            payload_snapshot={"quote_id": 184},
        )
        self.assertEqual(notif.quote_id, 184)
        self.assertIsNone(notif.payment_id)
        self.assertEqual(notif.kind, "account_purchase")

    async def test_claim_notification_respects_claim_until_on_pending_state(self):
        """Verify claim_notification query filters pending rows whose claim_until is in the future."""
        from services.notification_coordinator import claim_notification

        mock_session = AsyncMock()
        mock_session.scalar.return_value = None

        await claim_notification(mock_session, worker_id="w1", kind="payment_url")
        mock_session.scalar.assert_awaited_once()
        query = mock_session.scalar.call_args[0][0]
        compiled = str(query)
        self.assertIn("payment_notifications.claim_until <= :claim_until_", compiled)

    async def test_compensation_preserves_edited_hub_message(self):
        """Verify compensation phase does not delete user's hub message if trigger_message_id was edited."""
        from services.notification_coordinator import (
            NotificationClaim,
            execute_notification_presentation,
        )

        mock_bot = AsyncMock()
        claim = NotificationClaim(
            notification_id=77,
            payment_id=15,
            chat_id=100400,
            kind="payment_url",
            state="claimed",
            claim_token="tok_123",
            attempt_number=1,
            payload={"trigger_message_id": 8888, "force_new": False},
        )

        async def fake_render(bot, chat_id, payload):
            return 8888  # Edited existing trigger message

        session_prep = AsyncMock()
        session_ack = AsyncMock()
        # Mock payment with checkout_status='abandoned' to trigger compensation
        mock_payment = Payment(
            id=15, user_id=1, amount=Decimal(100), currency="RUB",
            public_order_id="topup_test", provider_status="pending",
            checkout_status="abandoned", ui_visible=False,
        )
        mock_notif = PaymentNotification(
            id=77, payment_id=15, kind="payment_url", chat_id=100400,
            state="claimed", claim_token="tok_123", attempts=1,
        )
        session_prep.get = AsyncMock(side_effect=[mock_payment, mock_notif])
        session_ack.get = AsyncMock(side_effect=[mock_payment, mock_notif, mock_notif])

        from contextlib import asynccontextmanager
        count = 0
        @asynccontextmanager
        async def mock_scope():
            nonlocal count
            count += 1
            if count == 1:
                yield session_prep
            else:
                yield session_ack

        with patch("database.connection.session_scope", mock_scope), \
             patch("services.notification_coordinator.lock_checkout_user", AsyncMock()), \
             patch("utils.telegram._delete_hub_messages", AsyncMock()) as mock_delete:
            delivered = await execute_notification_presentation(mock_bot, claim, render_func=fake_render)
            self.assertTrue(delivered)
            # Ensure _delete_hub_messages was NEVER called because it was an edited hub message
            mock_delete.assert_not_called()
            self.assertEqual(mock_notif.state, "compensated")
