"""Unit tests for Alembic migration 0029_rebase_wi_traffic_downlink."""

import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from alembic.config import Config
from alembic.script import ScriptDirectory


class Migration0029Tests(unittest.TestCase):
    """Test suite for migration 0029_rebase_wi_traffic_downlink."""

    def setUp(self):
        file_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "alembic",
            "versions",
            "0029_rebase_wi_traffic_downlink.py",
        )
        spec = importlib.util.spec_from_file_location("migration_0029", file_path)
        self.migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.migration)

    def test_migration_0029_metadata_and_chain(self):
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        rev = scripts.get_revision("0029_rebase_wi_traffic_downlink")
        self.assertIsNotNone(rev)
        self.assertEqual(rev.down_revision, "0028_wi_notifications")
        self.assertEqual(scripts.get_heads(), ["0029_rebase_wi_traffic_downlink"])
        self.assertEqual(self.migration.revision, "0029_rebase_wi_traffic_downlink")
        self.assertEqual(self.migration.down_revision, "0028_wi_notifications")

    def test_migration_0029_source_content(self):
        m29_path = Path("alembic/versions/0029_rebase_wi_traffic_downlink.py")
        self.assertTrue(m29_path.is_file())
        content = m29_path.read_text(encoding="utf-8")

        self.assertIn("def upgrade()", content)
        self.assertIn("def downgrade()", content)
        self.assertIn("traffic_used_bytes = traffic_downlink_bytes", content)
        self.assertIn("WHERE traffic_used_bytes > traffic_downlink_bytes", content)

    def test_migration_0029_upgrade_execution(self):
        with patch("alembic.op.execute") as mock_execute:
            self.migration.upgrade()
            mock_execute.assert_called_once()
            sql_clause = str(mock_execute.call_args[0][0])
            self.assertIn("UPDATE white_internet_subscriptions", sql_clause)
            self.assertIn("SET traffic_used_bytes = traffic_downlink_bytes", sql_clause)
            self.assertIn("WHERE traffic_used_bytes > traffic_downlink_bytes", sql_clause)

    def test_migration_0029_downgrade_noop(self):
        with patch("alembic.op.execute") as mock_execute:
            self.migration.downgrade()
            mock_execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
