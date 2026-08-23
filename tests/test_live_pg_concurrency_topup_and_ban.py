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
from services.payment_provider_operations import (
    ProviderOperationClaim,
    finalize,
    recover_stale,
)
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
            self.assertEqual(p.provider_status, "succeeded")
            if p.credited_at is not None:
                bal = await get_account_balance(session, user_id=user_id)
                self.assertEqual(bal.available, Decimal("400.00"))
            else:
                self.assertEqual(p.fulfillment_status, "manual_review")
                self.assertEqual(p.reconciliation_status, "manual_review")

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

    async def test_ban_user_with_succeeded_uncredited_payment_sets_manual_review(self):
        """Banning user when payment is succeeded at provider marks fulfillment as manual_review."""
        telegram_id = 999011
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
                provider_status="succeeded",
                provider_confirmed_at=datetime.now(timezone.utc),
                fulfillment_status="not_ready",
                credited_at=None,
                checkout_status="active",
                ui_visible=True,
            )
            session.add(payment)
            await session.commit()
            user_id = user.id
            payment_id = payment.id

        async with self.session_factory() as session:
            async with session.begin():
                u = await session.get(User, user_id)
                await BanService._ban_user(session, admin_id=1, user=u, telegram_id=telegram_id)

        async with self.session_factory() as session:
            p = await session.get(Payment, payment_id)
            self.assertEqual(p.checkout_status, "abandoned")
            self.assertFalse(p.ui_visible)
            self.assertEqual(p.fulfillment_status, "manual_review")
            self.assertEqual(p.reconciliation_status, "manual_review")
            self.assertEqual(p.manual_review_reason, "user_banned_before_credit")

    async def test_concurrent_recover_stale_and_finalize(self):
        """Test concurrent recover_stale and finalize without deadlock."""
        telegram_id = 999012
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
                external_id="yoo_test_stale_1",
            )
            session.add(payment)
            await session.flush()
            op = PaymentProviderOperation(
                payment_id=payment.id,
                operation_type="reconcile_payment",
                status="processing",
                idempotency_key=f"op_{uuid.uuid4().hex}",
                payload={},
                locked_by="worker_stale",
                locked_at=datetime.fromtimestamp(1000, tz=timezone.utc),
                attempts=1,
            )
            session.add(op)
            await session.commit()
            payment_id = payment.id
            op_id = op.id

        claim = ProviderOperationClaim(
            operation_id=op_id,
            payment_id=payment_id,
            operation_type="reconcile_payment",
            payload={},
            idempotency_key=f"op_{uuid.uuid4().hex}",
            worker_id="worker_stale",
            attempt_number=1,
            external_id="yoo_test_stale_1",
            created_at=datetime.now(timezone.utc),
        )
        fake_result = YooKassaResult(
            True,
            value={
                "id": "yoo_test_stale_1",
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

        async def worker_recover():
            await barrier.wait()
            async with self.session_factory() as session:
                async with session.begin():
                    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
                    return await recover_stale(session, lease_seconds=10)

        t1 = asyncio.create_task(worker_finalize())
        t2 = asyncio.create_task(worker_recover())
        barrier.set()

        results = await asyncio.gather(t1, t2, return_exceptions=True)
        for r in results:
            self.assertNotIsInstance(r, BaseException, f"Concurrent task failed with exception: {r}")

        async with self.session_factory() as session:
            p = await session.get(Payment, payment_id)
            op = await session.get(PaymentProviderOperation, op_id)
            self.assertEqual(p.provider_status, "succeeded")
            self.assertIn(op.status, ("succeeded", "dead", "retry"))

    async def test_concurrent_multi_user_recover_stale_no_deadlock(self):
        """Two concurrent recover_stale workers over multiple users never deadlock."""
        async with self.session_factory() as session:
            for i in range(5):
                user = User(telegram_id=999100 + i)
                session.add(user)
                await session.flush()
                payment = Payment(
                    user_id=user.id,
                    amount=Decimal("100"),
                    currency="RUB",
                    public_order_id=str(uuid.uuid4()),
                    provider_idempotency_key=str(uuid.uuid4()),
                    provider_status="pending",
                    external_id=f"yoo_multi_stale_{i}",
                )
                session.add(payment)
                await session.flush()
                op = PaymentProviderOperation(
                    payment_id=payment.id,
                    operation_type="reconcile_payment",
                    status="processing",
                    idempotency_key=f"op_{uuid.uuid4().hex}",
                    payload={},
                    locked_by=f"worker_stale_{i}",
                    locked_at=datetime.fromtimestamp(1000, tz=timezone.utc),
                    attempts=1,
                )
                session.add(op)
            await session.commit()

        barrier = asyncio.Event()

        async def worker_recover():
            await barrier.wait()
            async with self.session_factory() as session:
                async with session.begin():
                    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
                    return await recover_stale(session, lease_seconds=10)

        t1 = asyncio.create_task(worker_recover())
        t2 = asyncio.create_task(worker_recover())
        barrier.set()

        results = await asyncio.gather(t1, t2, return_exceptions=True)
        for r in results:
            self.assertNotIsInstance(r, Exception, f"Concurrent multi-user recovery failed: {r}")

    async def test_webhook_inbox_finalize_and_ban_user_concurrent(self):
        """Webhook inbox finalize and ban_user concurrently execute without deadlock."""
        from services.workers.webhook_inbox import finalize as webhook_finalize, InboxClaim
        from database.models import WebhookInbox
        from services.yookassa_service import YooKassaResult

        async with self.session_factory() as session:
            user = User(telegram_id=999888)
            session.add(user)
            await session.flush()
            payment = Payment(
                user_id=user.id,
                amount=Decimal("100"),
                currency="RUB",
                public_order_id=str(uuid.uuid4()),
                provider_idempotency_key=str(uuid.uuid4()),
                provider_status="pending",
                external_id="yoo_webhook_test_1",
            )
            session.add(payment)
            await session.flush()
            inbox = WebhookInbox(
                provider="yookassa",
                event_key=f"ev_{uuid.uuid4().hex}",
                event_type="payment.succeeded",
                provider_object_id="yoo_webhook_test_1",
                payment_external_id="yoo_webhook_test_1",
                public_order_id=payment.public_order_id,
                payload={"object": {"id": "yoo_webhook_test_1", "status": "succeeded"}},
                status="processing",
                locked_by="worker_webhook",
                locked_at=datetime.now(timezone.utc),
                attempts=1,
            )
            session.add(inbox)
            await session.commit()
            inbox_id = inbox.id
            user_id = user.id

        claim = InboxClaim(
            inbox_id,
            "worker_webhook",
            1,
            "payment.succeeded",
            "yoo_webhook_test_1",
            payment.public_order_id,
            {"object": {"id": "yoo_webhook_test_1", "status": "succeeded"}},
            inbox.event_key,
        )
        fake_result = YooKassaResult(
            True,
            value={
                "id": "yoo_webhook_test_1",
                "status": "succeeded",
                "paid": True,
                "amount": {"value": "100.00", "currency": "RUB"},
                "captured_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        barrier = asyncio.Event()

        async def run_webhook_finalize():
            await barrier.wait()
            async with self.session_factory() as session:
                async with session.begin():
                    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
                    return await webhook_finalize(session, claim, fake_result)

        async def run_ban():
            await barrier.wait()
            async with self.session_factory() as session:
                async with session.begin():
                    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
                    u = await session.get(User, user_id)
                    return await BanService._ban_user(session, 1, u, 999888)

        t1 = asyncio.create_task(run_webhook_finalize())
        t2 = asyncio.create_task(run_ban())
        barrier.set()

        results = await asyncio.gather(t1, t2, return_exceptions=True)
        for r in results:
            self.assertNotIsInstance(r, BaseException, f"Concurrent task failed with exception: {r}")

        async with self.session_factory() as session:
            u = await session.get(User, user_id)
            self.assertTrue(u.is_banned)
            p = await session.scalar(select(Payment).where(Payment.id == payment.id))
            self.assertEqual(p.provider_status, "succeeded")

    async def test_settle_succeeded_topup_blocks_banned_user(self):
        """Settlement on a banned user sets manual_review with reason 'user_banned' and does not credit."""
        async with self.session_factory() as session:
            user = User(telegram_id=999777, is_banned=True)
            session.add(user)
            await session.flush()
            payment = Payment(
                user_id=user.id,
                amount=Decimal("200"),
                currency="RUB",
                public_order_id=str(uuid.uuid4()),
                provider_idempotency_key=str(uuid.uuid4()),
                provider_status="succeeded",
                provider_confirmed_at=datetime.now(timezone.utc),
                external_id="yoo_banned_test_1",
            )
            session.add(payment)
            await session.commit()
            payment_id = payment.id

        async with self.session_factory() as session:
            async with session.begin():
                p = await session.get(Payment, payment_id)
                success, snapshot = await settle_succeeded_topup(session, payment=p, source="test")
                self.assertFalse(success)
                self.assertEqual(p.fulfillment_status, "manual_review")
                self.assertEqual(p.reconciliation_status, "manual_review")
                self.assertEqual(p.manual_review_reason, "user_banned")
                self.assertIsNone(p.credited_at)


if __name__ == "__main__":
    unittest.main()
