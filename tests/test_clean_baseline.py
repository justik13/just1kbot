import unittest
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

VERSIONS = Path(__file__).parents[1] / "alembic" / "versions"


class CleanBaselineTests(unittest.TestCase):
    def test_exactly_one_root_revision(self):
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        self.assertEqual(scripts.get_bases(), ["0001_clean_baseline"])

    def test_baseline_guards_live_financial_tables(self):
        """The baseline must protect the financial tables that are still live."""
        source = (VERSIONS / "0001_clean_baseline.py").read_text(encoding="utf-8")
        for required in (
            "account_ledger_append_only",
            "account_allocations_append_only",
            "validate_account_allocation",
            "tariff_quotes_immutable",
            "tariff_versions_immutable",
        ):
            self.assertIn(required, source)
        for removed in (
            "phase6_",
            "legacy_change_quote_source_snapshot_missing",
            "payment_fulfillment_operations",
        ):
            self.assertNotIn(removed, source)

    def test_head_migration_drops_the_banking_residue(self):
        """The abandoned banking schema must stay dropped.

        This is the regression guard for the PR #277 purge: that PR deleted the
        service layer but left the tables behind. Previously no test asserted
        their removal, so the baseline effectively protected the dead structure.
        """
        source = (VERSIONS / "0032_drop_banking_residue.py").read_text(encoding="utf-8")
        for table in (
            "paid_value_ledger",
            "payment_provider_operations",
            "account_balance_reservations",
            "payment_refunds",
            "payment_events",
            "entitlement_entries",
            "payment_disputes",
            "provider_refund_operations",
        ):
            self.assertIn(table, source)
        # The trigger that used to probe the ledger must no longer do so.
        self.assertNotIn("FROM paid_value_ledger", source)
        # And payments must survive: the admin UI and dashboard still read it.
        self.assertNotIn("DROP TABLE IF EXISTS public.payments", source)

    def test_legacy_downgrades_tolerate_dropped_tables(self):
        """Downgrades touching dropped tables must be guarded by to_regclass.

        Migration 0032 removes the banking tables without recreating them, so
        every older downgrade that names one must skip itself when the table
        is gone — otherwise `alembic downgrade base` aborts mid-chain. This
        pins the guards (execution itself is covered by the CI downgrade).
        """
        for filename, table in (
            ("0004_referral_entitlements.py", "entitlement_entries"),
            ("0012_payment_debits_and_audit_target_idx.py", "payment_events"),
            ("0017_white_internet_durations.py", "paid_value_ledger"),
            ("0027_backfill_entitlements.py", "entitlement_entries"),
        ):
            with self.subTest(migration=filename):
                source = (VERSIONS / filename).read_text(encoding="utf-8")
                self.assertIn("to_regclass", source)
                self.assertIn(table, source)


    def test_quote_drop_migration_backfills_and_drops(self):
        """Migration 0034 must backfill consumed quotes into orders, re-link
        ledger debits, and drop the quote/version tables without recreating.

        Regression guard for the White Internet billing port: dropping the
        tables without backfill would orphan purchase history and trial
        checks that now read Orders.
        """
        source = (VERSIONS / "0034_drop_tariff_quotes.py").read_text(encoding="utf-8")
        self.assertEqual(
            (VERSIONS / "0034_drop_tariff_quotes.py").exists(), True
        )
        for table in ("tariff_quotes", "tariff_versions"):
            self.assertIn(table, source)
        self.assertIn("migrated_from_quote", source)
        self.assertIn("DROP TABLE IF EXISTS public.tariff_quotes", source)
        self.assertIn("DROP TABLE IF EXISTS public.tariff_versions", source)
        # Append-only trigger guard around ledger backfill
        self.assertIn("_set_ledger_immutable_trigger", source)
        self.assertIn("DISABLE TRIGGER account_ledger_append_only", source)
        self.assertIn("ENABLE TRIGGER account_ledger_append_only", source)
        # Fail-closed: abort instead of dropping with dangling ledger links.
        self.assertIn("quote_id IS NOT NULL", source)

    def test_quote_drop_downgrades_tolerate_dropped_tables(self):
        """Downgrades touching quotes/versions must skip themselves when gone.

        Migration 0034 drops the tables without recreating them, so every
        older downgrade that names one must check to_regclass first —
        otherwise `alembic downgrade base` aborts mid-chain.
        """
        for filename, table in (
            ("0016_white_internet_subscriptions.py", "tariff_quotes"),
            ("0017_white_internet_durations.py", "tariff_versions"),
            ("0018_simplify_wi_traffic.py", "tariff_quotes"),
            ("0026_wi_trial_semantics.py", "tariff_quotes"),
        ):
            with self.subTest(migration=filename):
                source = (VERSIONS / filename).read_text(encoding="utf-8")
                self.assertIn("to_regclass", source)
                self.assertIn(table, source)

    def test_quote_drop_backfill_enrichment_unit(self):
        """Verify 0034 backfill logic preserves topup and add_device_slot semantics with traffic_bytes."""
        from datetime import datetime, timezone
        from decimal import Decimal
        import importlib.util
        import json
        from unittest.mock import MagicMock

        spec = importlib.util.spec_from_file_location(
            "m0034", str(VERSIONS / "0034_drop_tariff_quotes.py")
        )
        m0034 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m0034)

        now = datetime.now(timezone.utc)
        quotes = [
            # 1. Trial quote
            (1, 10, "white_internet", "trial", Decimal("0.00"), now, now, 1, 72, 2, 72, "Trial"),
            # 2. Topup 25 GiB quote
            (2, 11, "white_internet", "purchase", Decimal("100.00"), now, now, 1, 720, 2, 0, "White Internet"),
            # 3. Addon 200 RUB quote (50 GiB topup or device slot)
            (3, 12, "white_internet", "purchase", Decimal("200.00"), now, now, 1, 720, 2, 0, "White Internet"),
            # 4. Regular 30d purchase quote
            (4, 13, "white_internet", "purchase", Decimal("300.00"), now, now, 1, 720, 2, 720, "White Internet"),
        ]

        recorded_inserts = []
        recorded_updates = []

        def fake_execute(stmt, params=None):
            s = str(stmt)
            if "SELECT q.id" in s:
                m = MagicMock()
                m.fetchall.return_value = quotes
                return m
            elif "INSERT INTO orders" in s:
                recorded_inserts.append(params)
                return None
            elif "UPDATE account_ledger_entries" in s:
                recorded_updates.append(params)
                return None
            elif "entry_type = 'purchase_debit'" in s:
                m = MagicMock()
                m.scalar.return_value = 1
                return m
            elif "WHERE quote_id IS NOT NULL" in s:
                m = MagicMock()
                m.scalar.return_value = 0
                return m
            elif "migrated_from_quote" in s:
                m = MagicMock()
                m.scalar.return_value = len(quotes)
                return m
            return MagicMock()

        bind = MagicMock()
        bind.execute.side_effect = fake_execute

        m0034._backfill_consumed_quotes(bind)

        self.assertEqual(len(recorded_inserts), 4)
        self.assertEqual(len(recorded_updates), 4)

        # 1. Trial order
        trial_meta = json.loads(recorded_inserts[0]["metadata"])
        self.assertEqual(trial_meta["operation"], "trial")
        self.assertEqual(trial_meta["tariff_name"], "Trial")
        self.assertEqual(recorded_inserts[0]["days"], 3)
        self.assertEqual(recorded_inserts[0]["traffic_bytes"], 0)

        # 2. Topup order (25 GiB)
        topup_meta = json.loads(recorded_inserts[1]["metadata"])
        self.assertEqual(topup_meta["operation"], "topup")
        self.assertEqual(topup_meta["tariff_name"], "White Internet")
        self.assertEqual(recorded_inserts[1]["days"], 0)
        self.assertEqual(recorded_inserts[1]["traffic_bytes"], 25 * 1024**3)

        # 3. Addon 200 RUB order (50 GiB topup or device slot)
        slot_meta = json.loads(recorded_inserts[2]["metadata"])
        self.assertEqual(slot_meta["operation"], "topup_or_device_slot")
        self.assertEqual(slot_meta["tariff_name"], "White Internet")
        self.assertEqual(recorded_inserts[2]["days"], 0)
        self.assertEqual(recorded_inserts[2]["traffic_bytes"], 50 * 1024**3)

        # 4. Regular purchase order
        purchase_meta = json.loads(recorded_inserts[3]["metadata"])
        self.assertEqual(purchase_meta["operation"], "purchase")
        self.assertEqual(recorded_inserts[3]["days"], 30)
        self.assertEqual(recorded_inserts[3]["traffic_bytes"], 0)

    def test_quote_drop_backfill_leftover_aborts(self):
        """Verify 0034 raises RuntimeError if leftover ledger rows still reference quote_id."""
        import importlib.util
        from unittest.mock import MagicMock

        spec = importlib.util.spec_from_file_location(
            "m0034", str(VERSIONS / "0034_drop_tariff_quotes.py")
        )
        m0034 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m0034)

        def fake_execute(stmt, params=None):
            s = str(stmt)
            if "SELECT q.id" in s:
                m = MagicMock()
                m.fetchall.return_value = []
                return m
            elif "SELECT count(*)" in s:
                m = MagicMock()
                m.scalar.return_value = 2  # 2 leftovers!
                return m
            return MagicMock()

        bind = MagicMock()
        bind.execute.side_effect = fake_execute

        with self.assertRaises(RuntimeError) as cm:
            m0034._backfill_consumed_quotes(bind)
        self.assertIn("2 ledger rows still reference quotes", str(cm.exception))


    def test_quote_drop_backfill_trigger_guard_lifecycle(self):
        """Verify trigger is disabled before ledger updates and re-enabled in finally."""
        from datetime import datetime, timezone
        from decimal import Decimal
        import importlib.util
        from unittest.mock import MagicMock

        spec = importlib.util.spec_from_file_location(
            "m0034", str(VERSIONS / "0034_drop_tariff_quotes.py")
        )
        m0034 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m0034)

        now = datetime.now(timezone.utc)
        quotes = [
            (1, 10, "white_internet", "purchase", Decimal("100.00"), now, now, 1, 720, 2, 720, "WI"),
        ]

        executed_actions = []

        def tracking_execute(stmt, params=None):
            s = str(stmt)
            if "DISABLE TRIGGER" in s:
                executed_actions.append("trigger_disabled")
            elif "ENABLE TRIGGER" in s:
                executed_actions.append("trigger_enabled")
            elif "SELECT q.id" in s:
                executed_actions.append("select_quotes")
                m = MagicMock()
                m.fetchall.return_value = quotes
                return m
            elif "entry_type = 'purchase_debit'" in s:
                executed_actions.append("check_debit")
                m = MagicMock()
                m.scalar.return_value = 1
                return m
            elif "INSERT INTO orders" in s:
                executed_actions.append("insert_order")
            elif "UPDATE account_ledger_entries" in s:
                executed_actions.append("update_ledger")
            elif "WHERE quote_id IS NOT NULL" in s:
                executed_actions.append("count_leftovers")
                m = MagicMock()
                m.scalar.return_value = 0
                return m
            elif "migrated_from_quote" in s:
                executed_actions.append("count_orders")
                m = MagicMock()
                m.scalar.return_value = len(quotes)
                return m
            return MagicMock()

        bind = MagicMock()
        bind.dialect.name = "postgresql"
        bind.execute.side_effect = tracking_execute

        # 1. Normal run: disable -> debit_check -> insert -> update -> enable -> count -> orders_count
        m0034._backfill_consumed_quotes(bind)
        self.assertEqual(
            executed_actions,
            [
                "select_quotes",
                "trigger_disabled",
                "check_debit",
                "insert_order",
                "update_ledger",
                "trigger_enabled",
                "count_leftovers",
                "count_orders",
            ],
        )

        # 2. Failure run: trigger is re-enabled even on failure
        executed_actions.clear()

        def failing_execute(stmt, params=None):
            s = str(stmt)
            if "DISABLE TRIGGER" in s:
                executed_actions.append("trigger_disabled")
            elif "ENABLE TRIGGER" in s:
                executed_actions.append("trigger_enabled")
            elif "SELECT q.id" in s:
                executed_actions.append("select_quotes")
                m = MagicMock()
                m.fetchall.return_value = quotes
                return m
            elif "entry_type = 'purchase_debit'" in s:
                m = MagicMock()
                m.scalar.return_value = 1
                return m
            elif "UPDATE account_ledger_entries" in s:
                executed_actions.append("update_ledger_fail")
                raise RuntimeError("ledger simulated failure")
            return MagicMock()

        bind.execute.side_effect = failing_execute
        with self.assertRaises(RuntimeError) as cm:
            m0034._backfill_consumed_quotes(bind)
        self.assertIn("ledger simulated failure", str(cm.exception))
        self.assertIn("trigger_disabled", executed_actions)
        self.assertIn("trigger_enabled", executed_actions)
        # Ensure enable was called after failure
        self.assertEqual(executed_actions[-1], "trigger_enabled")

        # 3. Non-postgresql dialect (e.g. SQLite) safely skips trigger DDL
        sqlite_bind = MagicMock()
        sqlite_bind.dialect.name = "sqlite"
        m0034._set_ledger_immutable_trigger(sqlite_bind, enable=False)
        m0034._set_ledger_immutable_trigger(sqlite_bind, enable=True)
        sqlite_bind.execute.assert_not_called()

    def test_quote_drop_backfill_missing_debit_aborts(self):
        """Verify backfill aborts if a consumed paid quote has no matching purchase_debit."""
        from datetime import datetime, timezone
        from decimal import Decimal
        import importlib.util
        from unittest.mock import MagicMock

        spec = importlib.util.spec_from_file_location(
            "m0034", str(VERSIONS / "0034_drop_tariff_quotes.py")
        )
        m0034 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m0034)

        now = datetime.now(timezone.utc)
        quotes = [
            (99, 10, "white_internet", "purchase", Decimal("300.00"), now, now, 1, 720, 2, 720, "WI"),
        ]

        def fake_execute(stmt, params=None):
            s = str(stmt)
            if "SELECT q.id" in s:
                m = MagicMock()
                m.fetchall.return_value = quotes
                return m
            elif "entry_type = 'purchase_debit'" in s:
                m = MagicMock()
                m.scalar.return_value = 0  # Missing debit!
                return m
            return MagicMock()

        bind = MagicMock()
        bind.execute.side_effect = fake_execute

        with self.assertRaises(RuntimeError) as cm:
            m0034._backfill_consumed_quotes(bind)
        self.assertIn("has no matching purchase_debit", str(cm.exception))

    def test_quote_drop_backfill_trial_without_debit_succeeds(self):
        """Verify 0 RUB trial quotes succeed without needing a ledger debit."""
        from datetime import datetime, timezone
        from decimal import Decimal
        import importlib.util
        from unittest.mock import MagicMock

        spec = importlib.util.spec_from_file_location(
            "m0034", str(VERSIONS / "0034_drop_tariff_quotes.py")
        )
        m0034 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m0034)

        now = datetime.now(timezone.utc)
        quotes = [
            (101, 10, "white_internet", "trial", Decimal("0.00"), now, now, 1, 72, 2, 72, "Trial"),
        ]

        def fake_execute(stmt, params=None):
            s = str(stmt)
            if "SELECT q.id" in s:
                m = MagicMock()
                m.fetchall.return_value = quotes
                return m
            elif "WHERE quote_id IS NOT NULL" in s:
                m = MagicMock()
                m.scalar.return_value = 0
                return m
            elif "migrated_from_quote" in s:
                m = MagicMock()
                m.scalar.return_value = 1
                return m
            return MagicMock()

        bind = MagicMock()
        bind.execute.side_effect = fake_execute
        m0034._backfill_consumed_quotes(bind)  # Must not raise

    def test_quote_drop_backfill_migrated_orders_mismatch_aborts(self):
        """Verify backfill aborts if the count of created orders does not match quotes."""
        from datetime import datetime, timezone
        from decimal import Decimal
        import importlib.util
        from unittest.mock import MagicMock

        spec = importlib.util.spec_from_file_location(
            "m0034", str(VERSIONS / "0034_drop_tariff_quotes.py")
        )
        m0034 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m0034)

        now = datetime.now(timezone.utc)
        quotes = [
            (1, 10, "white_internet", "trial", Decimal("0.00"), now, now, 1, 72, 2, 72, "Trial"),
        ]

        def fake_execute(stmt, params=None):
            s = str(stmt)
            if "SELECT q.id" in s:
                m = MagicMock()
                m.fetchall.return_value = quotes
                return m
            elif "WHERE quote_id IS NOT NULL" in s:
                m = MagicMock()
                m.scalar.return_value = 0
                return m
            elif "migrated_from_quote" in s:
                m = MagicMock()
                m.scalar.return_value = 0  # Mismatch: 0 orders instead of 1
                return m
            return MagicMock()

        bind = MagicMock()
        bind.execute.side_effect = fake_execute

        with self.assertRaises(RuntimeError) as cm:
            m0034._backfill_consumed_quotes(bind)
        self.assertIn("created 0 orders but expected 1", str(cm.exception))


if __name__ == "__main__":
    unittest.main()


