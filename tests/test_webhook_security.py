import unittest

from bot.handlers.webhook import (
    _validate_webhook_object,
    _validate_webhook_payload,
)

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


class WebhookRouteRegistrationTests(unittest.TestCase):
    def test_both_yookassa_webhook_routes_are_registered(self):
        from aiohttp import web

        from bot.handlers.webhook import setup_webhook_routes

        app = web.Application()
        setup_webhook_routes(app)

        registered_paths = [route.resource.canonical for route in app.router.routes()]
        self.assertIn("/webhook/yookassa", registered_paths)
        self.assertIn("/yookassa/webhook", registered_paths)
        self.assertIn("/health", registered_paths)


if __name__ == "__main__":
    unittest.main()
