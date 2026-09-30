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

# Each entry is (block label, statement). Keep them single-line and free of
# double quotes, dollar signs and backticks so the assembled command can be
# pasted straight into a double-quoted shell argument.
PREFLIGHT_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "1_MIGRATION_HEAD",
        "SELECT '1_MIGRATION_HEAD' AS block, CASE WHEN (SELECT version_num FROM alembic_version) = "
        f"'{EXPECTED_HEAD}' THEN 'OK' ELSE 'FAIL: MISMATCH have=' || "
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
    (
        "3A_REFERRAL_DISCOUNT_FLOOR",
        "SELECT '3A_REFERRAL_DISCOUNT_FLOOR' AS block, CASE WHEN count(*) = 0 "
        "THEN 'OK (0 violations)' ELSE 'CRITICAL: HAS VIOLATIONS n=' || count(*) END AS verdict "
        "FROM orders WHERE status = 'paid' AND payment_method <> 'wallet' "
        "AND metadata_->>'is_referral_discount' = 'true' AND amount_rub < 1",
    ),
    (
        "3B_PAID_ORDER_NOT_CREDITED",
        "SELECT '3B_PAID_ORDER_NOT_CREDITED' AS block, CASE WHEN count(*) = 0 "
        "THEN 'OK (0 violations)' ELSE 'CRITICAL: HAS VIOLATIONS n=' || count(*) END AS verdict "
        "FROM orders o WHERE o.status = 'paid' AND o.payment_method <> 'wallet' AND NOT EXISTS ("
        "SELECT 1 FROM payments p WHERE p.public_order_id = o.id::text AND EXISTS ("
        "SELECT 1 FROM account_ledger_entries l WHERE l.payment_id = p.id "
        "AND l.entry_type = 'payment_credit'))",
    ),
    (
        "3C_CREDIT_AMOUNT_DESYNC",
        "SELECT '3C_CREDIT_AMOUNT_DESYNC' AS block, CASE WHEN count(*) = 0 "
        "THEN 'OK (0 violations)' ELSE 'CRITICAL: HAS VIOLATIONS n=' || count(*) END AS verdict "
        "FROM payments p "
        "JOIN account_ledger_entries l ON l.payment_id = p.id AND l.entry_type = 'payment_credit' "
        "JOIN orders o ON o.id::text = p.public_order_id "
        "WHERE o.status = 'paid' AND l.amount <> o.amount_rub",
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

PREFLIGHT_SQL = " ".join(f"{stmt};" for _, stmt in PREFLIGHT_STATEMENTS)

PREFLIGHT_COMMAND = (
    'docker compose exec -T db psql -U just1kbot -d just1kbot_bot -x -c "'
    + PREFLIGHT_SQL
    + '"'
)


class TestPreflightCommandIsShellSafe(unittest.TestCase):
    """The command must survive being pasted into a double-quoted shell argument."""

    def test_command_has_exactly_one_pair_of_wrapper_quotes(self):
        self.assertEqual(PREFLIGHT_COMMAND.count('"'), 2)
        self.assertTrue(PREFLIGHT_COMMAND.startswith("docker compose exec -T db psql "))

    def test_sql_body_has_no_shell_hazard_characters(self):
        for char, name in (('"', "double quote"), ("$", "dollar sign"), ("`", "backtick"), ("\\", "backslash")):
            with self.subTest(character=name):
                self.assertNotIn(char, PREFLIGHT_SQL)

    def test_every_verdict_is_a_pass_fail_not_a_vanity_count(self):
        for label, stmt in PREFLIGHT_STATEMENTS:
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

    def test_all_four_mandatory_blocks_are_present(self):
        joined = PREFLIGHT_SQL
        self.assertIn("alembic_version", joined)          # migrations
        self.assertIn("pg_constraint", joined)             # PR invariants
        self.assertIn("account_ledger_entries", joined)    # financial integrity
        self.assertIn("status = 'pending'", joined)        # in-flight


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class TestPreflightStatementsExecute(unittest.IsolatedAsyncioTestCase):
    """Every documented statement must actually run against PostgreSQL."""

    async def asyncSetUp(self):
        self.engine = create_async_engine(DB)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_each_statement_returns_a_single_verdict_row(self):
        async with self.engine.connect() as conn:
            for label, stmt in PREFLIGHT_STATEMENTS:
                with self.subTest(block=label):
                    rows = (await conn.execute(text(stmt))).mappings().all()
                    self.assertEqual(len(rows), 1, f"{label} must return exactly one verdict row")
                    verdict = rows[0]["verdict"]
                    self.assertIsInstance(verdict, str)
                    self.assertTrue(verdict, f"{label} returned an empty verdict")

    async def test_schema_blocks_pass_after_migration(self):
        """Blocks 2A-2C must be green once 0032 has been applied."""
        async with self.engine.connect() as conn:
            for label in (
                "2A_KEPT_TABLE_FK_INTO_DROPPED",
                "2B_DROPPED_TABLES_GONE",
                "2C_NO_FUNCTION_REF_DROPPED",
            ):
                stmt = dict(PREFLIGHT_STATEMENTS)[label]
                verdict = (await conn.execute(text(stmt))).scalar_one()
                with self.subTest(block=label):
                    self.assertTrue(
                        verdict.startswith("OK"),
                        f"{label} is not green after migration: {verdict}",
                    )

    async def test_migration_head_matches_expected_revision(self):
        async with self.engine.connect() as conn:
            stmt = dict(PREFLIGHT_STATEMENTS)["1_MIGRATION_HEAD"]
            verdict = (await conn.execute(text(stmt))).scalar_one()
        self.assertEqual(verdict, "OK")


if __name__ == "__main__":
    unittest.main()