"""Unit tests for Alembic migration 0031_awg_persistent_traffic."""

import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from alembic.config import Config
from alembic.script import ScriptDirectory
from database.models import User, VPNProfile


class Migration0031Tests(unittest.TestCase):
    """Test suite for migration 0031_awg_persistent_traffic structure and model compliance."""

    def setUp(self):
        file_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "alembic",
            "versions",
            "0031_awg_persistent_traffic.py",
        )
        spec = importlib.util.spec_from_file_location("migration_0031", file_path)
        self.migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.migration)

    def test_migration_0031_metadata_and_chain(self):
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        rev = scripts.get_revision("0031_awg_persistent_traffic")
        self.assertIsNotNone(rev)
        self.assertEqual(rev.down_revision, "0030_simple_billing")
        self.assertEqual(scripts.get_heads(), ["0031_awg_persistent_traffic"])
        self.assertEqual(self.migration.revision, "0031_awg_persistent_traffic")
        self.assertEqual(self.migration.down_revision, "0030_simple_billing")

    def test_migration_0031_source_content(self):
        m31_path = Path("alembic/versions/0031_awg_persistent_traffic.py")
        self.assertTrue(m31_path.is_file())
        content = m31_path.read_text(encoding="utf-8")

        self.assertIn("def upgrade()", content)
        self.assertIn("def downgrade()", content)
        self.assertIn("raw_last_down", content)
        self.assertIn("raw_last_up", content)
        self.assertIn("total_traffic_bytes", content)

    def test_models_have_new_traffic_columns(self):
        vpn_table = VPNProfile.__table__
        self.assertIn("raw_last_down", vpn_table.c)
        self.assertIn("raw_last_up", vpn_table.c)
        self.assertIn("traffic_down", vpn_table.c)
        self.assertIn("traffic_up", vpn_table.c)

        user_table = User.__table__
        self.assertIn("total_traffic_bytes", user_table.c)

    def test_upgrade_and_downgrade_operations(self):
        with patch.object(self.migration, "op") as mock_op:
            mock_op.add_column = MagicMock()
            mock_op.execute = MagicMock()
            mock_op.drop_column = MagicMock()

            # Run upgrade
            self.migration.upgrade()
            self.assertEqual(mock_op.add_column.call_count, 3)
            self.assertEqual(mock_op.execute.call_count, 2)

            # Run downgrade
            self.migration.downgrade()
            self.assertEqual(mock_op.drop_column.call_count, 3)


if __name__ == "__main__":
    unittest.main()
