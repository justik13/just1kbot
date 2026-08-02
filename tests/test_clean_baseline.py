import unittest
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


class CleanBaselineTests(unittest.TestCase):
    def test_exactly_one_root_revision(self):
        versions = sorted(
            path for path in (Path(__file__).parents[1] / "alembic" / "versions").glob("*.py")
            if path.name != "__init__.py"
        )
        self.assertEqual([path.name for path in versions], ["0001_clean_baseline.py"])
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        self.assertEqual(scripts.get_heads(), ["0001_clean_baseline"])
        self.assertEqual(scripts.get_bases(), ["0001_clean_baseline"])

    def test_baseline_contains_financial_guards_and_no_phase6_artifacts(self):
        source = (
            Path(__file__).parents[1] / "alembic" / "versions" / "0001_clean_baseline.py"
        ).read_text(encoding="utf-8")
        for required in (
            "account_ledger_append_only",
            "account_allocations_append_only",
            "account_reservation_identity",
            "validate_account_allocation",
            "entitlement_entries_append_only",
            "paid_value_ledger_append_only",
            "tariff_quotes_immutable",
            "tariff_versions_immutable",
            "provider_refund_operations",
            "payment_disputes",
        ):
            self.assertIn(required, source)
        self.assertNotIn("phase6_", source)
        self.assertNotIn("legacy_change_quote_source_snapshot_missing", source)


if __name__ == "__main__":
    unittest.main()
