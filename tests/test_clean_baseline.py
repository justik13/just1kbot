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

    def test_legacy_downgrades_tolerate_dropped_tables(self):
        """Downgrades touching dropped tables must be guarded by to_regclass.

        Migration 0032 removes the banking tables without recreating them, so
        every older downgrade that names one must skip itself when the table
        is gone — otherwise `alembic downgrade base` aborts mid-chain. This
        pins the guards (execution itself is covered by the CI downgrade).
        """
        for filename, table in (
            ("0004_referral_entitlements.py", "entitlement_entries"),
            ("0012_payment_debits_and_audit_target_idx.py", "payment_events"),
            ("0017_white_internet_durations.py", "paid_value_ledger"),
            ("0027_backfill_entitlements.py", "entitlement_entries"),
        ):
            with self.subTest(migration=filename):
                source = (VERSIONS / filename).read_text(encoding="utf-8")
                self.assertIn("to_regclass", source)
                self.assertIn(table, source)


    def test_quote_drop_migration_backfills_and_drops(self):
        """Migration 0034 must backfill consumed quotes into orders, re-link
        ledger debits, and drop the quote/version tables without recreating.

        Regression guard for the White Internet billing port: dropping the
        tables without backfill would orphan purchase history and trial
        checks that now read Orders.
        """
        source = (VERSIONS / "0034_drop_tariff_quotes.py").read_text(encoding="utf-8")
        self.assertEqual(
            (VERSIONS / "0034_drop_tariff_quotes.py").exists(), True
        )
        for table in ("tariff_quotes", "tariff_versions"):
            self.assertIn(table, source)
        self.assertIn("migrated_from_quote", source)
        self.assertIn("DROP TABLE IF EXISTS public.tariff_quotes", source)
        self.assertIn("DROP TABLE IF EXISTS public.tariff_versions", source)
        # Fail-closed: abort instead of dropping with dangling ledger links.
        self.assertIn("quote_id IS NOT NULL", source)

    def test_quote_drop_downgrades_tolerate_dropped_tables(self):
        """Downgrades touching quotes/versions must skip themselves when gone.

        Migration 0034 drops the tables without recreating them, so every
        older downgrade that names one must check to_regclass first —
        otherwise `alembic downgrade base` aborts mid-chain.
        """
        for filename, table in (
            ("0016_white_internet_subscriptions.py", "tariff_quotes"),
            ("0017_white_internet_durations.py", "tariff_versions"),
            ("0018_simplify_wi_traffic.py", "tariff_quotes"),
            ("0026_wi_trial_semantics.py", "tariff_quotes"),
        ):
            with self.subTest(migration=filename):
                source = (VERSIONS / filename).read_text(encoding="utf-8")
                self.assertIn("to_regclass", source)
                self.assertIn(table, source)


if __name__ == "__main__":
    unittest.main()
