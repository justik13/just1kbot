"""Unit tests for Alembic migration 0023_wi_active_hwids."""

from pathlib import Path
import unittest

from alembic.config import Config
from alembic.script import ScriptDirectory
from database.models import WhiteInternetSubscription


class Migration0023Tests(unittest.TestCase):
    """Test suite for migration 0023_wi_active_hwids structure and model compliance."""

    def test_migration_0023_in_script_directory(self):
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        rev = scripts.get_revision("0023_wi_active_hwids")
        self.assertIsNotNone(rev)
        self.assertEqual(rev.down_revision, "0022_servers_protocol_not_null")
        self.assertEqual(scripts.get_heads(), ["0027_backfill_entitlements"])

    def test_migration_0023_source_content(self):
        m23_path = Path("alembic/versions/0023_wi_active_hwids.py")
        self.assertTrue(m23_path.is_file())
        content = m23_path.read_text(encoding="utf-8")

        self.assertIn("def upgrade()", content)
        self.assertIn("def downgrade()", content)
        self.assertIn("white_internet_subscriptions", content)
        self.assertIn("active_hwids", content)
        self.assertIn("JSONB", content)

    def test_white_internet_subscription_model_active_hwids_attribute(self):
        table = WhiteInternetSubscription.__table__
        self.assertIn("active_hwids", table.columns)
        col = table.columns["active_hwids"]
        self.assertTrue(col.nullable)
