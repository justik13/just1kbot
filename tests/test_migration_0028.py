"""Unit tests for Alembic migration 0028_wi_notifications."""

import importlib.util
import os
from pathlib import Path
import unittest

from alembic.config import Config
from alembic.script import ScriptDirectory
from database.models import WhiteInternetSubscription


class Migration0028Tests(unittest.TestCase):
    """Test suite for migration 0028_wi_notifications structure and model compliance."""

    def setUp(self):
        file_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "alembic",
            "versions",
            "0028_wi_notifications.py",
        )
        spec = importlib.util.spec_from_file_location("migration_0028", file_path)
        self.migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.migration)

    def test_migration_0028_metadata_and_chain(self):
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        rev = scripts.get_revision("0028_wi_notifications")
        self.assertIsNotNone(rev)
        self.assertEqual(rev.down_revision, "0027_backfill_entitlements")
        self.assertEqual(scripts.get_heads(), ["0029_rebase_wi_traffic_downlink"])
        self.assertEqual(self.migration.revision, "0028_wi_notifications")
        self.assertEqual(self.migration.down_revision, "0027_backfill_entitlements")

    def test_migration_0028_source_content(self):
        m28_path = Path("alembic/versions/0028_wi_notifications.py")
        self.assertTrue(m28_path.is_file())
        content = m28_path.read_text(encoding="utf-8")

        self.assertIn("def upgrade()", content)
        self.assertIn("def downgrade()", content)
        self.assertIn("notified_3d", content)
        self.assertIn("notified_1d", content)
        self.assertIn("notified_2h", content)
        self.assertIn("notified_expired", content)
        self.assertIn("notified_90p", content)
        self.assertIn("is_trial", content)
        self.assertIn("ix_wi_subs_expiring_notify", content)

    def test_white_internet_subscription_model_has_notification_columns_and_index(self):
        table = WhiteInternetSubscription.__table__
        self.assertIn("notified_3d", table.c)
        self.assertIn("notified_1d", table.c)
        self.assertIn("notified_2h", table.c)
        self.assertIn("notified_expired", table.c)
        self.assertIn("notified_90p", table.c)

        index_names = {idx.name for idx in table.indexes}
        self.assertIn("ix_wi_subs_expiring_notify", index_names)


if __name__ == "__main__":
    unittest.main()
