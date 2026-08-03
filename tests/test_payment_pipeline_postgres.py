"""Greenfield payment-pipeline architecture contracts.

The live provider pipeline only creates/reconciles balance top-ups. Tariff
purchases are settled from the internal account balance and therefore do not
have a second YooKassa fulfillment queue.
"""

import unittest
from pathlib import Path

from database.models import Payment
from services.payment_provider_operations import VALID_PROVIDER_STATUSES

ROOT = Path(__file__).parents[1]


class GreenfieldPaymentPipelineContracts(unittest.TestCase):
    def test_payment_rows_are_balance_topups_only(self):
        columns = Payment.__table__.c
        for removed in (
            "tariff_id",
            "tariff_quote_id",
            "tariff_version_id",
            "payment_kind",
            "snapshot_duration_days",
            "snapshot_device_limit",
        ):
            self.assertNotIn(removed, columns)

    def test_provider_queue_only_creates_or_reconciles_topups(self):
        source = (ROOT / "services/payment_provider_operations.py").read_text()
        self.assertIn('operation_type == "create_payment"', source)
        self.assertIn('operation_type == "reconcile_payment"', source)
        self.assertNotIn('operation_type == "cancel_payment"', source)
        self.assertNotIn("grant_subscription", source)
        self.assertIn("succeeded", VALID_PROVIDER_STATUSES)

    def test_verified_success_routes_to_account_credit(self):
        source = (ROOT / "services/payment_provider_operations.py").read_text()
        self.assertIn("settle_succeeded_topup", source)
        self.assertIn("provider_get_payment", source)
        self.assertNotIn("payment-grant:", source)

    def test_removed_legacy_service_is_not_present(self):
        self.assertFalse((ROOT / "services/payment_service").exists())
        self.assertFalse((ROOT / "services/payment_fulfillment.py").exists())
        self.assertFalse((ROOT / "services/tariff_change_payment.py").exists())


if __name__ == "__main__":
    unittest.main()
