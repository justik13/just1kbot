import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from bot.handlers.admin.payment_queues import (
    QUEUE_CODES,
    _card_text,
    diagnostics_keyboard,
)
from services.payment_queue_admin import QueueRow, _spec, confirm_manual_retry


class PaymentQueueAdminUnitTests(unittest.IsolatedAsyncioTestCase):
    def row(self, status="dead"):
        now = datetime.now(timezone.utc)
        return QueueRow("provider", 42, 7, "create_payment", status, 4, 4,
                        "safe_code", now, now, now, None, "not_locked", 30)

    def test_card_is_secret_free_and_retry_only_for_dead(self):
        rendered = _card_text(self.row())
        self.assertIn("safe_code", rendered)
        for forbidden in ("payload", "last_error", "idempotency", "SECRET_CANARY"):
            self.assertNotIn(forbidden, rendered)
        self.assertTrue(self.row().retry_allowed)
        self.assertFalse(self.row("retry").retry_allowed)

    def test_callback_data_is_bounded_and_contains_no_reason(self):
        markup = diagnostics_keyboard()
        callbacks = [button.callback_data for line in markup.inline_keyboard for button in line]
        self.assertTrue(all(len(value.encode()) <= 64 for value in callbacks))
        self.assertTrue(all("operator reason" not in value for value in callbacks))
        self.assertEqual(set(QUEUE_CODES), {"provider", "fulfillment", "webhook"})

    async def test_invalid_inputs_are_rejected_before_database_access(self):
        with self.assertRaises(ValueError):
            _spec("invalid")
        session = AsyncMock()
        with self.assertRaises(ValueError):
            await confirm_manual_retry(session, admin_id=1, queue="invalid",
                                       operation_id=1, reason="valid reason")
        with self.assertRaises(ValueError):
            await confirm_manual_retry(session, admin_id=1, queue="provider",
                                       operation_id=0, reason="valid reason")
        with self.assertRaises(ValueError):
            await confirm_manual_retry(session, admin_id=1, queue="provider",
                                       operation_id=1, reason="x")
        session.scalar.assert_not_awaited()

    async def test_dispatcher_only_calls_existing_retry_primitive(self):
        row = type("Row", (), {"status": "dead", "attempts": 3, "payment_id": 9})()
        session = AsyncMock()
        session.scalar.return_value = row
        with patch("services.payment_queue_admin.retry_dead_fulfillment_operation",
                   AsyncMock(return_value=row)) as retry, patch(
                   "services.payment_queue_admin._audit", AsyncMock()) as audit:
            result = await confirm_manual_retry(session, admin_id=1,
                queue="fulfillment", operation_id=2, reason="operator approved")
        self.assertEqual(result.outcome, "retry_scheduled")
        retry.assert_awaited_once_with(session, 2, reset_attempts=True,
                                       reason="operator approved")
        audit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
