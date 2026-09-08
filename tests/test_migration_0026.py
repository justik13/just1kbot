import importlib.util
import os
import unittest


class TestMigration0026(unittest.TestCase):
    """Verify migration 0026 metadata and semantics."""

    def setUp(self):
        file_path = os.path.join(os.path.dirname(__file__), "..", "alembic", "versions", "0026_wi_trial_semantics.py")
        spec = importlib.util.spec_from_file_location("migration_0026", file_path)
        self.migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.migration)

    def test_migration_chain(self):
        self.assertEqual(self.migration.revision, "0026_wi_trial_semantics")
        self.assertEqual(self.migration.down_revision, "0025_admin_qol_and_idempotency")

    def test_has_upgrade_and_downgrade(self):
        self.assertTrue(callable(getattr(self.migration, "upgrade", None)))
        self.assertTrue(callable(getattr(self.migration, "downgrade", None)))


if __name__ == "__main__":
    unittest.main()
