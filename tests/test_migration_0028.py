"""Unit tests for Alembic migration 0028_two_balance_system."""

import importlib.util
import os
from pathlib import Path
import unittest

from alembic.config import Config
from alembic.script import ScriptDirectory
from database.models import User


class Migration0028Tests(unittest.TestCase):
    """Test suite for migration 0028_two_balance_system structure and model compliance."""

    def setUp(self):
        file_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "alembic",
            "versions",
            "0028_two_balance_system.py",
        )
        spec = importlib.util.spec_from_file_location("migration_0028", file_path)
        self.migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.migration)

    def test_migration_0028_metadata_and_chain(self):
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        rev = scripts.get_revision("0028_two_balance_system")
        self.assertIsNotNone(rev)
        self.assertEqual(rev.down_revision, "0027_awg_subscription_system")
        self.assertEqual(scripts.get_heads(), ["0028_two_balance_system"])
        self.assertEqual(self.migration.revision, "0028_two_balance_system")
        self.assertEqual(self.migration.down_revision, "0027_awg_subscription_system")

    def test_migration_0028_source_content(self):
        m28_path = Path("alembic/versions/0028_two_balance_system.py")
        self.assertTrue(m28_path.is_file())
        content = m28_path.read_text(encoding="utf-8")

        self.assertIn("def upgrade()", content)
        self.assertIn("def downgrade()", content)
        self.assertIn("balance", content)
        self.assertIn("bonus_balance", content)
        self.assertIn("ck_users_bonus_balance_nonnegative", content)

    def test_user_model_compliance(self):
        user_table = User.__table__
        self.assertIn("balance", user_table.columns)
        self.assertIn("bonus_balance", user_table.columns)

        bal_col = user_table.columns["balance"]
        self.assertFalse(bal_col.nullable)
        self.assertEqual(bal_col.type.precision, 12)
        self.assertEqual(bal_col.type.scale, 2)

        bonus_col = user_table.columns["bonus_balance"]
        self.assertFalse(bonus_col.nullable)
        self.assertEqual(bonus_col.type.precision, 12)
        self.assertEqual(bonus_col.type.scale, 2)


if __name__ == "__main__":
    unittest.main()
