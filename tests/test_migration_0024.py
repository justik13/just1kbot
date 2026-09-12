"""Unit tests for Alembic migration 0024_wi_device_limit."""

from pathlib import Path
import unittest

from alembic.config import Config
from alembic.script import ScriptDirectory
from database.models import WhiteInternetSubscription


class Migration0024Tests(unittest.TestCase):
    """Test suite for migration 0024_wi_device_limit structure and model compliance."""

    def test_migration_0024_in_script_directory(self):
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        rev = scripts.get_revision("0024_wi_device_limit")
        self.assertIsNotNone(rev)
        self.assertEqual(rev.down_revision, "0023_wi_active_hwids")
        self.assertEqual(scripts.get_heads(), ["0027_backfill_entitlements"])

    def test_migration_0024_source_content(self):
        m24_path = Path("alembic/versions/0024_wi_device_limit.py")
        self.assertTrue(m24_path.is_file())
        content = m24_path.read_text(encoding="utf-8")

        self.assertIn("def upgrade()", content)
        self.assertIn("def downgrade()", content)
        self.assertIn("white_internet_subscriptions", content)
        self.assertIn("device_limit", content)
        self.assertIn("last_device_reset_at", content)
        self.assertIn("ck_white_internet_subscriptions_device_limit", content)

    def test_white_internet_subscription_model_device_limit_attributes(self):
        table = WhiteInternetSubscription.__table__
        self.assertIn("device_limit", table.columns)
        self.assertIn("last_device_reset_at", table.columns)

        dev_col = table.columns["device_limit"]
        self.assertFalse(dev_col.nullable)

        reset_col = table.columns["last_device_reset_at"]
        self.assertTrue(reset_col.nullable)

        constraint_names = {c.name for c in table.constraints if c.name}
        self.assertIn("ck_white_internet_subscriptions_device_limit", constraint_names)
