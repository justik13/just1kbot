"""Real PostgreSQL integration tests for concurrent topup, cancellation, settlement, and ban flows."""

import asyncio
import os
import unittest
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import Payment, User
from database.repositories.account_ledger_repo import get_account_balance
from services.account_topup import (
    cancel_all_unfinished_topups,
    create_balance_topup,
    settle_succeeded_topup,
)
from services.ban_service import BanService

DB = os.getenv("TEST_DATABASE_URL")

TRUNCATE_SQL = (
    "TRUNCATE provider_refund_operations, webhook_inbox, payment_refunds, "
    "account_balance_reservations, "
    "account_ledger_allocations, account_ledger_entries, "
    "payment_events, audit_logs, payments, users, system_settings, payment_disputes RESTART IDENTITY CASCADE"
)


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class TestLivePgConcurrencyTopupAndBan(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            self.engine = create_async_engine(DB, pool_size=10, max_overflow=5)
            self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
            async with self.engine.begin() as conn:
                await conn.execute(text(TRUNCATE_SQL))
        except Exception as e:
            self.skipTest(f"PostgreSQL database not accessible at {DB}: {e}")

    async def asyncTearDown(self):
        try:
            async with self.engine.begin() as conn:
                await conn.execute(text(TRUNCATE_SQL))
            await self.engine.dispose()
        except Exception:
            pass

    async def test_concurrent_create_topup_and_cancel_unfinished(self):
        """Test concurrent topup creation and cancel_all_unfinished_topups against real PostgreSQL."""
        telegram_id = 999001
        async with self.session_factory() as session:
            user = User(telegram_id=telegram_id)
            session.add(user)
            await session.commit()
            user_id = user.id

        barrier = asyncio.Event()

        async def worker_create():
            await barrier.wait()
            async with self.session_factory() as session:
                async with session.begin():
                    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
                    return await create_balance_topup(
                        session=session,
                        user_id=user_id,
                        amount=Decimal("250"),
                        bot_username="testbot",
                    )

        async def worker_cancel():
            await barrier.wait()
            async with self.session_factory() as session:
                async with session.begin():
                    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
                    return await cancel_all_unfinished_topups(
                        session=session,
                        user_id=user_id,
                    )

        t1 = asyncio.create_task(worker_create())
        t2 = asyncio.create_task(worker_cancel())
        barrier.set()

        results = await asyncio.gather(t1, t2, return_exceptions=True)
        for r in results:
            self.assertNotIsInstance(r, Exception, f"Concurrent task failed with exception: {r}")

        async with self.session_factory() as session:
            payments = (
                await session.scalars(
                    select(Payment).where(Payment.user_id == user_id)
                )
            ).all()
            self.assertGreaterEqual(len(payments), 1)
            for p in payments:
                self.assertIn(p.provider_status, ("creating", "pending", "canceled"))

    async def test_concurrent_settle_succeeded_and_cancel_unfinished(self):
        """Test concurrent settlement of succeeded payment vs cancel_all_unfinished_topups."""
        telegram_id = 999002
        async with self.session_factory() as session:
            user = User(telegram_id=telegram_id)
            session.add(user)
            await session.flush()
            user_id = user.id

            payment = Payment(
                user_id=user_id,
                amount=Decimal("300.00"),
                currency="RUB",
                public_order_id=str(uuid.uuid4()),
                external_id=str(uuid.uuid4()),
                provider_idempotency_key=str(uuid.uuid4()),
                provider_status="succeeded",
                provider_confirmed_at=datetime.now(timezone.utc),
                fulfillment_status="not_ready",
                credited_at=None,
                reconciliation_status="ok",
                checkout_status="active",
                ui_visible=True,
            )
            session.add(payment)
            await session.commit()
            payment_id = payment.id

        barrier = asyncio.Event()

        async def worker_settle():
            await barrier.wait()
            async with self.session_factory() as session:
                async with session.begin():
                    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
                    p = await session.get(Payment, payment_id)
                    return await settle_succeeded_topup(
                        session=session,
                        payment=p,
                        source="webhook_test",
                    )

        async def worker_cancel():
            await barrier.wait()
            async with self.session_factory() as session:
                async with session.begin():
                    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
                    return await cancel_all_unfinished_topups(
                        session=session,
                        user_id=user_id,
                    )

        t1 = asyncio.create_task(worker_settle())
        t2 = asyncio.create_task(worker_cancel())
        barrier.set()

        results = await asyncio.gather(t1, t2, return_exceptions=True)
        for r in results:
            self.assertNotIsInstance(r, Exception, f"Concurrent task failed with exception: {r}")

        async with self.session_factory() as session:
            p = await session.get(Payment, payment_id)
            bal = await get_account_balance(session, user_id=user_id)
            
            # Deterministic state:
            # Either settled & credited (balance 300 RUB), or canceled before credit (balance 0 RUB).
            if p.credited_at is not None:
                self.assertEqual(p.provider_status, "succeeded")
                self.assertEqual(bal.available, Decimal("300.00"))
            else:
                self.assertEqual(p.provider_status, "canceled")
                self.assertEqual(bal.available, Decimal("0.00"))

    async def test_concurrent_ban_and_create_topup(self):
        """Test concurrent ban execution vs topup creation."""
        telegram_id = 999003
        async with self.session_factory() as session:
            user = User(telegram_id=telegram_id)
            session.add(user)
            await session.commit()
            user_id = user.id

        async def worker_ban():
            async with self.session_factory() as session:
                async with session.begin():
                    u = await session.get(User, user_id)
                    return await BanService._ban_user(
                        session=session,
                        admin_id=1,
                        user=u,
                        telegram_id=telegram_id,
                    )

        async def worker_topup():
            async with self.session_factory() as session:
                async with session.begin():
                    try:
                        return await create_balance_topup(
                            session=session,
                            user_id=user_id,
                            amount=Decimal("500"),
                            bot_username="testbot",
                        )
                    except Exception as e:
                        return e

        results = await asyncio.gather(worker_ban(), worker_topup(), return_exceptions=True)
        for r in results:
            if isinstance(r, Exception) and not isinstance(r, RuntimeError):
                self.fail(f"Unexpected non-domain exception during concurrent ban/topup: {r}")

        async with self.session_factory() as session:
            u = await session.get(User, user_id)
            self.assertTrue(u.is_banned)
            payments = (
                await session.scalars(select(Payment).where(Payment.user_id == user_id))
            ).all()
            for p in payments:
                if p.credited_at is None:
                    # Uncredited payments under ban must not remain actively visible
                    self.assertIn(p.checkout_status, ("abandoned", "canceled"))

    async def test_concurrent_two_create_topups_for_same_user(self):
        """Test two concurrent create_balance_topup requests for the same user."""
        telegram_id = 999004
        async with self.session_factory() as session:
            user = User(telegram_id=telegram_id)
            session.add(user)
            await session.commit()
            user_id = user.id

        async def worker_topup_1():
            async with self.session_factory() as session:
                async with session.begin():
                    return await create_balance_topup(
                        session=session,
                        user_id=user_id,
                        amount=Decimal("150"),
                        bot_username="testbot",
                    )

        async def worker_topup_2():
            async with self.session_factory() as session:
                async with session.begin():
                    return await create_balance_topup(
                        session=session,
                        user_id=user_id,
                        amount=Decimal("200"),
                        bot_username="testbot",
                    )

        results = await asyncio.gather(worker_topup_1(), worker_topup_2(), return_exceptions=True)
        for r in results:
            self.assertNotIsInstance(r, Exception, f"Concurrent create_topup failed: {r}")

        async with self.session_factory() as session:
            payments = (
                await session.scalars(select(Payment).where(Payment.user_id == user_id).order_by(Payment.id))
            ).all()
            # Under single-flight deduplication, concurrent calls safely deduplicate to 1 or 2 rows max
            self.assertLessEqual(len(payments), 2)
            visible_count = sum(1 for p in payments if p.ui_visible and p.checkout_status == "active")
            self.assertEqual(visible_count, 1)

    async def test_concurrent_ban_and_settle_succeeded(self):
        """Test concurrent ban vs webhook settlement of a succeeded payment."""
        telegram_id = 999005
        async with self.session_factory() as session:
            user = User(telegram_id=telegram_id)
            session.add(user)
            await session.flush()
            user_id = user.id

            payment = Payment(
                user_id=user_id,
                amount=Decimal("400.00"),
                currency="RUB",
                public_order_id=str(uuid.uuid4()),
                external_id=str(uuid.uuid4()),
                provider_idempotency_key=str(uuid.uuid4()),
                provider_status="succeeded",
                provider_confirmed_at=datetime.now(timezone.utc),
                fulfillment_status="not_ready",
                credited_at=None,
                reconciliation_status="ok",
                checkout_status="active",
                ui_visible=True,
            )
            session.add(payment)
            await session.commit()
            payment_id = payment.id

        async def worker_ban():
            async with self.session_factory() as session:
                async with session.begin():
                    u = await session.get(User, user_id)
                    return await BanService._ban_user(
                        session=session,
                        admin_id=1,
                        user=u,
                        telegram_id=telegram_id,
                    )

        async def worker_settle():
            async with self.session_factory() as session:
                async with session.begin():
                    p = await session.get(Payment, payment_id)
                    return await settle_succeeded_topup(
                        session=session,
                        payment=p,
                        source="webhook_test",
                    )

        results = await asyncio.gather(worker_ban(), worker_settle(), return_exceptions=True)
        for r in results:
            self.assertNotIsInstance(r, Exception, f"Concurrent ban/settle failed: {r}")

        async with self.session_factory() as session:
            u = await session.get(User, user_id)
            self.assertTrue(u.is_banned)


if __name__ == "__main__":
    unittest.main()
