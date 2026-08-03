"""Greenfield quote and paid-value schema contracts.

Historical provider-backed tariff checkouts no longer exist. Tariff quotes now
freeze a purchase/renew/change decision that is settled only from the internal
account balance.
"""

import unittest

from database.models import (
    EntitlementEntry,
    PaidValueLedgerEntry,
    Payment,
    TariffQuote,
)


class GreenfieldTariffQuoteSchemaTests(unittest.TestCase):
    def test_provider_payment_is_not_bound_to_tariff_quote(self):
        for column in ("tariff_id", "tariff_quote_id", "tariff_version_id"):
            self.assertNotIn(column, Payment.__table__.c)
        self.assertNotIn("payment_id", TariffQuote.__table__.c)

    def test_paid_subscription_value_is_quote_backed(self):
        columns = PaidValueLedgerEntry.__table__.c
        self.assertIn("quote_id", columns)
        self.assertNotIn("payment_id", columns)
        constraints = {
            item.name for item in PaidValueLedgerEntry.__table__.constraints if item.name
        }
        self.assertIn("ck_paid_value_account_purchase_shape", constraints)
        self.assertIn("ck_paid_value_conversion_shape", constraints)
        indexes = {item.name for item in PaidValueLedgerEntry.__table__.indexes}
        self.assertIn("uq_paid_value_account_purchase", indexes)
        self.assertIn("uq_paid_value_conversion_quote", indexes)

    def test_entitlement_uses_account_purchase_grant(self):
        constraints = {
            item.name for item in EntitlementEntry.__table__.constraints if item.name
        }
        self.assertIn("ck_entitlement_entries_type", constraints)
        allowed = next(
            str(item.sqltext)
            for item in EntitlementEntry.__table__.constraints
            if item.name == "ck_entitlement_entries_type"
        )
        self.assertIn("account_purchase_grant", allowed)
        self.assertNotIn("payment_grant", allowed)

    def test_change_quote_requires_frozen_source_snapshot(self):
        constraints = {
            item.name for item in TariffQuote.__table__.constraints if item.name
        }
        self.assertIn("ck_tariff_quotes_change_source_snapshot", constraints)
        self.assertIn("ck_tariff_quotes_lifecycle_timestamps", constraints)
        self.assertIn("ck_tariff_quotes_value_invariant", constraints)


if __name__ == "__main__":
    unittest.main()
