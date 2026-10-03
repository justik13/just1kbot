"""PostgreSQL tests: order debits attribute spending FIFO like quote debits.

Bonus lots (admin_adjustment) are consumed before real lots
(payment_credit), so balance buckets stay exact for wallet orders.
Runs exclusively against TEST_DATABASE_URL.
"""

from __future__ import annotations

import os
import unittest
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import (
    AccountLedgerAllocation,
    AccountLedgerEntry,
    Order,
    User,
)
try:
    from tests.db_utils import TRUNCATE_SQL
except ImportError:
    from db_utils import TRUNCATE_SQL


DB = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class OrderDebitAllocationPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from unittest.mock import patch

        self.env_patcher = patch.dict(
            os.environ,
            {
                "BOT_TOKEN": "123:test",
                "REDIS_URL": "redis://localhost:6379/1",
                "REDIS_PASSWORD": "test",
                "ADMIN_IDS": "[123456789]",
                "SUPPORT_USERNAME": "test_support",
                "DOMAIN": "test.domain",
                "SSL_EMAIL": "test@domain.com",
                "YOOKASSA_SHOP_ID": "123456",
                "YOOKASSA_SECRET_KEY": "test_secret",
                "YOOKASSA_RETURN_URL": "https://t.me/{bot_username}",
                "YOOKASSA_WEBHOOK_PORT": "8080",
                "DB_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
                "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
            },
        )
        self.env_patcher.start()

        from config.settings import get_settings

        get_settings.cache_clear()

        self.engine = create_async_engine(DB, pool_size=5, max_overflow=5)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        from sqlalchemy import text

        async with self.sessions.begin() as session:
            await session.execute(text(TRUNCATE_SQL))
            self.user = User(telegram_id=int(uuid.uuid4().int % 1000000000))
            session.add(self.user)
            await session.flush()
            self.user_id = self.user.id

    async def asyncTearDown(self):
        from sqlalchemy import text
        try:
            try:
                from tests.db_utils import TRUNCATE_SQL
            except ImportError:
                from db_utils import TRUNCATE_SQL
            async with self.sessions.begin() as session:
                await session.execute(text(TRUNCATE_SQL))
        except Exception:
            pass
        finally:
            self.env_patcher.stop()
            await self.engine.dispose()

    async def _make_order(
        self,
        session,
        *,
        service_type="awg",
        amount=Decimal("0"),
        payment_method="wallet",
        status="paid",
    ):
        order = Order(
            user_id=self.user_id,
            service_type=service_type,
            amount_rub=amount,
            payment_method=payment_method,
            status=status,
        )
        session.add(order)
        await session.flush()
        return order

    async def _seed_credits(self, session, *, real=Decimal("100"), bonus=Decimal("50")):
        from database.repositories.account_ledger_repo import (
            create_admin_adjustment,
            create_order_credit,
        )

        topup = await self._make_order(
            session, service_type="topup", amount=real, payment_method="yookassa"
        )
        await create_order_credit(
            session,
            user_id=self.user_id,
            amount_rub=real,
            order_id=topup.id,
            metadata={},
        )
        await create_admin_adjustment(
            session,
            user_id=self.user_id,
            signed_amount=bonus,
            idempotency_key=f"bonus:{self.user_id}:{uuid.uuid4().hex}",
            metadata={"reason": "test_bonus"},
        )

    async def _split_by_type(self, session, debit_id):
        rows = (
            await session.execute(
                select(
                    AccountLedgerAllocation.credit_entry_id,
                    AccountLedgerAllocation.amount,
                ).where(AccountLedgerAllocation.debit_entry_id == debit_id)
            )
        ).all()
        totals: dict[str, Decimal] = {}
        for credit_id, amount in rows:
            entry_type = await session.scalar(
                select(AccountLedgerEntry.entry_type).where(
                    AccountLedgerEntry.id == credit_id
                )
            )
            totals[entry_type] = totals.get(entry_type, Decimal(0)) + amount
        return totals, len(rows)

    async def test_order_debit_allocates_bonus_first(self):
        from database.repositories.account_ledger_repo import (
            create_order_debit,
            get_account_balance,
        )

        async with self.sessions.begin() as session:
            await self._seed_credits(session)
            purchase = await self._make_order(
                session, service_type="awg", amount=Decimal("120")
            )
            order_id = purchase.id
            debit, created = await create_order_debit(
                session,
                user_id=self.user_id,
                amount_rub=Decimal("120"),
                order_id=order_id,
                metadata={},
            )
            self.assertTrue(created)

        async with self.sessions() as session:
            totals, count = await self._split_by_type(session, debit.id)
            self.assertEqual(count, 2)
            self.assertEqual(totals.get("admin_adjustment"), Decimal("50"))
            self.assertEqual(totals.get("payment_credit"), Decimal("70"))

            bal = await get_account_balance(session, user_id=self.user_id)
            self.assertEqual(bal.accounting_position, Decimal("30"))
            self.assertEqual(bal.available, Decimal("30"))
            self.assertEqual(bal.real_available, Decimal("30"))
            self.assertEqual(bal.bonus_available, Decimal("0"))

    async def test_order_debit_idempotent_without_reallocation(self):
        from database.repositories.account_ledger_repo import create_order_debit

        async with self.sessions.begin() as session:
            await self._seed_credits(session)
            purchase = await self._make_order(
                session, service_type="awg", amount=Decimal("120")
            )
            order_id = purchase.id
            debit, created = await create_order_debit(
                session,
                user_id=self.user_id,
                amount_rub=Decimal("120"),
                order_id=order_id,
                metadata={},
            )
            self.assertTrue(created)
            debit_id = debit.id

        async with self.sessions.begin() as session:
            debit2, created2 = await create_order_debit(
                session,
                user_id=self.user_id,
                amount_rub=Decimal("120"),
                order_id=order_id,
                metadata={},
            )
            self.assertFalse(created2)
            self.assertEqual(debit2.id, debit_id)

        async with self.sessions() as session:
            _, count = await self._split_by_type(session, debit_id)
            self.assertEqual(count, 2)

    async def test_order_debit_insufficient_lots_fails_closed(self):
        from sqlalchemy import func

        from database.repositories.account_ledger_repo import (
            AccountLedgerInvariantError,
            InsufficientAccountBalanceError,
            create_order_debit,
        )

        async with self.sessions.begin() as session:
            await self._seed_credits(
                session, real=Decimal("20"), bonus=Decimal("10")
            )
            purchase = await self._make_order(
                session, service_type="awg", amount=Decimal("1000")
            )
            order_id = purchase.id

        # The failing debit must roll back entirely: raise out of the
        # transaction block so nothing (including the debit row) commits.
        with self.assertRaises((AccountLedgerInvariantError, InsufficientAccountBalanceError)):
            async with self.sessions.begin() as session:
                await create_order_debit(
                    session,
                    user_id=self.user_id,
                    amount_rub=Decimal("1000"),
                    order_id=order_id,
                    metadata={},
                )

        async with self.sessions() as session:
            leftover = await session.scalar(
                select(func.count(AccountLedgerEntry.id)).where(
                    AccountLedgerEntry.entry_type == "purchase_debit",
                    AccountLedgerEntry.order_id == order_id,
                )
            )
            self.assertEqual(leftover or 0, 0)


if __name__ == "__main__":
    unittest.main()
