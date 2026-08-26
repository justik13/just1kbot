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

    async def test_handler_survives_rollback_and_answers_precisely(self):
        """Drive the real handler through the invariant-rejection branch."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch

        from database.repositories.account_ledger_repo import (
            AccountLedgerInvariantError,
        )

        callback = MagicMock()
        callback.from_user = SimpleNamespace(id=123456789)
        callback.answer = AsyncMock()

        state = MagicMock()
        state.get_data = AsyncMock(
            return_value={
                "target_telegram_id": 424242,
                "amount": 50,
                "action_type": "deduct",
                "reason": "regression",
                "adjustment_id": "deadbeef",
            }
        )
        state.clear = AsyncMock()

        async def raise_invariant(*args, **kwargs):
            raise AccountLedgerInvariantError(
                "available_balance_has_no_credit_lots"
            )

        with patch(
            "bot.handlers.admin.users.balance_routes.is_admin",
            return_value=True,
        ), patch(
            "bot.handlers.admin.users.balance_routes.create_admin_adjustment",
            side_effect=raise_invariant,
        ):
            from bot.handlers.admin.users.balance_routes import (
                apply_user_balance_change,
            )

            session = self.sessions()
            try:
                # Must NOT raise MissingGreenlet: pre-fix the except-block touched
                # user.id after rollback() expired it in async session context.
                await apply_user_balance_change(callback, state, session)
            finally:
                await session.close()

        callback.answer.assert_awaited_once()
        answer_text = callback.answer.await_args.args[0]
        self.assertIn("Недостаточно бонусных средств", answer_text)
        state.clear.assert_awaited_once()

    async def test_rollback_expires_instances_mechanics(self):
        """Prove the premise: post-rollback attribute access raises MissingGreenlet."""
        from sqlalchemy.exc import MissingGreenlet

        from database.models import User as DBUser
        from database.repositories.account_ledger_repo import (
            get_account_balance,
        )

        session = self.sessions()
        try:
            user = await session.get(DBUser, self.user_id)
            self.assertIsNotNone(user)
            await session.rollback()

            with self.assertRaises(MissingGreenlet):
                _ = user.id

            balance = await get_account_balance(
                session, user_id=self.user_id
            )
            self.assertEqual(balance.bonus_available, Decimal(0))
        finally:
            await session.close()


if __name__ == "__main__":
    unittest.main()

