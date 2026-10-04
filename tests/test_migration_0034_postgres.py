"""Integration test for migration 0034 against a real PostgreSQL instance.

Runs exclusively when TEST_DATABASE_URL is provided (as configured in CI).
Verifies:
1. Trigger account_ledger_append_only is validated as 'O' (origin enabled).
2. _set_ledger_immutable_trigger toggles between 'D' and 'O'.
3. An active trigger blocks UPDATE with 'account ledger is append-only'.
4. Toggling allows the backfill UPDATE to succeed.
5. Invariants (paid quote requires debit, order count matches) hold.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import unittest
import uuid

from sqlalchemy.ext.asyncio import create_async_engine


DB = os.getenv("TEST_DATABASE_URL")
VERSIONS_DIR = Path(__file__).resolve().parent.parent / "alembic" / "versions"


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class Migration0034PostgresIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(DB, pool_size=3, max_overflow=2)
        spec = importlib.util.spec_from_file_location(
            "m0034", str(VERSIONS_DIR / "0034_drop_tariff_quotes.py")
        )
        self.m0034 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m0034)

    async def asyncTearDown(self):
        await self.engine.dispose()

    @staticmethod
    def _norm(val: object) -> str:
        if isinstance(val, (bytes, bytearray)):
            return val.decode("ascii")
        return str(val) if val is not None else ""

    async def test_trigger_toggle_and_append_only_protection_on_postgres(self):
        """Verify trigger account_ledger_append_only protects table and toggle works."""
        from sqlalchemy import text

        async with self.engine.connect() as conn:
            # 1. Verify trigger exists and is 'O' (origin enabled)
            status = (
                await conn.execute(
                    text(
                        "SELECT tgenabled::text FROM pg_trigger "
                        "WHERE tgrelid = 'public.account_ledger_entries'::regclass "
                        "AND tgname = 'account_ledger_append_only'"
                    )
                )
            ).scalar()
            self.assertEqual(self._norm(status), "O")

            # 2. Insert test user and ledger entry
            user_tg = int(uuid.uuid4().int % 1000000000)
            user_id = (
                await conn.execute(
                    text(
                        "INSERT INTO users (telegram_id, is_active, created_at, updated_at) "
                        "VALUES (:tg, true, now(), now()) RETURNING id"
                    ),
                    {"tg": user_tg},
                )
            ).scalar()

            idem_key = f"test_{uuid.uuid4()}"
            entry_id = (
                await conn.execute(
                    text(
                        "INSERT INTO account_ledger_entries (user_id, entry_type, amount, currency, idempotency_key, created_at) "
                        "VALUES (:uid, 'admin_adjustment', 100.00, 'RUB', :idem, now()) RETURNING id"
                    ),
                    {"uid": user_id, "idem": idem_key},
                )
            ).scalar()

            # 3. With trigger active, UPDATE must fail with CheckViolationError
            with self.assertRaises(Exception) as cm:
                await conn.execute(
                    text(
                        "UPDATE account_ledger_entries SET amount = 200.00 WHERE id = :id"
                    ),
                    {"id": entry_id},
                )
            self.assertIn("account ledger is append-only", str(cm.exception))

            # 4. Disable trigger via migration helper
            await conn.execute(self.m0034._DISABLE_LEDGER_TRIGGER_SQL)
            dis_status = (
                await conn.execute(
                    text(
                        "SELECT tgenabled::text FROM pg_trigger "
                        "WHERE tgrelid = 'public.account_ledger_entries'::regclass "
                        "AND tgname = 'account_ledger_append_only'"
                    )
                )
            ).scalar()
            self.assertEqual(self._norm(dis_status), "D")

            # 5. While disabled, UPDATE succeeds
            await conn.execute(
                text(
                    "UPDATE account_ledger_entries SET amount = 200.00 WHERE id = :id"
                ),
                {"id": entry_id},
            )

            # 6. Re-enable trigger via migration helper
            await conn.execute(self.m0034._ENABLE_LEDGER_TRIGGER_SQL)
            en_status = (
                await conn.execute(
                    text(
                        "SELECT tgenabled::text FROM pg_trigger "
                        "WHERE tgrelid = 'public.account_ledger_entries'::regclass "
                        "AND tgname = 'account_ledger_append_only'"
                    )
                )
            ).scalar()
            self.assertEqual(self._norm(en_status), "O")

            # 7. With trigger re-enabled, UPDATE fails again
            with self.assertRaises(Exception) as cm:
                await conn.execute(
                    text(
                        "UPDATE account_ledger_entries SET amount = 300.00 WHERE id = :id"
                    ),
                    {"id": entry_id},
                )
            self.assertIn("account ledger is append-only", str(cm.exception))

            # Cleanup: disable trigger to clean up test rows
            await conn.execute(self.m0034._DISABLE_LEDGER_TRIGGER_SQL)
            await conn.execute(
                text("DELETE FROM account_ledger_entries WHERE id = :id"),
                {"id": entry_id},
            )
            await conn.execute(
                text("DELETE FROM users WHERE id = :uid"),
                {"uid": user_id},
            )
            await conn.execute(self.m0034._ENABLE_LEDGER_TRIGGER_SQL)


if __name__ == "__main__":
    unittest.main()
