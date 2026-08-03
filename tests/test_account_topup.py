import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from database.models import Payment
from services.payment_provider_operations import create_payload
from services.payment_provider_state import apply_provider_transition
from services.payment_provider_validation import validate_provider_payment


def topup() -> Payment:
    return Payment(
        id=17,
        user_id=3,
        amount=Decimal("499"),
        currency="RUB",
        public_order_id="topup_public",
        provider_idempotency_key="topup_idempotency",
        provider_status="pending",
        fulfillment_status="not_ready",
        reconciliation_status="ok",
        checkout_status="active",
        ui_visible=True,
        topup_context={},
        external_id="provider-17",
    )


def provider_snapshot(payment: Payment, **overrides) -> dict:
    value = {
        "id": payment.external_id,
        "status": "succeeded",
        "captured_at": "2026-08-02T06:00:00Z",
        "amount": {"value": "499.00", "currency": "RUB"},
        "metadata": {
            "order_id": payment.public_order_id,
            "local_payment_id": str(payment.id),
        },
    }
    value.update(overrides)
    return value


class AccountTopupProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_post_cannot_prove_topup_success(self):
        payment = topup()
        transition = await apply_provider_transition(
            AsyncMock(),
            payment,
            provider_snapshot(payment),
            source="provider_create_payment_post",
        )
        self.assertEqual(transition.outcome, "retry")
        self.assertEqual(payment.provider_status, "pending")

    async def test_verified_get_records_capture_and_allows_credit_route(self):
        payment = topup()
        transition = await apply_provider_transition(
            AsyncMock(),
            payment,
            provider_snapshot(payment),
            source="provider_get_payment",
        )
        self.assertEqual(transition.outcome, "applied")
        self.assertEqual(payment.provider_status, "succeeded")
        self.assertEqual(
            payment.provider_confirmed_at,
            datetime(2026, 8, 2, 6, tzinfo=timezone.utc),
        )

    async def test_missing_capture_moves_topup_to_manual_review(self):
        payment = topup()
        data = provider_snapshot(payment)
        data.pop("captured_at")
        session = MagicMock()
        transition = await apply_provider_transition(
            session, payment, data, source="provider_get_payment"
        )
        self.assertEqual(transition.outcome, "conflict")
        self.assertEqual(payment.fulfillment_status, "manual_review")
        session.add.assert_called_once()

    def test_topup_model_has_no_subscription_checkout_fields(self):
        columns = Payment.__table__.c
        self.assertNotIn("tariff_id", columns)
        self.assertNotIn("tariff_quote_id", columns)
        self.assertNotIn("payment_kind", columns)

    def test_unexpected_kopecks_are_not_rounded(self):
        payment = topup()
        data = provider_snapshot(payment)
        data["amount"] = {"value": "499.01", "currency": "RUB"}
        self.assertEqual(validate_provider_payment(payment, data), "amount_mismatch")

    def test_provider_payload_preserves_whole_ruble_contract(self):
        payment = topup()
        payload = create_payload(payment, "Пополнение баланса", "https://t.me/bot")
        self.assertEqual(payload["amount"]["value"], "499.00")
        self.assertEqual(payload["amount"]["currency"], "RUB")
        self.assertEqual(payload["description"], "Пополнение баланса")
        self.assertEqual(
            payload["metadata"],
            {"order_id": "topup_public", "local_payment_id": "17"},
        )

    def test_durable_notification_worker_is_registered(self):
        source = (
            Path(__file__).parents[1] / "services" / "workers" / "__init__.py"
        ).read_text(encoding="utf-8")
        self.assertIn('WorkerDefinition("account_balance", _account_balance, False)', source)


if __name__ == "__main__":
    unittest.main()
