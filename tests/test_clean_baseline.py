import unittest
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

VERSIONS = Path(__file__).parents[1] / "alembic" / "versions"


class CleanBaselineTests(unittest.TestCase):
    def test_exactly_one_root_revision(self):
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        self.assertEqual(scripts.get_bases(), ["0001_clean_baseline"])

    def test_baseline_guards_live_financial_tables(self):
        """The baseline must protect the financial tables that are still live."""
        source = (VERSIONS / "0001_clean_baseline.py").read_text(encoding="utf-8")
        for required in (
            "account_ledger_append_only",
            "account_allocations_append_only",
            "validate_account_allocation",
            "tariff_quotes_immutable",
            "tariff_versions_immutable",
        ):
            self.assertIn(required, source)
        for removed in (
            "phase6_",
            "legacy_change_quote_source_snapshot_missing",
            "payment_fulfillment_operations",
        ):
            self.assertNotIn(removed, source)

    def test_head_migration_drops_the_banking_residue(self):
        """The abandoned banking schema must stay dropped.

        This is the regression guard for the PR #277 purge: that PR deleted the
        service layer but left the tables behind. Previously no test asserted
        their removal, so the baseline effectively protected the dead structure.
        """
        source = (VERSIONS / "0032_drop_banking_residue.py").read_text(encoding="utf-8")
        for table in (
            "paid_value_ledger",
            "payment_provider_operations",
            "account_balance_reservations",
            "payment_refunds",
            "payment_events",
            "entitlement_entries",
            "payment_disputes",
            "provider_refund_operations",
        ):
            self.assertIn(table, source)
        # The trigger that used to probe the ledger must no longer do so.
        self.assertNotIn("FROM paid_value_ledger", source)
        # And payments must survive: the admin UI and dashboard still read it.
        self.assertNotIn("DROP TABLE IF EXISTS public.payments", source)


if __name__ == "__main__":
    unittest.main()
