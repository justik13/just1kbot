import unittest
from bot.handlers.webhook import _validate_webhook_object


class WebhookObjectValidationTests(unittest.TestCase):
    def test_valid_payment_object_with_created_at_older_than_24_hours(self):
        """Test that payment object with valid identifiers and old created_at passes validation."""
        obj = {
            "id": "123",
            "created_at": "2020-01-01T00:00:00Z"
        }
        event = "payment.succeeded"

        # Should not raise an exception
        provider_object_id, payment_external_id = _validate_webhook_object(obj, event)

        self.assertEqual(provider_object_id, "123")
        self.assertEqual(payment_external_id, "123")

    def test_valid_payment_object_without_created_at(self):
        """Test that payment object with valid identifiers and no created_at passes validation."""
        obj = {
            "id": "456"
        }
        event = "payment.succeeded"

        # Should not raise an exception
        provider_object_id, payment_external_id = _validate_webhook_object(obj, event)

        self.assertEqual(provider_object_id, "456")
        self.assertEqual(payment_external_id, "456")

    def test_missing_provider_object_id_raises_error(self):
        """Test that missing provider object id raises ValueError."""
        obj = {
            "payment_id": "payment_123"
        }
        event = "payment.succeeded"

        with self.assertRaises(ValueError) as context:
            _validate_webhook_object(obj, event)

        self.assertEqual(str(context.exception), "identity")

    def test_missing_payment_external_id_raises_error(self):
        """Test that missing payment external id raises ValueError."""
        obj = {
            "id": "123"
        }
        event = "refund.succeeded"

        with self.assertRaises(ValueError) as context:
            _validate_webhook_object(obj, event)

        self.assertEqual(str(context.exception), "identity")

    def test_valid_refund_object_with_payment_id(self):
        """Test that refund object with valid identifiers passes validation."""
        obj = {
            "id": "refund-1",
            "payment_id": "payment-1"
        }
        event = "refund.succeeded"

        # Should not raise an exception
        provider_object_id, payment_external_id = _validate_webhook_object(obj, event)

        self.assertEqual(provider_object_id, "refund-1")
        self.assertEqual(payment_external_id, "payment-1")

    def test_valid_refund_object_with_nested_payment_id(self):
        """Test that refund object with nested payment id passes validation."""
        obj = {
            "id": "refund-2",
            "payment": {"id": "payment-2"}
        }
        event = "refund.succeeded"

        # Should not raise an exception
        provider_object_id, payment_external_id = _validate_webhook_object(obj, event)

        self.assertEqual(provider_object_id, "refund-2")
        self.assertEqual(payment_external_id, "payment-2")

    def test_refund_object_with_invalid_payment_type_raises_error(self):
        """Test that refund object with invalid payment type raises ValueError."""
        obj = {
            "id": "refund-invalid",
            "payment": "not-a-dict",
        }
        event = "refund.succeeded"

        with self.assertRaises(ValueError) as context:
            _validate_webhook_object(obj, event)

        self.assertEqual(str(context.exception), "identity")


if __name__ == "__main__":
    unittest.main()
