"""Unit tests for Alembic migration 0030_wi_notify_90p."""

import importlib.util
import os
from pathlib import Path
import unittest

from alembic.config import Config
from alembic.script import ScriptDirectory
from database.models import WhiteInternetSubscription


class Migration0030Tests(unittest.TestCase):
    """Test suite for migration 0030_wi_notify_90p structure and model compliance."""

    def setUp(self):
        file_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "alembic",
            "versions",
            "0030_wi_notify_90p.py",
        )
        spec = importlib.util.spec_from_file_location("migration_0030", file_path)
        self.migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.migration)

    def test_migration_0030_metadata_and_chain(self):
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        rev = scripts.get_revision("0030_wi_notify_90p")
        self.assertIsNotNone(rev)
        self.assertEqual(rev.down_revision, "0029_wi_notify_and_quote_opt")
        self.assertEqual(scripts.get_heads(), ["0030_wi_notify_90p"])
        self.assertEqual(self.migration.revision, "0030_wi_notify_90p")
        self.assertEqual(self.migration.down_revision, "0029_wi_notify_and_quote_opt")

    def test_migration_0030_source_content(self):
        m30_path = Path("alembic/versions/0030_wi_notify_90p.py")
        self.assertTrue(m30_path.is_file())
        content = m30_path.read_text(encoding="utf-8")

        self.assertIn("def upgrade()", content)
        self.assertIn("def downgrade()", content)
        self.assertIn("notified_90p", content)

    def test_white_internet_subscription_model_compliance(self):
        sub_table = WhiteInternetSubscription.__table__
        self.assertIn("notified_90p", sub_table.columns)
        col = sub_table.columns["notified_90p"]
        self.assertFalse(col.nullable)


if __name__ == "__main__":
    unittest.main()
