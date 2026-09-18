from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, MagicMock

from bot.handlers.admin.payments import get_payment_refundable_remainder
from database.models import Payment, PaymentRefund
from services.payment_provider_state import (
    PaymentFulfillmentStatus,
    PaymentProviderStatus,
    PaymentReconciliationStatus,
    apply_provider_transition,
)


class RefundEntitlementRevocationTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_payment_refundable_remainder_full(self):
        session = AsyncMock()
        payment = Payment(
            id=101,
            amount=Decimal("250.00"),
            currency="RUB",
            provider_status="succeeded",
        )
        payment.refunds = []

        refundable = await get_payment_refundable_remainder(session, payment)
        self.assertEqual(refundable, Decimal("250.00"))

    async def test_get_payment_refundable_remainder_partial(self):
        session = AsyncMock()
        payment = Payment(
            id=101,
            amount=Decimal("250.00"),
            currency="RUB",
            provider_status="succeeded",
        )
        refund1 = PaymentRefund(
            payment_id=101,
            amount=Decimal("50.00"),
            currency="RUB",
            provider_status="succeeded",
            event_key="evt_1",
        )
        payment.refunds = [refund1]

        refundable = await get_payment_refundable_remainder(session, payment)
        self.assertEqual(refundable, Decimal("200.00"))

    async def test_reconciliation_status_refunded_succeeded_ok(self):
        session = MagicMock()
        payment = Payment(
            id=101,
            amount=Decimal("250.00"),
            currency="RUB",
            public_order_id="topup_101",
            provider_status=PaymentProviderStatus.REFUNDED.value,
            fulfillment_status=PaymentFulfillmentStatus.REVERSED.value,
            reconciliation_status=PaymentReconciliationStatus.OK.value,
        )
        # YooKassa returns observed="succeeded" with refunded_amount=250.00
        data = {
            "id": "2d3e4f5a-000f-5000-8000-123456789abc",
            "status": "succeeded",
            "refundable": False,
            "amount": {"value": "250.00", "currency": "RUB"},
            "refunded_amount": {"value": "250.00", "currency": "RUB"},
            "captured_at": "2026-09-18T10:00:00.000Z",
            "metadata": {
                "order_id": "topup_101",
                "local_payment_id": "101",
            },
        }

        transition = await apply_provider_transition(
            session,
            payment,
            data,
            source="reconciliation",
        )

        self.assertEqual(transition.outcome, "applied")
        self.assertEqual(payment.reconciliation_status, PaymentReconciliationStatus.OK.value)


if __name__ == "__main__":
    unittest.main()
