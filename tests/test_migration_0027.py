"""Unit tests for Alembic migration 0027_wi_notification_flags."""

from pathlib import Path
import unittest

from alembic.config import Config
from alembic.script import ScriptDirectory
from database.models import WhiteInternetSubscription


class Migration0027Tests(unittest.TestCase):
    """Test suite for migration 0027_wi_notification_flags structure and model compliance."""

    def test_migration_0027_in_script_directory(self):
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        rev = scripts.get_revision("0027_wi_notification_flags")
        self.assertIsNotNone(rev)
        self.assertEqual(rev.down_revision, "0026_wi_trial_semantics")
        self.assertEqual(scripts.get_heads(), ["0027_wi_notification_flags"])

    def test_migration_0027_source_content(self):
        m27_path = Path("alembic/versions/0027_wi_notification_flags.py")
        self.assertTrue(m27_path.is_file())
        content = m27_path.read_text(encoding="utf-8")

        self.assertIn("def upgrade()", content)
        self.assertIn("def downgrade()", content)
        self.assertIn("white_internet_subscriptions", content)
        self.assertIn("notified_3d", content)
        self.assertIn("notified_1d", content)
        self.assertIn("notified_2h", content)
        self.assertIn("notified_expired", content)
        self.assertIn("ix_wi_subs_expiring_notify", content)

    def test_white_internet_subscription_model_notification_attributes(self):
        table = WhiteInternetSubscription.__table__
        self.assertIn("notified_3d", table.columns)
        self.assertIn("notified_1d", table.columns)
        self.assertIn("notified_2h", table.columns)
        self.assertIn("notified_expired", table.columns)

        self.assertFalse(table.columns["notified_3d"].nullable)
        self.assertFalse(table.columns["notified_1d"].nullable)
        self.assertFalse(table.columns["notified_2h"].nullable)
        self.assertFalse(table.columns["notified_expired"].nullable)

        index_names = {idx.name for idx in table.indexes}
        self.assertIn("ix_wi_subs_expiring_notify", index_names)
