"""PostgreSQL contracts for concurrent first-topup bonus and referral bonuses."""

import os
import unittest
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import Payment, User
from services.referral_bonus import grant_referral_bonus_for_topup
from services.workers.payments import _needs_recovery

DB = os.getenv("TEST_DATABASE_URL")

TRUNCATE_SQL = (
    "TRUNCATE provider_refund_operations, webhook_inbox, payment_refunds, "
    "account_balance_reservations, "
    "account_ledger_allocations, account_ledger_entries, "
    "payment_events, audit_logs, payments, users, system_settings, payment_disputes RESTART IDENTITY CASCADE"
)

@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class TestReferralBonusRecoveryPostgres(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(DB)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.execute(text(TRUNCATE_SQL))

    async def asyncTearDown(self):
        async with self.engine.begin() as conn:
            await conn.execute(text(TRUNCATE_SQL))
        await self.engine.dispose()

    async def test_persistent_retry_out_of_order_recovery(self):
        async with self.session_factory() as session:
            referrer = User(telegram_id=100)
            session.add(referrer)
            await session.flush()
            
            purchaser = User(telegram_id=200, referred_by=100)
            session.add(purchaser)
            await session.commit()
            
            p_id = purchaser.id

        # Setup P1 and P2
        async with self.session_factory() as session:
            p1 = Payment(
                user_id=p_id, amount=Decimal(100), currency='RUB',
                public_order_id=str(uuid.uuid4()), external_id=str(uuid.uuid4()),
                provider_idempotency_key=str(uuid.uuid4()), provider_status='succeeded',
                provider_confirmed_at=datetime.now(timezone.utc),
                fulfillment_status='succeeded', credited_at=datetime.now(timezone.utc),
                reconciliation_status='ok', checkout_status='active',
            )
            session.add(p1)
            await session.commit()
            p1_id = p1.id

            p2 = Payment(
                user_id=p_id, amount=Decimal(200), currency='RUB',
                public_order_id=str(uuid.uuid4()), external_id=str(uuid.uuid4()),
                provider_idempotency_key=str(uuid.uuid4()), provider_status='succeeded',
                provider_confirmed_at=datetime.now(timezone.utc),
                fulfillment_status='succeeded', credited_at=datetime.now(timezone.utc),
                reconciliation_status='ok', checkout_status='active',
            )
            session.add(p2)
            await session.commit()
            p2_id = p2.id

        # P2 processed BEFORE P1 recovery
        async with self.session_factory() as session:
            res2 = await grant_referral_bonus_for_topup(session, purchaser_user_id=p_id, payment_id=p2_id, topup_amount=Decimal(200))
            # simulate worker updating the flag
            p2_model = await session.get(Payment, p2_id)
            p2_model.topup_context = {**(p2_model.topup_context or {}), "referral_bonus_processed": True}
            await session.commit()
            # P2 shouldn't receive welcome bonus because P1 exists (id < P2.id)
            self.assertEqual(res2.purchaser_welcome_bonus, Decimal(0))

        # Recovery selects P1
        async with self.session_factory() as session:
            payments = (await session.scalars(select(Payment).where(Payment.id == p1_id, _needs_recovery()))).all()
            self.assertTrue(len(payments) > 0, "P1 should be selected for recovery")
            
            res1 = await grant_referral_bonus_for_topup(session, purchaser_user_id=p_id, payment_id=p1_id, topup_amount=Decimal(100))
            # simulate worker updating the flag
            p1_model = await session.get(Payment, p1_id)
            p1_model.topup_context = {**(p1_model.topup_context or {}), "referral_bonus_processed": True}
            await session.commit()
            # P1 MUST receive the welcome bonus (10% of 100 = 10) exactly once after the fix
            self.assertEqual(res1.purchaser_welcome_bonus, Decimal(10))

        # Rerunning recovery does not create duplicate
        async with self.session_factory() as session:
            payments = (await session.scalars(select(Payment).where(Payment.id == p1_id, _needs_recovery()))).all()
            self.assertEqual(len(payments), 0, "P1 should NOT be selected again after bonus granted")

            res1_dup = await grant_referral_bonus_for_topup(session, purchaser_user_id=p_id, payment_id=p1_id, topup_amount=Decimal(100))
            self.assertEqual(res1_dup.purchaser_welcome_bonus, Decimal(0))

    async def test_concurrent_worker_recovery(self):
        """Test that _recover_stale_topups correctly serializes concurrent workers via skip_locked."""
        import asyncio

        from database.models import AccountLedgerEntry
        from services.workers.payments import _recover_stale_topups

        async with self.session_factory() as session:
            referrer = User(telegram_id=300)
            session.add(referrer)
            await session.flush()
            
            purchaser = User(telegram_id=400, referred_by=300)
            session.add(purchaser)
            await session.commit()
            
            p_id = purchaser.id

        from datetime import timedelta
        stale_time = datetime.now(timezone.utc) - timedelta(minutes=10)

        async with self.session_factory() as session:
            p1 = Payment(
                user_id=p_id, amount=Decimal(100), currency='RUB',
                public_order_id=str(uuid.uuid4()), external_id=str(uuid.uuid4()),
                provider_idempotency_key=str(uuid.uuid4()), provider_status='succeeded',
                provider_confirmed_at=stale_time,
                fulfillment_status='succeeded', credited_at=stale_time,
                created_at=stale_time,
                reconciliation_status='ok', checkout_status='active',
            )
            session.add(p1)
            await session.commit()

        # We need a patch for session_scope so the worker uses our test DB
        from unittest.mock import patch

        async def dummy_session_scope():
            # Return a real async session connected to the test DB
            return self.session_factory()

        # Since _recover_stale_topups uses session_scope as an async context manager, we must wrap it
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def patched_session_scope():
            async with self.session_factory() as s:
                try:
                    yield s
                    await s.commit()
                except:
                    await s.rollback()
                    raise

        with patch("services.workers.payments.session_scope", new=patched_session_scope):
            # Run 5 concurrent workers!
            await asyncio.gather(
                _recover_stale_topups(None),
                _recover_stale_topups(None),
                _recover_stale_topups(None),
                _recover_stale_topups(None),
                _recover_stale_topups(None),
            )
            
        # Verify exactly one welcome bonus ledger entry was created
        async with self.session_factory() as session:
            entries = (await session.scalars(
                select(AccountLedgerEntry).where(
                    AccountLedgerEntry.user_id == p_id,
                    AccountLedgerEntry.entry_type == "admin_adjustment",
                    AccountLedgerEntry.metadata_["reason"].as_string() == "first_topup_welcome"
                )
            )).all()
    
            self.assertEqual(len(entries), 1, "Only one welcome bonus should be granted despite 5 concurrent workers")

    async def test_needs_recovery_uses_partial_index(self):
        """Test that the _needs_recovery query actually uses the partial index on production data sizes."""
        async with self.session_factory() as session:
            # Insert 5000 irrelevant rows so the planner naturally prefers an Index Scan
            # over a full Seq Scan without artificial settings.
            await session.execute(
                text(
                    "INSERT INTO payments (user_id, amount, currency, provider_status, fulfillment_status, created_at, updated_at, public_order_id, external_id, provider_idempotency_key) "
                    "SELECT 1, 100, 'RUB', 'succeeded', 'succeeded', now(), now(), md5(random()::text), md5(random()::text), md5(random()::text) "
                    "FROM generate_series(1, 5000)"
                )
            )
            await session.execute(
                text(
                    "UPDATE payments SET topup_context = '{\"referral_bonus_processed\": true}'::jsonb"
                )
            )
            await session.commit()
            
        async with self.engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT").execute(text("ANALYZE payments"))

        async with self.session_factory() as session:
            stmt = select(Payment).where(_needs_recovery())
            compiled = stmt.compile(dialect=session.bind.dialect, compile_kwargs={"literal_binds": True})
            explain_query = f"EXPLAIN {compiled!s}"
            
            result = await session.execute(text(explain_query))
            explain_plan = "\n".join([row[0] for row in result.fetchall()])
            
            index_name = "ix_payments_referral_bonus_unprocessed"
            self.assertIn(index_name, explain_plan, f"The query planner did NOT use the partial index! Plan:\n{explain_plan}")
