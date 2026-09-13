"""Unit tests for Alembic migration 0027_backfill_entitlements."""

import importlib.util
import os
from pathlib import Path
import unittest

from alembic.config import Config
from alembic.script import ScriptDirectory
from database.models import EntitlementEntry


class Migration0027Tests(unittest.TestCase):
    """Test suite for migration 0027_backfill_entitlements structure and constraint compliance."""

    def setUp(self):
        file_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "alembic",
            "versions",
            "0027_backfill_entitlements.py",
        )
        spec = importlib.util.spec_from_file_location("migration_0027", file_path)
        self.migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.migration)

    def test_migration_0027_metadata_and_chain(self):
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        rev = scripts.get_revision("0027_backfill_entitlements")
        self.assertIsNotNone(rev)
        self.assertEqual(rev.down_revision, "0026_wi_trial_semantics")
        self.assertEqual(scripts.get_heads(), ["0027_backfill_entitlements"])
        self.assertEqual(self.migration.revision, "0027_backfill_entitlements")
        self.assertEqual(self.migration.down_revision, "0026_wi_trial_semantics")

    def test_migration_0027_source_content(self):
        m27_path = Path("alembic/versions/0027_backfill_entitlements.py")
        self.assertTrue(m27_path.is_file())
        content = m27_path.read_text(encoding="utf-8")

        self.assertIn("def upgrade()", content)
        self.assertIn("def downgrade()", content)
        self.assertIn("ck_entitlement_entries_shape", content)
        self.assertIn("exact_hours", content)
        self.assertIn("legacy_0027_grant_", content)
        self.assertIn("legacy_active_subscription_backfill", content)

    def test_entitlement_entry_model_shape_constraint(self):
        constraint = next(
            c for c in EntitlementEntry.__table__.constraints
            if c.name == "ck_entitlement_entries_shape"
        )
        sqltext = str(constraint.sqltext)
        self.assertIn("days_delta = 0 AND hours_delta > 0", sqltext)
        self.assertIn("hours_delta = days_delta * 24", sqltext)
        self.assertIn("manual_grant", sqltext)
