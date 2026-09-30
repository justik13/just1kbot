"""Executable contract for the documented production pre-flight command.

This module is the single source of truth for the SQL the operator runs before
deploying a schema-changing release. The same statements are assembled into the
command quoted in the pull request body:

    docker compose exec -T db psql -U just1kbot -d just1kbot_bot -x -c "<PREFLIGHT_SQL>"

Why this is a test and not prose in the PR:

* A pre-flight command nobody ever executed is a liability. Every statement here
  runs against a real PostgreSQL, so a typo or a renamed column fails the build
  instead of failing on production.
* Schema drift is caught early. If a later migration renames ``orders.status`` or
  reintroduces a reference to a dropped table, this test goes red before the
  operator is handed a broken command.

Rules this file deliberately obeys (AGENTS.md / gemini.md, pre-flight standard):

* No vanity metrics. Every count feeds a pass/fail verdict against a hazard
  condition derived from the diff; there is no bare informational count.
* The webhook inbox backlog is NOT probed. The worker that drained it was removed
  in PR #277, so such a check always reports a false positive.
"""

import os
import unittest

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DB = os.getenv("TEST_DATABASE_URL")

EXPECTED_BASE = "0031_awg_persistent_traffic"
EXPECTED_HEAD = "0032_drop_banking_residue"

DROPPED_TABLES = (
    "paid_value_ledger",
    "payment_provider_operations",
    "account_balance_reservations",
    "payment_refunds",
    "payment_events",
    "entitlement_entries",
    "payment_disputes",
    "provider_refund_operations",
)

_DROPPED_LIST = ",".join(f"'{name}'" for name in DROPPED_TABLES)

# Pre-flight statements: run BEFORE applying migration 0032 against the baseline database.
# Verifies expected baseline revision, invariant constraints on kept tables, and zero in-flight operations.
PREFLIGHT_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "1_MIGRATION_BASE",
        "SELECT '1_MIGRATION_BASE' AS block, CASE WHEN (SELECT version_num FROM alembic_version) = "
        f"'{EXPECTED_BASE}' THEN 'OK' ELSE 'FAIL: MISMATCH have=' || "
        "(SELECT version_num FROM alembic_version) END AS verdict",
    ),
    (
        "2A_KEPT_TABLE_FK_INTO_DROPPED",
        "SELECT '2A_KEPT_TABLE_FK_INTO_DROPPED' AS block, CASE WHEN count(*) = 0 "
        "THEN 'OK (0 violations)' ELSE 'CRITICAL: HAS VIOLATIONS n=' || count(*) END AS verdict "
        "FROM pg_constraint k WHERE k.contype = 'f' "
        f"AND k.confrelid IN (SELECT oid FROM pg_class WHERE relname IN ({_DROPPED_LIST})) "
        f"AND k.conrelid NOT IN (SELECT oid FROM pg_class WHERE relname IN ({_DROPPED_LIST}))",
    ),
    (
        "3A_REFERRAL_DISCOUNT_FLOOR",
        "SELECT '3A_REFERRAL_DISCOUNT_FLOOR' AS block, CASE WHEN count(*) = 0 "
        "THEN 'OK (0 violations)' ELSE 'CRITICAL: HAS VIOLATIONS n=' || count(*) END AS verdict "
        "FROM orders WHERE status = 'paid' AND payment_method <> 'wallet' "
        "AND (metadata ->> 'is_referral_discount') = 'true' AND amount_rub < 1",
    ),
    (
        "3B_PAID_TOPUP_NOT_CREDITED",
        "SELECT '3B_PAID_TOPUP_NOT_CREDITED' AS block, CASE WHEN count(*) = 0 "
        "THEN 'OK (0 violations)' ELSE 'CRITICAL: HAS VIOLATIONS n=' || count(*) END AS verdict "
        "FROM orders o WHERE o.status = 'paid' AND o.service_type = 'topup' AND NOT EXISTS ("
        "SELECT 1 FROM account_ledger_entries l WHERE l.order_id = o.id "
        "AND l.entry_type = 'payment_credit')",
    ),
    (
        "3C_CREDIT_AMOUNT_DESYNC",
        "SELECT '3C_CREDIT_AMOUNT_DESYNC' AS block, CASE WHEN count(*) = 0 "
        "THEN 'OK (0 violations)' ELSE 'CRITICAL: HAS VIOLATIONS n=' || count(*) END AS verdict "
        "FROM orders o "
        "JOIN account_ledger_entries l ON l.order_id = o.id AND l.entry_type = 'payment_credit' "
        "WHERE o.status = 'paid' AND o.service_type = 'topup' AND l.amount <> o.amount_rub",
    ),
    (
        "4A_INFLIGHT_ORDERS",
        "SELECT '4A_INFLIGHT_ORDERS' AS block, CASE WHEN count(*) = 0 THEN 'OK (clean)' "
        "ELSE 'WARN: IN-FLIGHT ORDERS n=' || count(*) END AS verdict FROM orders "
        "WHERE status = 'pending' AND created_at > now() - interval '30 minutes'",
    ),
    (
        "4B_INFLIGHT_PAYMENTS",
        "SELECT '4B_INFLIGHT_PAYMENTS' AS block, CASE WHEN count(*) = 0 THEN 'OK (clean)' "
        "ELSE 'WARN: IN-FLIGHT PAYMENTS n=' || count(*) END AS verdict FROM payments "
        "WHERE provider_status IN ('creating','pending','waiting_for_capture') "
        "AND created_at > now() - interval '30 minutes'",
    ),
)

