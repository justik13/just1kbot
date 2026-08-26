"""Regression: admin balance apply must survive the under-lock rejection.

The AccountLedgerInvariantError path performs session.rollback(); touching
the expired ORM user afterwards raises MissingGreenlet in async context.
The handler therefore captures the id before the call — this test pins the
rollback + fresh-balance-read sequence against a real PostgreSQL.
"""
import os
import unittest
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

try:
    from tests.db_utils import TRUNCATE_SQL
except ImportError:  # direct unittest discover run
    from db_utils import TRUNCATE_SQL

DB = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class AdminBalanceApplyRollbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(DB)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions.begin() as session:
            await session.execute(text(TRUNCATE_SQL))
            from database.models import User as DBUser

            self.user = DBUser(telegram_id=424242)
            session.add(self.user)
            await session.flush()
            self.user_id = self.user.id
        from config.settings import get_settings

        get_settings.cache_clear()

    async def asyncTearDown(self):
        await self.engine.dispose()
        from config.settings import get_settings

        get_settings.cache_clear()

    async def test_rollback_path_reads_fresh_balance_without_greenlet_error(self):
        from database.repositories.account_ledger_repo import (
            AccountLedgerInvariantError,
            create_admin_adjustment,
        )

        # A negative adjustment exceeding available funds must raise the
        # invariant inside create_admin_adjustment (under advisory lock).
        with self.assertRaises(AccountLedgerInvariantError):
            async with self.sessions.begin() as session:
                await create_admin_adjustment(
                    session,
                    user_id=self.user_id,
                    signed_amount=Decimal(-100),
                    idempotency_key="admin_adj:test-overdraw",
                    metadata={"reason": "test"},
                )

        # Reproduce the handler's except-branch: rollback expires instances,
        # then a fresh balance read must work via explicit id (no greenlet).
        session = self.sessions()
        try:
            await session.rollback()
            from database.repositories.account_ledger_repo import (
                get_account_balance,
            )

            balance = await get_account_balance(
                session, user_id=self.user_id
            )
            self.assertEqual(balance.bonus_available, Decimal(0))
        finally:
            await session.close()


if __name__ == "__main__":
    unittest.main()
