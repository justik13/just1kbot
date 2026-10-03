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
        "FROM orders o WHERE o.status = 'paid' AND o.service_type = 'topup' "
        "AND coalesce(o.metadata ->> 'settlement_held', 'false') <> 'true' "
        "AND NOT EXISTS ("
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
        "3D_SETTLEMENT_HELD_TOPUPS",
        "SELECT '3D_SETTLEMENT_HELD_TOPUPS' AS block, CASE WHEN count(*) = 0 "
        "THEN 'OK (clean)' ELSE 'WARN: SETTLEMENT-HELD ORDERS n=' || count(*) END AS verdict "
        "FROM orders WHERE status = 'paid' "
        "AND coalesce(metadata ->> 'settlement_held', 'false') = 'true'",
    ),
    (
        "3E_NEGATIVE_LEDGER_POSITIONS",
        "SELECT '3E_NEGATIVE_LEDGER_POSITIONS' AS block, CASE WHEN count(*) = 0 "
        "THEN 'OK (0 violations)' ELSE 'CRITICAL: HAS VIOLATIONS n=' || count(*) END AS verdict "
        "FROM (SELECT user_id FROM account_ledger_entries "
        "GROUP BY user_id HAVING SUM(amount) < 0) AS neg",
    ),
    (
        "3G_REFUND_DEBIT_EXCEEDS_CREDIT",
        "SELECT '3G_REFUND_DEBIT_EXCEEDS_CREDIT' AS block, CASE WHEN count(*) = 0 "
        "THEN 'OK (0 violations)' ELSE 'CRITICAL: HAS VIOLATIONS n=' || count(*) END AS verdict "
        "FROM orders o WHERE o.status IN ('paid', 'refunded') AND ("
        "SELECT COALESCE(SUM(-l.amount), 0) FROM account_ledger_entries l "
        "WHERE l.order_id = o.id AND l.entry_type IN ('refund_debit', 'chargeback_debit')"
        ") > (SELECT COALESCE(SUM(l2.amount), 0) FROM account_ledger_entries l2 "
        "WHERE l2.order_id = o.id AND l2.entry_type = 'payment_credit')",
    ),
    (
        "3F_ACCOUNTS_ON_HOLD",
        "SELECT '3F_ACCOUNTS_ON_HOLD' AS block, CASE WHEN count(*) = 0 "
        "THEN 'OK (clean)' ELSE 'WARN: ACCOUNTS ON HOLD n=' || count(*) END AS verdict "
        "FROM users WHERE financial_hold OR topup_blocked",
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
    (
        "4C_BROADCAST_STOPPING_ORPHANS",
        "SELECT '4C_BROADCAST_STOPPING_ORPHANS' AS block, CASE WHEN count(*) = 0 THEN 'OK (clean)' "
        "ELSE 'WARN: ORPHAN STOPPING ROWS n=' || count(*) END AS verdict FROM broadcast_progress "
        "WHERE status = 'stopping'",
    ),
)

VALID_POSTFLIGHT_HEADS = (
    "0032_drop_banking_residue",
    "0033_wi_order_checkout",
    "0034_drop_tariff_quotes",
)
_HEADS_LIST = ",".join(f"'{h}'" for h in VALID_POSTFLIGHT_HEADS)

