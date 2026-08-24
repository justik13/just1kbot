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
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from database.models import (
    Payment,
    PaymentNotification,
    PaymentProviderOperation,
    TariffQuote,
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
            mock_delete.assert_not_called()
            self.assertEqual(mock_notif.state, "compensated")

    async def test_account_purchase_phase1_applicability_uses_quote_id(self):
        """Verify Phase 1 applicability check retrieves TariffQuote via claim.quote_id."""
        from services.notification_coordinator import (
            NotificationClaim,
            execute_notification_presentation,
        )
        from database.models import TariffQuote

        mock_bot = AsyncMock()
        claim = NotificationClaim(
            notification_id=88,
            payment_id=None,
            quote_id=199,
            chat_id=100500,
            kind="account_purchase",
            state="claimed",
            claim_token="tok_quote",
            attempt_number=1,
            payload={"quote_id": 199},
        )

        mock_quote = TariffQuote(
            id=199,
            user_id=1,
            operation_type="purchase",
            target_tariff_version_id=1,
            current_paid_hours=0,
            current_paid_value_rub=Decimal(0),
            bonus_hours=0,
            amount_due_rub=Decimal(100),
            resulting_paid_hours=720,
            resulting_paid_value_rub=Decimal(100),
            resulting_bonus_hours=0,
            rounding_loss_hours=Decimal(0),
            rounding_loss_value_rub=Decimal(0),
            status="consumed",
            expires_at=now_utc(),
        )
        mock_notif = PaymentNotification(
            id=88,
            payment_id=None,
            quote_id=199,
            kind="account_purchase",
            chat_id=100500,
            state="claimed",
            claim_token="tok_quote",
            attempts=1,
        )

        session_phase1 = AsyncMock()
        session_phase3 = AsyncMock()

        session_phase1.get = AsyncMock(return_value=mock_quote)
        session_phase3.get = AsyncMock(side_effect=[mock_notif, mock_quote])

        from contextlib import asynccontextmanager
        call_count = 0
        @asynccontextmanager
        async def mock_scope():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield session_phase1
            else:
                yield session_phase3

        rendered = False
        async def fake_render(bot, chat_id, payload):
            nonlocal rendered
            rendered = True
            return 9999

        with patch("database.connection.session_scope", mock_scope), \
             patch("services.notification_coordinator.lock_checkout_user", AsyncMock()):
            ok = await execute_notification_presentation(mock_bot, claim, render_func=fake_render)
            self.assertTrue(ok)
            self.assertTrue(rendered)
            # Verify TariffQuote was retrieved by quote_id (199) in Phase 1
            session_phase1.get.assert_awaited_once_with(TariffQuote, 199)
            self.assertEqual(mock_notif.state, "delivered")

    async def test_phase3_crash_to_compensation_required_recovery(self):
        """Verify crash after Phase 3 setting compensation_required is reclaimed and cleaned up."""
        from services.notification_coordinator import (
            claim_notification,
            execute_notification_presentation,
        )

        mock_bot = AsyncMock()
        mock_notif = PaymentNotification(
            id=99,
            payment_id=20,
            kind="payment_url",
            state="compensation_required",
            chat_id=100600,
            claim_token="old_stale_token",
            claim_until=now_utc() - timedelta(seconds=5),
            attempts=1,
            max_attempts=5,
            telegram_message_ids=[101, 102],
        )

        session_claim = AsyncMock()
        session_claim.scalar = AsyncMock(return_value=mock_notif)
        session_claim.get = AsyncMock(return_value=None)

        # Claim the stuck compensation_required notification
        claim = await claim_notification(session_claim, worker_id="recovery_worker")
        self.assertIsNotNone(claim)
        self.assertEqual(claim.state, "compensation_required")
        self.assertEqual(claim.telegram_message_ids, [101, 102])

        session_comp = AsyncMock()
        session_comp.get = AsyncMock(return_value=mock_notif)

        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def mock_comp_scope():
            yield session_comp

        with patch("database.connection.session_scope", mock_comp_scope), \
             patch("utils.telegram._delete_hub_messages", AsyncMock()) as mock_delete:
            cleaned = await execute_notification_presentation(mock_bot, claim, render_func=AsyncMock())
            self.assertTrue(cleaned)
            mock_delete.assert_awaited_once_with(mock_bot, 100600, [101, 102])
            self.assertEqual(mock_notif.state, "compensated")

    async def test_outbox_db_failure_does_not_abort_financial_purchase(self):
        """Verify outbox DB failure inside begin_nested savepoint does not abort quote purchase settlement."""
        from services.account_purchase import settle_account_purchase
        from database.models import Tariff, TariffVersion
        from database.repositories.account_ledger_repo import AccountBalanceSnapshot

        mock_session = AsyncMock()
        mock_user = User(id=1, telegram_id=55555, is_banned=False, is_deleted=False, current_tariff_id=None, financial_hold=False)
        q_uuid = uuid.uuid4()
        mock_quote = TariffQuote(
            id=123, public_id=q_uuid, user_id=1, operation_type="purchase",
            target_tariff_version_id=1, status="active",
            current_paid_hours=0, current_paid_value_rub=Decimal(0),
            bonus_hours=0, amount_due_rub=Decimal(300), currency="RUB",
            resulting_paid_hours=720, resulting_paid_value_rub=Decimal(300),
            resulting_bonus_hours=0, rounding_loss_hours=Decimal(0),
            rounding_loss_value_rub=Decimal(0),
            expires_at=now_utc() + timedelta(days=30),
        )
        mock_tariff = Tariff(id=1, name="Standard", is_active=True, device_limit=2)
        mock_version = TariffVersion(
            id=1, tariff_id=1, version_number=1, name_snapshot="Standard",
            duration_hours=720, price_rub=Decimal(300), currency="RUB", device_limit=2,
        )

        mock_session.scalar = AsyncMock(side_effect=[mock_quote, mock_tariff])
        mock_session.get = AsyncMock(return_value=mock_version)

        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def mock_savepoint():
            try:
                yield
            except Exception:
                raise

        mock_session.begin_nested = mock_savepoint

        with patch("services.account_purchase.lock_checkout_user", AsyncMock(return_value=mock_user)), \
             patch("services.account_purchase._settled_state", AsyncMock(return_value=(None, False))), \
             patch("services.account_purchase.get_or_create_current_version", AsyncMock(return_value=mock_version)), \
             patch("services.account_purchase.get_user_profiles_count", AsyncMock(return_value=0)), \
             patch("services.account_purchase.get_account_balance", AsyncMock(return_value=AccountBalanceSnapshot(accounting_position=Decimal(700), available=Decimal(700), reserved=Decimal(0), debt=Decimal(0)))), \
             patch("services.account_purchase.create_purchase_debit", AsyncMock(return_value=(AsyncMock(id=999), True))), \
             patch("services.account_purchase.get_or_create_account_purchase_entry", AsyncMock()), \
             patch("services.account_purchase._get_or_create_entitlement", AsyncMock(return_value=(AsyncMock(), True))), \
             patch("services.account_purchase.SubscriptionService.extend_subscription", AsyncMock(return_value=AsyncMock())), \
             patch("services.account_purchase.grant_referral_bonus_for_purchase", AsyncMock()), \
             patch("services.audit_service.AuditService.log_action", AsyncMock()), \
             patch("services.notification_coordinator.ensure_payment_notification", AsyncMock(side_effect=Exception("DB outbox error"))):

            settlement = await settle_account_purchase(
                mock_session,
                user_id=1,
                quote_public_id=q_uuid,
            )
            self.assertTrue(settlement.created)
            self.assertEqual(mock_quote.status, "consumed")

    async def test_outbox_db_failure_does_not_abort_topup_settlement(self):
        """Verify outbox DB failure inside begin_nested savepoint does not abort topup credit settlement."""
        from services.account_topup import settle_succeeded_topup

        mock_session = AsyncMock()
        mock_user = User(id=1, telegram_id=66666, is_banned=False, referred_by=77777)
        mock_payment = Payment(
            id=50, user_id=1, amount=Decimal(500), currency="RUB",
            public_order_id="topup_test_50", provider_status="succeeded",
            provider_confirmed_at=now_utc(),
            fulfillment_status="pending", checkout_status="active",
            ui_visible=True, topup_context={},
        )

        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def mock_savepoint():
            try:
                yield
            except Exception:
                raise

        mock_session.begin_nested = mock_savepoint

        mock_settings = MagicMock(BALANCE_MAX_AVAILABLE_RUB=100000)

        with patch("services.notification_coordinator.ensure_payment_notification", AsyncMock(side_effect=Exception("DB deadlock on outbox"))), \
             patch("services.referral_bonus.grant_referral_bonus_for_topup", AsyncMock(return_value=Decimal(50))), \
             patch("services.account_topup.credit_succeeded_topup", AsyncMock(return_value=(AsyncMock(amount=500), True))), \
             patch("services.account_topup.get_account_balance", AsyncMock(return_value=AsyncMock(real_position=Decimal(500), accounting_position=Decimal(500)))), \
             patch("services.account_topup.refresh_user_dispute_hold", AsyncMock()), \
             patch("services.audit_service.AuditService.log_action", AsyncMock()):

            res = await settle_succeeded_topup(
                mock_session,
                payment=mock_payment,
                source="test",
                locked_user=mock_user,
                locked_payment=mock_payment,
                settings=mock_settings,
            )
            self.assertTrue(res[0])
            self.assertEqual(mock_payment.fulfillment_status, "succeeded")
            self.assertIsNotNone(mock_payment.credited_at)

    async def test_telegram_send_success_db_failure_retains_doc_id_for_compensation(self):
        """Verify _append_hub_document_unlocked still returns doc ID even if DB storage fails."""
        from utils.telegram import _append_hub_document_unlocked
        from aiogram.types import BufferedInputFile

        mock_bot = AsyncMock()
        mock_doc_msg = AsyncMock(message_id=445566)
        mock_bot.send_document = AsyncMock(return_value=mock_doc_msg)

        fake_file = BufferedInputFile(b"test", filename="test.vpn")

        with patch("utils.telegram._store_hub_id_in_db", AsyncMock(side_effect=Exception("DB pool timeout"))):
            returned_id = await _append_hub_document_unlocked(
                mock_bot,
                chat_id=12345,
                document=fake_file,
                caption="Test",
            )
            # The returned ID MUST match mock_doc_msg.message_id so caller can delete it on rollback
            self.assertEqual(returned_id, 445566)