# Post-flight statements: run AFTER applying migration 0032.
# Verifies expected head revision, 8 dropped tables gone, and clean functions without stale references.
POSTFLIGHT_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "1_MIGRATION_HEAD",
        "SELECT '1_MIGRATION_HEAD' AS block, CASE WHEN (SELECT version_num FROM alembic_version) = "
        f"'{EXPECTED_HEAD}' THEN 'OK' ELSE 'FAIL: MISMATCH have=' || "
        "(SELECT version_num FROM alembic_version) END AS verdict",
    ),
    (
        "2B_DROPPED_TABLES_GONE",
        "SELECT '2B_DROPPED_TABLES_GONE' AS block, CASE WHEN count(*) = 0 "
        "THEN 'OK (0 tables remain)' ELSE 'CRITICAL: n=' || count(*) || "
        "' dropped tables still present' END AS verdict "
        f"FROM pg_class WHERE relname IN ({_DROPPED_LIST})",
    ),
    (
        "2C_NO_FUNCTION_REF_DROPPED",
        "SELECT '2C_NO_FUNCTION_REF_DROPPED' AS block, CASE WHEN count(*) = 0 "
        "THEN 'OK (0 violations)' ELSE 'CRITICAL: n=' || count(*) || "
        "' functions still reference dropped tables' END AS verdict "
        "FROM pg_proc WHERE prosrc ~ '" + "|".join(DROPPED_TABLES) + "'",
    ),
)

PREFLIGHT_SQL = " ".join(f"{stmt};" for _, stmt in PREFLIGHT_STATEMENTS)
PREFLIGHT_COMMAND = (
    'docker compose exec -T db psql -U just1kbot -d just1kbot_bot -x -c "'
    + PREFLIGHT_SQL
    + '"'
)

POSTFLIGHT_SQL = " ".join(f"{stmt};" for _, stmt in POSTFLIGHT_STATEMENTS)
POSTFLIGHT_COMMAND = (
    'docker compose exec -T db psql -U just1kbot -d just1kbot_bot -x -c "'
    + POSTFLIGHT_SQL
    + '"'
)


