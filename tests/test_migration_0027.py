"""Unit tests for Alembic migration 0027_awg_subscription_system."""

import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from alembic.config import Config
from alembic.script import ScriptDirectory
from database.models import User, VPNProfile


class Migration0027Tests(unittest.TestCase):
    """Test suite for migration 0027_awg_subscription_system structure and model compliance."""

    def setUp(self):
        file_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "alembic",
            "versions",
            "0027_awg_subscription_system.py",
        )
        spec = importlib.util.spec_from_file_location("migration_0027", file_path)
        self.migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.migration)

    def test_migration_0027_metadata_and_chain(self):
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        rev = scripts.get_revision("0027_awg_subscription_system")
        self.assertIsNotNone(rev)
        self.assertEqual(rev.down_revision, "0026_wi_trial_semantics")
        self.assertEqual(scripts.get_heads(), ["0027_awg_subscription_system"])
        self.assertEqual(self.migration.revision, "0027_awg_subscription_system")
        self.assertEqual(self.migration.down_revision, "0026_wi_trial_semantics")

    def test_migration_0027_source_content(self):
        m27_path = Path("alembic/versions/0027_awg_subscription_system.py")
        self.assertTrue(m27_path.is_file())
        content = m27_path.read_text(encoding="utf-8")

        self.assertIn("def upgrade()", content)
        self.assertIn("def downgrade()", content)
        self.assertIn("subscription_token", content)
        self.assertIn("active_sub_devices", content)
        self.assertIn("device_type", content)
        self.assertIn("sub_device_hash", content)
        self.assertIn("ix_users_subscription_token", content)
        self.assertIn("ix_vpn_profiles_sub_device_hash", content)

    def test_user_model_compliance(self):
        user_table = User.__table__
        self.assertIn("subscription_token", user_table.columns)
        self.assertIn("active_sub_devices", user_table.columns)

        tok_col = user_table.columns["subscription_token"]
        self.assertTrue(tok_col.nullable)
        self.assertEqual(tok_col.type.length, 64)

        sub_col = user_table.columns["active_sub_devices"]
        self.assertTrue(sub_col.nullable)

    def test_vpn_profile_model_compliance(self):
        profile_table = VPNProfile.__table__
        self.assertIn("device_type", profile_table.columns)
        self.assertIn("sub_device_hash", profile_table.columns)

        dt_col = profile_table.columns["device_type"]
        self.assertFalse(dt_col.nullable)
        self.assertEqual(dt_col.type.length, 20)

        sdh_col = profile_table.columns["sub_device_hash"]
        self.assertTrue(sdh_col.nullable)
        self.assertEqual(sdh_col.type.length, 64)

    def test_upgrade_and_downgrade_call_op_methods(self):
        with patch("alembic.op.add_column") as mock_add_col, \
             patch("alembic.op.create_index") as mock_create_idx, \
             patch("alembic.op.drop_index") as mock_drop_idx, \
             patch("alembic.op.drop_column") as mock_drop_col:

            # Execute upgrade
            self.migration.upgrade()
            self.assertEqual(mock_add_col.call_count, 4)
            self.assertEqual(mock_create_idx.call_count, 2)

            # Execute downgrade
            self.migration.downgrade()
            self.assertEqual(mock_drop_idx.call_count, 2)
            self.assertEqual(mock_drop_col.call_count, 4)


if __name__ == "__main__":
    unittest.main()
