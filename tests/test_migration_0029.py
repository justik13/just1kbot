"""Unit tests for Alembic migration 0029_wi_notify_and_quote_opt."""

import importlib.util
import os
from pathlib import Path
import unittest

from alembic.config import Config
from alembic.script import ScriptDirectory
from database.models import TariffQuote, WhiteInternetSubscription


class Migration0029Tests(unittest.TestCase):
    """Test suite for migration 0029_wi_notify_and_quote_opt structure and model compliance."""

    def setUp(self):
        file_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "alembic",
            "versions",
            "0029_wi_notify_and_quote_opt.py",
        )
        spec = importlib.util.spec_from_file_location("migration_0029", file_path)
        self.migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.migration)

    def test_migration_0029_metadata_and_chain(self):
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        rev = scripts.get_revision("0029_wi_notify_and_quote_opt")
        self.assertIsNotNone(rev)
        self.assertEqual(rev.down_revision, "0028_two_balance_system")
        self.assertEqual(scripts.get_heads(), ["0029_wi_notify_and_quote_opt"])
        self.assertEqual(self.migration.revision, "0029_wi_notify_and_quote_opt")
        self.assertEqual(self.migration.down_revision, "0028_two_balance_system")

    def test_migration_0029_source_content(self):
        m29_path = Path("alembic/versions/0029_wi_notify_and_quote_opt.py")
        self.assertTrue(m29_path.is_file())
        content = m29_path.read_text(encoding="utf-8")

        self.assertIn("def upgrade()", content)
        self.assertIn("def downgrade()", content)
        self.assertIn("option_type", content)
        self.assertIn("notified_3d", content)
        self.assertIn("notified_1d", content)
        self.assertIn("notified_2h", content)
        self.assertIn("notified_expired", content)
        self.assertIn("ix_wi_subs_expiring_notify", content)

    def test_tariff_quote_model_compliance(self):
        quote_table = TariffQuote.__table__
        self.assertIn("option_type", quote_table.columns)
        col = quote_table.columns["option_type"]
        self.assertTrue(col.nullable)
        self.assertEqual(col.type.length, 20)

    def test_white_internet_subscription_model_compliance(self):
        sub_table = WhiteInternetSubscription.__table__
        for col_name in ("notified_3d", "notified_1d", "notified_2h", "notified_expired"):
            self.assertIn(col_name, sub_table.columns)
            col = sub_table.columns[col_name]
            self.assertFalse(col.nullable)

        index_names = {idx.name for idx in sub_table.indexes}
        self.assertIn("ix_wi_subs_expiring_notify", index_names)
        idx = next(i for i in sub_table.indexes if i.name == "ix_wi_subs_expiring_notify")
        self.assertIn("EXPIRED", str(idx.dialect_options["postgresql"]["where"]))


if __name__ == "__main__":
    unittest.main()