class TestPreflightCommandIsShellSafe(unittest.TestCase):
    """The commands must survive being pasted into a double-quoted shell argument."""

    def test_commands_have_exactly_one_pair_of_wrapper_quotes(self):
        for name, cmd in (("preflight", PREFLIGHT_COMMAND), ("postflight", POSTFLIGHT_COMMAND)):
            with self.subTest(command=name):
                self.assertEqual(cmd.count('"'), 2)
                self.assertTrue(cmd.startswith("docker compose exec -T db psql "))

    def test_sql_body_has_no_shell_hazard_characters(self):
        for sql_name, sql in (("preflight", PREFLIGHT_SQL), ("postflight", POSTFLIGHT_SQL)):
            for char, char_name in (('"', "double quote"), ("$", "dollar sign"), ("`", "backtick"), ("\\", "backslash")):
                with self.subTest(command=sql_name, character=char_name):
                    self.assertNotIn(char, sql)

    def test_every_verdict_is_a_pass_fail_not_a_vanity_count(self):
        for label, stmt in PREFLIGHT_STATEMENTS + POSTFLIGHT_STATEMENTS:
            with self.subTest(block=label):
                self.assertIn("AS verdict", stmt)
                self.assertIn("CASE WHEN", stmt)
                # A green verdict and a red/warn verdict must both be reachable.
                self.assertIn("'OK", stmt)
                self.assertTrue(
                    any(f"'{marker}" in stmt for marker in ("CRITICAL", "WARN", "FAIL")),
                    f"{label} has no failure verdict",
                )

    def test_webhook_inbox_backlog_is_not_probed(self):
        # The inbox worker was removed in PR #277; pending rows accumulate forever,
        # so probing them would always report a false positive.
        self.assertNotIn("webhook_inbox", PREFLIGHT_SQL)
        self.assertNotIn("webhook_inbox", POSTFLIGHT_SQL)

    def test_all_four_mandatory_blocks_are_present_in_preflight(self):
        joined = PREFLIGHT_SQL
        self.assertIn("alembic_version", joined)          # migrations
        self.assertIn("pg_constraint", joined)             # PR invariants
        self.assertIn("account_ledger_entries", joined)    # financial integrity
        self.assertIn("status = 'pending'", joined)        # in-flight

    def test_sql_uses_real_column_names_not_orm_attribute_names(self):
        """The ORM renames reserved columns; raw SQL must use the database name.

        ``Order.metadata_`` is the Python attribute, but the column is ``metadata``.
        SQL written against the attribute name fails at runtime with
        UndefinedColumnError, which is exactly how this shipped once.
        """
        from database.models import Order

        self.assertIn("metadata ->> 'is_referral_discount'", PREFLIGHT_SQL)
        self.assertNotIn("metadata_", PREFLIGHT_SQL)
        # And the attribute must still map to that column, so the SQL above is not
        # silently drifting away from the model either.
        self.assertEqual(Order.metadata_.property.columns[0].name, "metadata")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class TestPreflightStatementsExecute(unittest.IsolatedAsyncioTestCase):
    """Every documented statement must actually run against PostgreSQL."""

    async def asyncSetUp(self):
        self.engine = create_async_engine(DB)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _verdict_for(self, stmt: str) -> str:
        """Run one statement in its own transaction and return its verdict.

        Each statement gets a fresh connection on purpose. If one of them is
        invalid, an aborted transaction would otherwise mask every later
        statement and the failure would be reported against the wrong block.
        """
        async with self.engine.connect() as conn:
            row = (await conn.execute(text(stmt))).mappings().one()
        self.assertIn("verdict", row.keys(), f"statement has no verdict column: {stmt[:80]}")
        return row["verdict"]

    async def test_every_statement_executes_and_returns_a_verdict(self):
        failures = []
        for label, stmt in PREFLIGHT_STATEMENTS + POSTFLIGHT_STATEMENTS:
            try:
                verdict = await self._verdict_for(stmt)
            except Exception as exc:
                failures.append(f"{label}: {type(exc).__name__}: {exc}")
                continue
            if not verdict:
                failures.append(f"{label}: returned an empty verdict")
        self.assertEqual(failures, [], "statements failed:\n" + "\n".join(failures))

    async def test_schema_blocks_pass_after_migration(self):
        """Post-flight blocks must be green once 0032 has been applied."""
        statements = dict(PREFLIGHT_STATEMENTS + POSTFLIGHT_STATEMENTS)
        for label in (
            "2A_KEPT_TABLE_FK_INTO_DROPPED",
            "2B_DROPPED_TABLES_GONE",
            "2C_NO_FUNCTION_REF_DROPPED",
        ):
            verdict = await self._verdict_for(statements[label])
            with self.subTest(block=label):
                self.assertTrue(
                    verdict.startswith("OK"),
                    f"{label} is not green after migration: {verdict}",
                )

    async def test_migration_head_matches_expected_revision(self):
        verdict = await self._verdict_for(dict(POSTFLIGHT_STATEMENTS)["1_MIGRATION_HEAD"])
        self.assertEqual(verdict, "OK", f"alembic_version is not at {EXPECTED_HEAD}")

    async def test_financial_blocks_pass_on_clean_db(self):
        """Financial pre-flight blocks must report OK (0 violations) on a clean database."""
        statements = dict(PREFLIGHT_STATEMENTS)
        for label in (
            "3A_REFERRAL_DISCOUNT_FLOOR",
            "3B_PAID_TOPUP_NOT_CREDITED",
            "3C_CREDIT_AMOUNT_DESYNC",
        ):
            verdict = await self._verdict_for(statements[label])
            with self.subTest(block=label):
                self.assertEqual(verdict, "OK (0 violations)")


if __name__ == "__main__":
    unittest.main()