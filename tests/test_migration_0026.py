import importlib.util
import os
import unittest
from unittest.mock import MagicMock, patch


class TestMigration0026Metadata(unittest.TestCase):
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

    def test_downgrade_guard_checks_quotes_and_subs(self):
        bind = MagicMock()
        # 1. trial quotes exist -> raise
        bind.execute.side_effect = [
            MagicMock(scalar=MagicMock(return_value="public.tariff_quotes")),
            MagicMock(scalar=MagicMock(return_value=1)),
        ]
        with patch("alembic.op.get_bind", return_value=bind):
            with self.assertRaises(RuntimeError) as ctx:
                self.migration.downgrade()
            self.assertIn("Cannot safely downgrade migration 0026", str(ctx.exception))

        # 2. trial subs exist -> raise
        bind.execute.side_effect = [
            MagicMock(scalar=MagicMock(return_value="public.tariff_quotes")),
            MagicMock(scalar=MagicMock(return_value=None)),
            MagicMock(scalar=MagicMock(return_value=1)),
        ]
        with patch("alembic.op.get_bind", return_value=bind):
            with self.assertRaises(RuntimeError) as ctx:
                self.migration.downgrade()
            self.assertIn("Cannot safely downgrade migration 0026", str(ctx.exception))

    def test_downgrade_skips_quote_check_when_table_dropped(self):
        """After migration 0034 drops tariff_quotes, downgrade must not touch it."""
        bind = MagicMock()
        bind.execute.side_effect = [
            MagicMock(scalar=MagicMock(return_value=None)),
            MagicMock(scalar=MagicMock(return_value=None)),
        ]
        with patch("alembic.op.get_bind", return_value=bind):
            with patch("alembic.op.drop_constraint"), \
                patch("alembic.op.create_check_constraint"), \
                patch("alembic.op.drop_column"):
                # No trial data anywhere -> downgrade proceeds without raising.
                self.migration.downgrade()

    def test_upgrade_safely_logs_and_does_not_abort_on_ambiguous_data(self):
        """upgrade() must log warnings and complete without raising RuntimeError when ambiguous rows are detected."""
        bind = MagicMock()
        bind.dialect.name = "postgresql"
        # Mock responses for checks 4a and 4b: both return ambiguous records
        ambig_no_quote_result = MagicMock()
        ambig_no_quote_result.fetchall.return_value = [(1, 100, 5368709120)]
        ambig_counts_result = MagicMock()
        ambig_counts_result.fetchall.return_value = [(100, 2, 1)]
        bind.execute.side_effect = [ambig_no_quote_result, ambig_counts_result]

        with patch("alembic.op.get_bind", return_value=bind), \
             patch("alembic.op.drop_constraint") as mock_drop_c, \
             patch("alembic.op.create_check_constraint") as mock_create_c, \
             patch("alembic.op.execute") as mock_exec, \
             patch("alembic.op.add_column") as mock_add_col, \
             patch.object(self.migration.logger, "warning") as mock_warn:
            # Must NOT raise RuntimeError
            self.migration.upgrade()

            mock_drop_c.assert_called_once_with("ck_tariff_quotes_operation", "tariff_quotes", type_="check")
            mock_create_c.assert_called_once()
            mock_add_col.assert_called_once()
            # On postgresql: 4 calls (disable trigger DO block, quotes backfill, enable trigger DO block, subs backfill)
            self.assertEqual(mock_exec.call_count, 4)
            self.assertIn("DISABLE TRIGGER tariff_quotes_immutable", mock_exec.call_args_list[0][0][0])
            self.assertIn("UPDATE tariff_quotes", mock_exec.call_args_list[1][0][0])
            self.assertIn("ENABLE TRIGGER tariff_quotes_immutable", mock_exec.call_args_list[2][0][0])
            self.assertIn("UPDATE white_internet_subscriptions", mock_exec.call_args_list[3][0][0])
            self.assertEqual(mock_warn.call_count, 2)  # 4a and 4b both logged warnings

    def test_upgrade_skips_trigger_alter_on_non_postgres(self):
        """upgrade() must skip PostgreSQL DO blocks on non-PostgreSQL dialects."""
        bind = MagicMock()
        bind.dialect.name = "sqlite"
        ambig_no_quote_result = MagicMock()
        ambig_no_quote_result.fetchall.return_value = []
        ambig_counts_result = MagicMock()
        ambig_counts_result.fetchall.return_value = []
        bind.execute.side_effect = [ambig_no_quote_result, ambig_counts_result]

        with patch("alembic.op.get_bind", return_value=bind), \
             patch("alembic.op.drop_constraint"), \
             patch("alembic.op.create_check_constraint"), \
             patch("alembic.op.execute") as mock_exec, \
             patch("alembic.op.add_column"):
            self.migration.upgrade()
            # On non-postgres: exactly 2 calls (quotes backfill, subs backfill)
            self.assertEqual(mock_exec.call_count, 2)
            self.assertIn("UPDATE tariff_quotes", mock_exec.call_args_list[0][0][0])
            self.assertIn("UPDATE white_internet_subscriptions", mock_exec.call_args_list[1][0][0])


if __name__ == "__main__":
    unittest.main()
