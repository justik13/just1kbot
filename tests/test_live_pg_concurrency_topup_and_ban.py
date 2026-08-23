"""Real PostgreSQL integration tests for concurrent topup, cancellation, settlement, and ban flows."""

import asyncio
import os
import unittest
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import Payment, PaymentProviderOperation, User
from database.repositories.account_ledger_repo import get_account_balance
from services.account_topup import (
    cancel_all_unfinished_topups,
    create_balance_topup,
    settle_succeeded_topup,
)
from services.ban_service import BanService
from services.payment_provider_operations import ProviderOperationClaim, finalize
from services.yookassa_service import YooKassaResult

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
                self.assertIn(p.provider_status, ("not_created", "creating", "pending", "canceled"))

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
            
            # Succeeded payment must always be safely settled and credited, never corrupted by cancel
            self.assertEqual(p.provider_status, "succeeded")
            self.assertIsNotNone(p.credited_at)
            self.assertEqual(bal.available, Decimal("300.00"))

    async def test_concurrent_ban_and_create_topup(self):
        """Test concurrent ban execution vs topup creation."""
        telegram_id = 999003
        async with self.session_factory() as session:
            user = User(telegram_id=telegram_id)
            session.add(user)
            await session.commit()
            user_id = user.id

        barrier = asyncio.Event()

        async def worker_ban():
            await barrier.wait()
            async with self.session_factory() as session:
                async with session.begin():
                    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
                    u = await session.get(User, user_id)
                    return await BanService._ban_user(
                        session=session,
                        admin_id=1,
                        user=u,
                        telegram_id=telegram_id,
                    )

        async def worker_topup():
            await barrier.wait()
            async with self.session_factory() as session:
                async with session.begin():
                    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
                    try:
                        return await create_balance_topup(
                            session=session,
                            user_id=user_id,
                            amount=Decimal("500"),
                            bot_username="testbot",
                        )
                    except Exception as e:
                        return e

        t1 = asyncio.create_task(worker_ban())
        t2 = asyncio.create_task(worker_topup())
        barrier.set()

        results = await asyncio.gather(t1, t2, return_exceptions=True)
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

        barrier = asyncio.Event()

        async def worker_topup_1():
            await barrier.wait()
            async with self.session_factory() as session:
                async with session.begin():
                    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
                    return await create_balance_topup(
                        session=session,
                        user_id=user_id,
                        amount=Decimal("150"),
                        bot_username="testbot",
                    )

        async def worker_topup_2():
            await barrier.wait()
            async with self.session_factory() as session:
                async with session.begin():
                    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
                    return await create_balance_topup(
                        session=session,
                        user_id=user_id,
                        amount=Decimal("200"),
                        bot_username="testbot",
                    )

        t1 = asyncio.create_task(worker_topup_1())
        t2 = asyncio.create_task(worker_topup_2())
        barrier.set()

        results = await asyncio.gather(t1, t2, return_exceptions=True)
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

        barrier = asyncio.Event()

        async def worker_ban():
            await barrier.wait()
            async with self.session_factory() as session:
                async with session.begin():
                    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
                    u = await session.get(User, user_id)
                    return await BanService._ban_user(
                        session=session,
                        admin_id=1,
                        user=u,
                        telegram_id=telegram_id,
                    )

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

        t1 = asyncio.create_task(worker_ban())
        t2 = asyncio.create_task(worker_settle())
        barrier.set()

        results = await asyncio.gather(t1, t2, return_exceptions=True)
        for r in results:
            self.assertNotIsInstance(r, Exception, f"Concurrent ban/settle failed: {r}")

        async with self.session_factory() as session:
            u = await session.get(User, user_id)
            self.assertTrue(u.is_banned)
            p = await session.get(Payment, payment_id)
            bal = await get_account_balance(session, user_id=user_id)
            # Succeeded payment must always settle and credit funds safely even when banned
            self.assertEqual(p.provider_status, "succeeded")
            self.assertIsNotNone(p.credited_at)
            self.assertEqual(bal.available, Decimal("400.00"))

    async def test_ban_and_create_interleaving_serializes_and_leaves_no_active_checkout(self):
        """Verify that under unified advisory locking, no active checkout can survive user ban."""
        telegram_id = 999006
        async with self.session_factory() as session:
            user = User(telegram_id=telegram_id)
            session.add(user)
            await session.commit()
            user_id = user.id

        barrier = asyncio.Event()

        async def worker_ban():
            await barrier.wait()
            async with self.session_factory() as session:
                async with session.begin():
                    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
                    u = await session.get(User, user_id)
                    return await BanService._ban_user(
                        session=session,
                        admin_id=1,
                        user=u,
                        telegram_id=telegram_id,
                    )

        async def worker_create():
            await barrier.wait()
            async with self.session_factory() as session:
                async with session.begin():
                    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
                    try:
                        return await create_balance_topup(
                            session=session,
                            user_id=user_id,
                            amount=Decimal("150"),
                            bot_username="testbot",
                        )
                    except Exception as e:
                        return e

        t1 = asyncio.create_task(worker_ban())
        t2 = asyncio.create_task(worker_create())
        barrier.set()

        await asyncio.gather(t1, t2)

        async with self.session_factory() as session:
            u = await session.get(User, user_id)
            self.assertTrue(u.is_banned)
            payments = (
                await session.scalars(
                    select(Payment).where(Payment.user_id == user_id)
                )
            ).all()
            for p in payments:
                if p.credited_at is None:
                    self.assertEqual(p.checkout_status, "abandoned", f"Payment {p.id} was not abandoned on ban!")
                    self.assertFalse(p.ui_visible, f"Payment {p.id} remained ui_visible on ban!")

    async def test_concurrent_finalize_and_cancel_unfinished(self):
        """Test concurrent provider finalize and cancel_all_unfinished_topups without deadlock."""
        telegram_id = 999009
        async with self.session_factory() as session:
            user = User(telegram_id=telegram_id)
            session.add(user)
            await session.flush()
            payment = Payment(
                user_id=user.id,
                amount=Decimal("300"),
                currency="RUB",
                public_order_id=str(uuid.uuid4()),
                provider_idempotency_key=str(uuid.uuid4()),
                provider_status="pending",
                external_id="yoo_test_fin_1",
            )
            session.add(payment)
            await session.flush()
            op = PaymentProviderOperation(
                payment_id=payment.id,
                operation_type="reconcile_payment",
                status="processing",
                idempotency_key=f"op_{uuid.uuid4().hex}",
                payload={},
                locked_by="worker_1",
                locked_at=datetime.now(timezone.utc),
                attempts=1,
            )
            session.add(op)
            await session.commit()
            user_id = user.id
            payment_id = payment.id
            op_id = op.id

        claim = ProviderOperationClaim(
            operation_id=op_id,
            payment_id=payment_id,
            operation_type="reconcile_payment",
            payload={},
            idempotency_key=f"op_{uuid.uuid4().hex}",
            worker_id="worker_1",
            attempt_number=1,
            external_id="yoo_test_fin_1",
            created_at=datetime.now(timezone.utc),
        )
        fake_result = YooKassaResult(
            True,
            value={
                "id": "yoo_test_fin_1",
                "status": "succeeded",
                "paid": True,
                "amount": {"value": "300.00", "currency": "RUB"},
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "metadata": {
                    "order_id": payment.public_order_id,
                    "local_payment_id": str(payment.id),
                },
            },
        )

        barrier = asyncio.Event()

        async def worker_finalize():
            await barrier.wait()
            async with self.session_factory() as session:
                async with session.begin():
                    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
                    return await finalize(session, claim, fake_result)

        async def worker_cancel():
            await barrier.wait()
            async with self.session_factory() as session:
                async with session.begin():
                    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
                    return await cancel_all_unfinished_topups(session=session, user_id=user_id)

        t1 = asyncio.create_task(worker_finalize())
        t2 = asyncio.create_task(worker_cancel())
        barrier.set()

        results = await asyncio.gather(t1, t2, return_exceptions=True)
        for r in results:
            self.assertNotIsInstance(r, Exception, f"Concurrent finalize vs cancel failed with exception: {r}")

        async with self.session_factory() as session:
            p = await session.get(Payment, payment_id)
            self.assertEqual(p.provider_status, "succeeded")
            bal = await get_account_balance(session, user_id=user_id)
            self.assertEqual(bal.accounting_position, Decimal("300.00"))

    async def test_concurrent_finalize_and_ban_user(self):
        """Test concurrent provider finalize and BanService._ban_user without deadlock."""
        telegram_id = 999010
        async with self.session_factory() as session:
            user = User(telegram_id=telegram_id)
            session.add(user)
            await session.flush()
            payment = Payment(
                user_id=user.id,
                amount=Decimal("500"),
                currency="RUB",
                public_order_id=str(uuid.uuid4()),
                provider_idempotency_key=str(uuid.uuid4()),
                provider_status="pending",
                external_id="yoo_test_fin_ban_1",
            )
            session.add(payment)
            await session.flush()
            op = PaymentProviderOperation(
                payment_id=payment.id,
                operation_type="reconcile_payment",
                status="processing",
                idempotency_key=f"op_{uuid.uuid4().hex}",
                payload={},
                locked_by="worker_1",
                locked_at=datetime.now(timezone.utc),
                attempts=1,
            )
            session.add(op)
            await session.commit()
            user_id = user.id
            payment_id = payment.id
            op_id = op.id

        claim = ProviderOperationClaim(
            operation_id=op_id,
            payment_id=payment_id,
            operation_type="reconcile_payment",
            payload={},
            idempotency_key=f"op_{uuid.uuid4().hex}",
            worker_id="worker_1",
            attempt_number=1,
            external_id="yoo_test_fin_ban_1",
            created_at=datetime.now(timezone.utc),
        )
        fake_result = YooKassaResult(
            True,
            value={
                "id": "yoo_test_fin_ban_1",
                "status": "succeeded",
                "paid": True,
                "amount": {"value": "500.00", "currency": "RUB"},
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "metadata": {
                    "order_id": payment.public_order_id,
                    "local_payment_id": str(payment.id),
                },
            },
        )

        barrier = asyncio.Event()

        async def worker_finalize():
            await barrier.wait()
            async with self.session_factory() as session:
                async with session.begin():
                    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
                    return await finalize(session, claim, fake_result)

        async def worker_ban():
            await barrier.wait()
            async with self.session_factory() as session:
                async with session.begin():
                    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
                    u = await session.get(User, user_id)
                    return await BanService._ban_user(
                        session=session,
                        admin_id=1,
                        user=u,
                        telegram_id=telegram_id,
                    )

        t1 = asyncio.create_task(worker_finalize())
        t2 = asyncio.create_task(worker_ban())
        barrier.set()

        results = await asyncio.gather(t1, t2, return_exceptions=True)
        for r in results:
            self.assertNotIsInstance(r, Exception, f"Concurrent finalize vs ban failed with exception: {r}")

        async with self.session_factory() as session:
            u = await session.get(User, user_id)
            self.assertTrue(u.is_banned)


if __name__ == "__main__":
    unittest.main()
