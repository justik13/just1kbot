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
            await session.commit()
            # P2 shouldn't receive welcome bonus because P1 exists (id < P2.id)
            self.assertEqual(res2.purchaser_welcome_bonus, Decimal(0))

        # Recovery selects P1
        async with self.session_factory() as session:
            payments = (await session.scalars(select(Payment).where(Payment.id == p1_id, _needs_recovery()))).all()
            self.assertTrue(len(payments) > 0, "P1 should be selected for recovery")
            
            res1 = await grant_referral_bonus_for_topup(session, purchaser_user_id=p_id, payment_id=p1_id, topup_amount=Decimal(100))
            await session.commit()
            # P1 MUST receive the welcome bonus (10% of 100 = 10) exactly once after the fix
            self.assertEqual(res1.purchaser_welcome_bonus, Decimal(10))

        # Rerunning recovery does not create duplicate
        async with self.session_factory() as session:
            payments = (await session.scalars(select(Payment).where(Payment.id == p1_id, _needs_recovery()))).all()
            self.assertEqual(len(payments), 0, "P1 should NOT be selected again after bonus granted")

            res1_dup = await grant_referral_bonus_for_topup(session, purchaser_user_id=p_id, payment_id=p1_id, topup_amount=Decimal(100))
            # Idempotency guarantees it returns 0 for dup
            self.assertEqual(res1_dup.purchaser_welcome_bonus, Decimal(0))
