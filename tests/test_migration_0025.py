"""Unit tests for Alembic migration 0025_admin_qol_and_idempotency."""

from pathlib import Path
import unittest

from alembic.config import Config
from alembic.script import ScriptDirectory
from database.models import AdminOperationIdempotency, User, WhiteInternetSubscription


class Migration0025Tests(unittest.TestCase):
    """Test suite for migration 0025_admin_qol_and_idempotency structure and model compliance."""

    def test_migration_0025_in_script_directory(self):
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        rev = scripts.get_revision("0025_admin_qol_and_idempotency")
        self.assertIsNotNone(rev)
        self.assertEqual(rev.down_revision, "0024_wi_device_limit")
        self.assertEqual(scripts.get_heads(), ["0028_two_balance_system"])

    def test_migration_0025_source_content(self):
        m25_path = Path("alembic/versions/0025_admin_qol_and_idempotency.py")
        self.assertTrue(m25_path.is_file())
        content = m25_path.read_text(encoding="utf-8")

        self.assertIn("def upgrade()", content)
        self.assertIn("def downgrade()", content)
        self.assertIn("admin_operation_idempotency", content)
        self.assertIn("op_key", content)
        self.assertIn("last_trial_reset_at", content)
        self.assertIn("ix_users_username_lower", content)
        self.assertIn("ix_white_internet_subscriptions_active_hwids", content)

    def test_admin_operation_idempotency_model_attributes(self):
        table = AdminOperationIdempotency.__table__
        self.assertEqual(table.name, "admin_operation_idempotency")
        self.assertIn("op_key", table.columns)
        self.assertIn("admin_id", table.columns)
        self.assertIn("target_id", table.columns)
        self.assertIn("created_at", table.columns)

        op_col = table.columns["op_key"]
        self.assertTrue(op_col.primary_key)
        self.assertFalse(op_col.nullable)

        admin_col = table.columns["admin_id"]
        self.assertFalse(admin_col.nullable)

        target_col = table.columns["target_id"]
        self.assertFalse(target_col.nullable)

        created_col = table.columns["created_at"]
        self.assertFalse(created_col.nullable)

    def test_user_and_wi_subscription_models(self):
        user_table = User.__table__
        self.assertIn("last_trial_reset_at", user_table.columns)
        self.assertTrue(user_table.columns["last_trial_reset_at"].nullable)

        wi_table = WhiteInternetSubscription.__table__
        index_names = {idx.name for idx in wi_table.indexes if idx.name}
        self.assertIn("ix_white_internet_subscriptions_active_hwids", index_names)