# Post-flight statements: run AFTER applying migration 0032.
# Verifies expected head revision, 8 dropped tables gone, and clean functions without stale references.
POSTFLIGHT_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "1_MIGRATION_HEAD",
        "SELECT '1_MIGRATION_HEAD' AS block, CASE WHEN (SELECT version_num FROM alembic_version) IN "
        f"({_HEADS_LIST}) THEN 'OK' ELSE 'FAIL: MISMATCH have=' || "
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
        self.assertIn("metadata ->> 'settlement_held'", PREFLIGHT_SQL)
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

    async def _verdict_with_hazard(self, kind: str, label: str) -> str:
        """Seed one synthetic violation and read its block verdict.

        Everything runs inside a single transaction that is always rolled
        back: the ledger is append-only (DELETE/UPDATE raise), so cleanup
        by deletion is impossible — rollback is the only way back.
        """
        import random
        import uuid as uuid_lib

        tag = uuid_lib.uuid4().hex[:12]
        statements = dict(PREFLIGHT_STATEMENTS)
        async with self.engine.connect() as conn:
            trans = await conn.begin()
            try:
                tg = 7900000000000 + random.randint(1, 999999999)
                user_id = int(
                    (
                        await conn.execute(
                            text(
                                "INSERT INTO users (telegram_id, is_deleted, "
                                "is_banned, is_bot_blocked, device_limit, "
                                "notification_retry_count, notified_3d, "
                                "notified_1d, notified_2h, notified_expired, "
                                "notified_grace_12h, device_creations_today, "
                                "created_at) VALUES (:tg, false, false, false, "
                                "0, 0, false, false, false, false, false, 0, "
                                "now()) RETURNING id"
                            ),
                            {"tg": tg},
                        )
                    ).one()[0]
                )
                order_id = str(
                    (
                        await conn.execute(
                            text(
                                "INSERT INTO orders (id, user_id, service_type, "
                                "amount_rub, payment_method, status, metadata) "
                                "VALUES (gen_random_uuid(), :uid, 'topup', "
                                "'200', 'yookassa', 'paid', '{}'::jsonb) "
                                "RETURNING id"
                            ),
                            {"uid": user_id},
                        )
                    ).one()[0]
                )

                async def ledger(
                    entry_type: str, amount: str, oid: str | None, suffix: str
                ) -> None:
                    await conn.execute(
                        text(
                            "INSERT INTO account_ledger_entries (id, user_id, "
                            "entry_type, amount, currency, order_id, "
                            "idempotency_key) VALUES (:lid, :uid, :etype, "
                            ":amt, 'RUB', CAST(:oid AS uuid), :key)"
                        ),
                        {
                            "lid": random.randint(10**12, 10**13),
                            "uid": user_id,
                            "etype": entry_type,
                            "amt": amount,
                            "oid": oid,
                            "key": f"preflight-test-{tag}-{suffix}",
                        },
                    )

                if kind == "desync":
                    await ledger("payment_credit", "199", order_id, "c")
                elif kind == "negative":
                    await conn.execute(
                        text("DELETE FROM orders WHERE id = CAST(:oid AS uuid)"),
                        {"oid": order_id},
                    )
                    await ledger("admin_adjustment", "-5", None, "n")
                elif kind == "overrefund":
                    await ledger("payment_credit", "200", order_id, "c")
                    await ledger("refund_debit", "-250", order_id, "r")
                elif kind != "uncredited":
                    raise AssertionError(f"unknown hazard {kind}")

                row = (
                    await conn.execute(text(statements[label]))
                ).mappings().one()
                self.assertIn("verdict", row.keys())
                return row["verdict"]
            finally:
                await trans.rollback()

    async def test_hazard_uncredited_topup_is_critical(self):
        verdict = await self._verdict_with_hazard(
            "uncredited", "3B_PAID_TOPUP_NOT_CREDITED"
        )
        self.assertTrue(
            verdict.startswith("CRITICAL"),
            f"3B must fire on uncredited topup, got: {verdict}",
        )

    async def test_hazard_desync_is_critical(self):
        verdict = await self._verdict_with_hazard(
            "desync", "3C_CREDIT_AMOUNT_DESYNC"
        )
        self.assertTrue(
            verdict.startswith("CRITICAL"),
            f"3C must fire on desync, got: {verdict}",
        )

    async def test_hazard_negative_position_is_critical(self):
        verdict = await self._verdict_with_hazard(
            "negative", "3E_NEGATIVE_LEDGER_POSITIONS"
        )
        self.assertTrue(
            verdict.startswith("CRITICAL"),
            f"3E must fire on negative position, got: {verdict}",
        )

    async def test_hazard_overrefund_is_critical(self):
        verdict = await self._verdict_with_hazard(
            "overrefund", "3G_REFUND_DEBIT_EXCEEDS_CREDIT"
        )
        self.assertTrue(
            verdict.startswith("CRITICAL"),
            f"3G must fire on over-refund, got: {verdict}",
        )
    async def test_financial_blocks_pass_on_clean_db(self):
        """Financial pre-flight blocks must report OK (0 violations) on a clean database."""
        statements = dict(PREFLIGHT_STATEMENTS)
        for label in (
            "3A_REFERRAL_DISCOUNT_FLOOR",
            "3B_PAID_TOPUP_NOT_CREDITED",
            "3C_CREDIT_AMOUNT_DESYNC",
            "3E_NEGATIVE_LEDGER_POSITIONS",
            "3G_REFUND_DEBIT_EXCEEDS_CREDIT",
        ):
            verdict = await self._verdict_for(statements[label])
            with self.subTest(block=label):
                self.assertEqual(verdict, "OK (0 violations)")

    async def test_watch_blocks_are_clean_on_clean_db(self):
        """WARN blocks must report OK (clean) on a clean database."""
        statements = dict(PREFLIGHT_STATEMENTS)
        for label in (
            "3D_SETTLEMENT_HELD_TOPUPS",
            "3F_ACCOUNTS_ON_HOLD",
            "4C_BROADCAST_STOPPING_ORPHANS",
        ):
            verdict = await self._verdict_for(statements[label])
            with self.subTest(block=label):
                self.assertEqual(verdict, "OK (clean)")


if __name__ == "__main__":
    unittest.main()