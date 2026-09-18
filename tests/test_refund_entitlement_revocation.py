from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers.admin.payments import get_payment_refundable_remainder
from database.models import Payment, PaymentRefund, User
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
        session = AsyncMock()
        payment = Payment(
            id=101,
            amount=Decimal("250.00"),
            currency="RUB",
            provider_status=PaymentProviderStatus.REFUNDED.value,
            fulfillment_status=PaymentFulfillmentStatus.REVERSED.value,
            reconciliation_status=PaymentReconciliationStatus.OK.value,
        )
        # YooKassa returns observed="succeeded" with refunded_amount=250.00
        data = {
            "status": "succeeded",
            "refundable": False,
            "refunded_amount": {"value": "250.00", "currency": "RUB"},
        }

        transition = await apply_provider_transition(
            session,
            payment,
            data,
            source="reconciliation",
        )

        self.assertEqual(transition.action, "applied")
        self.assertEqual(payment.reconciliation_status, PaymentReconciliationStatus.OK.value)


if __name__ == "__main__":
    unittest.main()
