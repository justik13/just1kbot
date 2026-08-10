import asyncio
import os
import uuid
from decimal import Decimal
import unittest
import pytest

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database.models import User, Payment, AccountLedgerEntry
from database.repositories.account_ledger_repo import get_account_balance
from services.account_topup import settle_succeeded_topup
from utils import now_utc

DB = os.getenv("TEST_DATABASE_URL")
if not DB:
    pytest.skip("No TEST_DATABASE_URL found", allow_module_level=True)

TRUNCATE_SQL = (
    "TRUNCATE provider_refund_operations, webhook_inbox, payment_refunds, "
    "account_balance_reservations, "
    "account_ledger_allocations, account_ledger_entries, "
    "payment_events, audit_logs, payments, users, system_settings, payment_disputes RESTART IDENTITY CASCADE"
)

class AccountTopupConcurrencyPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(DB, pool_size=5)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions.begin() as session:
            await session.execute(text(TRUNCATE_SQL))
            
            self.user = User(telegram_id=int(uuid.uuid4().int % 1000000000))
            session.add(self.user)
            await session.flush()
            
            self.payment = Payment(
                user_id=self.user.id,
                amount=Decimal("1000.00"),
                currency="RUB",
                public_order_id="topup_a",
                provider_idempotency_key="key_a",
                external_id="ext_a",
                provider_status="succeeded",
                provider_confirmed_at=now_utc(),
                fulfillment_status="not_ready",
                reconciliation_status="ok",
                checkout_status="active",
                ui_visible=False,
            )
            session.add(self.payment)
            await session.flush()
            self.payment_id = self.payment.id

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_concurrent_same_payment_topup(self):
        # We simulate two concurrent webhooks processing the exact same payment.
        
        async def process_payment():
            async with self.sessions() as session:
                async with session.begin():
                    payment = await session.get(Payment, self.payment_id)
                    # Use a mock settings to avoid relying on actual .env if needed
                    # but test_runner provides it.
                    await settle_succeeded_topup(session, payment=payment, source="test")
        
        results = await asyncio.gather(
            process_payment(),
            process_payment(),
            return_exceptions=True
        )
        for r in results:
            print("RESULT/EXCEPTION:", r)
        
        async with self.sessions() as session:
            balance = await get_account_balance(session, user_id=self.user.id)
            
            # Since both tried to settle the same 1000 RUB payment concurrently,
            # exactly one should have succeeded and the other should have either
            # raised an exception or exited early because the payment status changed.
            # The balance must be exactly 1000 RUB.
            self.assertEqual(balance.real_position, Decimal("1000.00"))
            
            # Check how many ledger entries were created for topup
            result = await session.execute(
                select(AccountLedgerEntry).where(
                    AccountLedgerEntry.user_id == self.user.id,
                    AccountLedgerEntry.payment_id == self.payment_id,
                    AccountLedgerEntry.entry_type == "payment_credit"
                )
            )
            entries = result.scalars().all()
            self.assertEqual(len(entries), 1, "Exactly one topup ledger entry must be created")
            
            payment_db = await session.get(Payment, self.payment_id)
            self.assertIsNotNone(payment_db.credited_at)
            self.assertEqual(payment_db.fulfillment_status, "succeeded")
