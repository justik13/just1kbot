"""Fast contract tests for the isolated phase-6 creation boundary."""
import dataclasses
import inspect
import unittest

from services.tariff_change_payment import TariffChangePaymentResult, create_tariff_change_payment


class TariffChangePaymentContractTests(unittest.TestCase):
    def test_result_is_typed_and_frozen(self):
        self.assertTrue(dataclasses.is_dataclass(TariffChangePaymentResult))
        self.assertEqual([field.name for field in dataclasses.fields(TariffChangePaymentResult)],
                         ["payment", "created", "provider_operation", "failure_code"])
        self.assertTrue(TariffChangePaymentResult.__dataclass_params__.frozen)

    def test_caller_cannot_supply_financial_identity(self):
        parameters = inspect.signature(create_tariff_change_payment).parameters
        forbidden = {"amount", "currency", "tariff_id", "duration", "device_limit",
                     "resulting_hours", "resulting_value"}
        self.assertFalse(forbidden & parameters.keys())
        self.assertEqual(set(parameters), {"session", "user_id", "quote_public_id", "bot_username", "as_of"})

    def test_service_has_no_commit_or_http(self):
        source = inspect.getsource(create_tariff_change_payment)
        self.assertNotIn(".commit(", source)
        self.assertNotIn("perform_http", source)
        self.assertNotIn("YooKassaService", source)
        self.assertIn("await session.flush()", source)

    def test_public_quote_id_is_typed(self):
        annotation = inspect.signature(create_tariff_change_payment).parameters["quote_public_id"].annotation
        self.assertNotEqual(annotation, object)
