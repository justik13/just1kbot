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


class WebhookIPValidationTests(unittest.TestCase):
    def test_official_yookassa_ips_are_trusted_by_default(self):
        from bot.handlers.webhook import _is_yookassa_ip

        self.assertTrue(_is_yookassa_ip("185.71.76.5"))
        self.assertTrue(_is_yookassa_ip("185.71.77.20"))
        self.assertTrue(_is_yookassa_ip("77.75.153.50"))
        self.assertTrue(_is_yookassa_ip("77.75.154.200"))
        self.assertTrue(_is_yookassa_ip("77.75.156.11"))
        self.assertTrue(_is_yookassa_ip("77.75.156.35"))
        self.assertTrue(_is_yookassa_ip("2a02:5180::1"))

        self.assertFalse(_is_yookassa_ip("1.2.3.4"))
        self.assertFalse(_is_yookassa_ip("198.51.100.1"))
        self.assertFalse(_is_yookassa_ip("invalid-ip"))

    def test_yookassa_extra_ips_dynamically_expand_trusted_ranges(self):
        from unittest.mock import patch
        from bot.handlers.webhook import _is_yookassa_ip

        with patch("bot.handlers.webhook.get_settings") as mock_settings:
            mock_settings.return_value.yookassa_allowed_ip_ranges = (
                "185.71.76.0/27",
                "198.51.100.5/32",
                "203.0.113.0/24",
            )
            self.assertTrue(_is_yookassa_ip("198.51.100.5"))
            self.assertTrue(_is_yookassa_ip("203.0.113.88"))
            self.assertTrue(_is_yookassa_ip("185.71.76.10"))
            self.assertFalse(_is_yookassa_ip("192.0.2.1"))

    def test_yookassa_rejection_logs_security_guidance(self):
        import asyncio
        from unittest.mock import MagicMock, patch
        from bot.handlers.webhook import yookassa_webhook_handler

        request = MagicMock()
        request.remote = "198.51.100.99"
        request.headers = {}

        with patch("bot.handlers.webhook._get_real_ip", return_value="198.51.100.99"), \
             patch("bot.handlers.webhook._is_yookassa_ip", return_value=False), \
             patch("bot.handlers.webhook.logger.warning") as mock_log_warn:
            resp = asyncio.run(yookassa_webhook_handler(request))
            self.assertEqual(resp.status, 404)
            mock_log_warn.assert_called_once()
            log_msg = mock_log_warn.call_args[0][0]
            self.assertIn("YOOKASSA_EXTRA_IPS in .env", log_msg)

    def test_yookassa_extra_ips_validator(self):
        from config.settings import Settings

        # Valid space-separated IP and CIDR
        res = Settings.validate_yookassa_extra_ips("198.51.100.5 203.0.113.0/24")
        self.assertEqual(res, "198.51.100.5/32 203.0.113.0/24")

        # Commas rejected to prevent breaking Caddyfile parser
        with self.assertRaisesRegex(ValueError, "space-separated IP/CIDR"):
            Settings.validate_yookassa_extra_ips("198.51.100.5, 203.0.113.0/24")

        # Semicolons rejected
        with self.assertRaisesRegex(ValueError, "space-separated IP/CIDR"):
            Settings.validate_yookassa_extra_ips("198.51.100.5;203.0.113.0/24")

        # Empty / whitespace
        self.assertEqual(Settings.validate_yookassa_extra_ips(""), "")
        self.assertEqual(Settings.validate_yookassa_extra_ips("   "), "")

        # Wildcard 0.0.0.0/0 rejected
        with self.assertRaisesRegex(ValueError, "wildcard allow-all"):
            Settings.validate_yookassa_extra_ips("0.0.0.0/0")

        # Wildcard ::/0 rejected
        with self.assertRaisesRegex(ValueError, "wildcard allow-all"):
            Settings.validate_yookassa_extra_ips("::/0")

        # Invalid IP rejected
        with self.assertRaisesRegex(ValueError, "invalid IP or CIDR"):
            Settings.validate_yookassa_extra_ips("not-an-ip")


if __name__ == "__main__":
    unittest.main()
