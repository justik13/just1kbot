import unittest
from unittest.mock import AsyncMock

from bot.handlers.webhook import (
    _validate_webhook_object,
    _validate_webhook_payload,
)
from services.workers.webhook_inbox import InboxClaim, fetch_provider
from services.yookassa_service import YooKassaResult


class WebhookObjectValidationTests(unittest.TestCase):
    def test_valid_payment_object(self):
        provider_object_id, payment_external_id = _validate_webhook_object(
            {"id": "payment-1"}, "payment.succeeded"
        )
        self.assertEqual(provider_object_id, "payment-1")
        self.assertEqual(payment_external_id, "payment-1")

    def test_missing_provider_object_id_raises_error(self):
        with self.assertRaisesRegex(ValueError, "identity"):
            _validate_webhook_object(
                {"payment_id": "payment-1"}, "payment.succeeded"
            )

    def test_valid_refund_object_uses_official_payment_id(self):
        provider_object_id, payment_external_id = _validate_webhook_object(
            {"id": "refund-1", "payment_id": "payment-1"},
            "refund.succeeded",
        )
        self.assertEqual(provider_object_id, "refund-1")
        self.assertEqual(payment_external_id, "payment-1")

    def test_nested_refund_payment_id_is_not_official_contract(self):
        with self.assertRaisesRegex(ValueError, "identity"):
            _validate_webhook_object(
                {"id": "refund-1", "payment": {"id": "payment-1"}},
                "refund.succeeded",
            )

    def test_valid_official_notification_payload(self):
        payload = {
            "type": "notification",
            "event": "refund.succeeded",
            "object": {
                "id": "refund-1",
                "status": "succeeded",
                "payment_id": "payment-1",
                "amount": {"value": "10.00", "currency": "RUB"},
            },
        }
        event, obj, provider_id, payment_id = _validate_webhook_payload(payload)
        self.assertEqual(event, "refund.succeeded")
        self.assertIs(obj, payload["object"])
        self.assertEqual(provider_id, "refund-1")
        self.assertEqual(payment_id, "payment-1")

    def test_notification_type_is_required(self):
        with self.assertRaisesRegex(ValueError, "notification_type"):
            _validate_webhook_payload(
                {
                    "event": "payment.succeeded",
                    "object": {"id": "payment-1"},
                }
            )

    def test_legacy_payment_refunded_event_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported_event"):
            _validate_webhook_payload(
                {
                    "type": "notification",
                    "event": "payment.refunded",
                    "object": {"id": "payment-1"},
                }
            )


class WebhookProviderVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_refund_webhook_gets_current_refund_object(self):
        result = YooKassaResult(
            True,
            value={
                "id": "refund-1",
                "status": "succeeded",
                "payment_id": "payment-1",
                "amount": {"value": "10.00", "currency": "RUB"},
            },
        )
        transport = type(
            "Transport",
            (),
            {
                "get_refund_result": AsyncMock(return_value=result),
                "get_payment_result": AsyncMock(),
            },
        )
        claim = InboxClaim(
            1,
            "worker",
            1,
            "refund.succeeded",
            "payment-1",
            None,
            {"object": {"id": "refund-1"}},
            "event-key",
        )

        self.assertIs(await fetch_provider(claim, transport), result)
        transport.get_refund_result.assert_awaited_once_with("refund-1")
        transport.get_payment_result.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
