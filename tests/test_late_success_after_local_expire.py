"""The local-expiry ↔ provider-success reconciliation contract.

Scenario (review-requested race test):
    T1  cleanup GET  → provider reports pending
    T2  user completes the payment
    T3  provider flips to succeeded
    T4  cleanup marks the local row canceled (provider-verified LOCAL cancel)

A late provider success must NEVER become a silent credit: the payment state
machine converts canceled_to_succeeded into mismatch/manual_review, and the
settlement guard refuses to credit manual-review rows. That is the expected
reconciliation path an operator resolves by hand.
"""

import os
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("BOT_TOKEN", "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
os.environ.setdefault("ADMIN_IDS", "[100]")
os.environ.setdefault("SUPPORT_USERNAME", "test_support")
os.environ.setdefault("DB_ENCRYPTION_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("REDIS_PASSWORD", "testpass")
os.environ.setdefault("YOOKASSA_SHOP_ID", "12345")
os.environ.setdefault("YOOKASSA_SECRET_KEY", "test_key")
os.environ.setdefault("YOOKASSA_RETURN_URL", "https://t.me/test_bot?start={bot_username}")
os.environ.setdefault("YOOKASSA_WEBHOOK_PORT", "8080")
os.environ.setdefault("DOMAIN", "myrealdomain.com")
os.environ.setdefault("SSL_EMAIL", "admin@myrealdomain.com")
# NOTE: no DATABASE_URL setdefault here — this module must not flip the
# skipUnless live-database marker of tests/test_database_startup.py.

from services.account_topup import settle_succeeded_topup
from services.payment_provider_state import apply_provider_transition
from utils.datetime_helpers import now_utc


def _expired_locally_payment():
    from database.models import Payment

    return Payment(
        id=17,
        user_id=1,
        amount=Decimal("35.00"),
        currency="RUB",
        public_order_id="topup_local_expire",
        provider_idempotency_key="key",
        provider_status="canceled",
        fulfillment_status="failed",
        reconciliation_status="ok",
        manual_review_reason="auto_expired_abandoned_pending_48h",
        external_id="ext-17",
    )


def _late_success_snapshot():
    return {
        "id": "ext-17",
        "status": "succeeded",
        "captured_at": "2026-08-30T10:00:00+00:00",
        "amount": {"value": "35.00", "currency": "RUB"},
        "metadata": {
            "order_id": "topup_local_expire",
            "local_payment_id": "17",
        },
    }


def _recording_session():
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.scalar = AsyncMock(return_value=0)
    scalars_result = MagicMock()
    scalars_result.all.return_value = []
    session.scalars = AsyncMock(return_value=scalars_result)
    return session


def _added_event_types(session):
    return [call.args[0].event_type for call in session.add.call_args_list]


class LateSuccessAfterLocalExpireTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_expire_then_provider_success_is_conflict(self):
        payment = _expired_locally_payment()
        session = _recording_session()

        transition = await apply_provider_transition(
            session,
            payment,
            _late_success_snapshot(),
            source="webhook_inbox",
            event_type="payment.succeeded",
        )

        self.assertEqual(transition.outcome, "conflict")
        self.assertEqual(transition.reason, "canceled_to_succeeded")
        # These two states are exactly what blocks settlement below.
        self.assertEqual(payment.reconciliation_status, "mismatch")
        self.assertEqual(payment.fulfillment_status, "manual_review")
        self.assertEqual(payment.manual_review_reason, "canceled_to_succeeded")
        # An audit event was recorded for the operator.
        self.assertIn("provider_transition_conflict", _added_event_types(session))

    async def test_settlement_refuses_manual_review_row(self):
        payment = _expired_locally_payment()
        # Force the post-transition state produced by the test above.
        payment.reconciliation_status = "mismatch"
        payment.fulfillment_status = "manual_review"
        payment.manual_review_reason = "canceled_to_succeeded"
        payment.provider_confirmed_at = now_utc()
        session = _recording_session()

        settled, _snapshot = await settle_succeeded_topup(
            session, payment=payment, source="webhook_inbox"
        )

        self.assertFalse(settled)
        # The blocked-settlement marker was recorded instead of a credit.
        self.assertIn(
            "topup_settlement_blocked_manual_review", _added_event_types(session)
        )
        self.assertIsNone(payment.credited_at)


if __name__ == "__main__":
    unittest.main()
