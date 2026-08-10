"""PostgreSQL contracts for concurrent first-topup bonus and referral bonuses."""

import asyncio
import os
import unittest
import uuid
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import User, Payment, AccountLedgerEntry
from database.repositories.account_ledger_repo import get_account_balance
from services.account_topup import settle_succeeded_topup
from services.referral_bonus import reverse_referral_bonus_for_topup
from utils.datetime_helpers import now_utc

DB = os.getenv("TEST_DATABASE_URL")
TRUNCATE_SQL = (
    "TRUNCATE provider_refund_operations, webhook_inbox, payment_refunds, "
    "account_balance_reservations, "
    "account_ledger_allocations, account_ledger_entries, "
    "payment_events, audit_logs, payments, users, system_settings, payment_disputes RESTART IDENTITY CASCADE"
)


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class ReferralBonusConcurrencyPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(DB, pool_size=5)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions.begin() as session:
            await session.execute(text(TRUNCATE_SQL))

            self.referrer = User(telegram_id=int(uuid.uuid4().int % 1000000000))
            session.add(self.referrer)
            await session.flush()

            self.purchaser = User(
                telegram_id=int(uuid.uuid4().int % 1000000000),
                referred_by=self.referrer.telegram_id,
            )
            session.add(self.purchaser)
            await session.flush()

            self.payment_a = Payment(
                user_id=self.purchaser.id,
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
            self.payment_b = Payment(
                user_id=self.purchaser.id,
                amount=Decimal("2000.00"),
                currency="RUB",
                public_order_id="topup_b",
                provider_idempotency_key="key_b",
                external_id="ext_b",
                provider_status="succeeded",
                provider_confirmed_at=now_utc(),
                fulfillment_status="not_ready",
                reconciliation_status="ok",
                checkout_status="active",
                ui_visible=False,
            )
            session.add(self.payment_a)
            session.add(self.payment_b)
            await session.flush()
            self.payment_a_id = self.payment_a.id
            self.payment_b_id = self.payment_b.id

    async def asyncTearDown(self):
        try:
            async with self.sessions.begin() as session:
                await session.execute(text(TRUNCATE_SQL))
        finally:
            await self.engine.dispose()

    async def test_concurrent_first_topup(self):
        async def process_payment(payment_id):
            async with self.sessions() as session:
                async with session.begin():
                    payment = await session.get(Payment, payment_id)
                    await settle_succeeded_topup(session, payment=payment, source="test")

        await asyncio.gather(
            process_payment(self.payment_a_id),
            process_payment(self.payment_b_id),
        )

        async with self.sessions() as session:
            referrer_balance = await get_account_balance(session, user_id=self.referrer.id)
            purchaser_balance = await get_account_balance(session, user_id=self.purchaser.id)

            self.assertEqual(referrer_balance.bonus_position, Decimal("300.00"))

            result = await session.execute(
                select(AccountLedgerEntry).where(
                    AccountLedgerEntry.user_id == self.purchaser.id,
                    AccountLedgerEntry.idempotency_key.like("referral-bonus:first-topup-welcome:%"),
                )
            )
            welcome_entries = result.scalars().all()

            self.assertEqual(len(welcome_entries), 1, "Exactly one welcome bonus must be granted")

            welcome_amount = welcome_entries[0].amount
            self.assertIn(welcome_amount, [Decimal("100.00"), Decimal("200.00")])

            self.assertEqual(purchaser_balance.real_position, Decimal("3000.00"))
            self.assertEqual(purchaser_balance.bonus_position, welcome_amount)

    async def test_referral_bonus_failure_rolls_back_topup_credit(self):
        async with self.sessions() as session:
            with self.assertRaises(RuntimeError):
                async with session.begin():
                    payment = await session.get(Payment, self.payment_a_id)
                    with patch(
                        "services.referral_bonus.grant_referral_bonus_for_topup",
                        side_effect=RuntimeError("synthetic referral failure"),
                    ):
                        await settle_succeeded_topup(
                            session, payment=payment, source="test"
                        )

        async with self.sessions() as session:
            payment = await session.get(Payment, self.payment_a_id)
            self.assertIsNotNone(payment)
            self.assertIsNone(payment.credited_at)
            self.assertEqual(payment.fulfillment_status, "not_ready")
            credited = await session.scalar(
                select(AccountLedgerEntry.id).where(
                    AccountLedgerEntry.payment_id == self.payment_a_id
                )
            )
            self.assertIsNone(credited)

    async def test_concurrent_referral_refund_reversal_is_idempotent(self):
        async with self.sessions() as session:
            async with session.begin():
                payment = await session.get(Payment, self.payment_a_id)
                await settle_succeeded_topup(session, payment=payment, source="test")

        async def reverse_once():
            async with self.sessions() as session:
                async with session.begin():
                    return await reverse_referral_bonus_for_topup(
                        session, payment_id=self.payment_a_id
                    )

        first, second = await asyncio.gather(reverse_once(), reverse_once())

        async with self.sessions() as session:
            referrer_reversals = await session.scalars(
                select(AccountLedgerEntry).where(
                    AccountLedgerEntry.user_id == self.referrer.id,
                    AccountLedgerEntry.entry_type == "admin_adjustment",
                    AccountLedgerEntry.amount < 0,
                    AccountLedgerEntry.idempotency_key
                    == f"referral-bonus-reversal:topup:{self.payment_a_id}:{self.referrer.id}",
                )
            )
            purchaser_reversals = await session.scalars(
                select(AccountLedgerEntry).where(
                    AccountLedgerEntry.user_id == self.purchaser.id,
                    AccountLedgerEntry.entry_type == "admin_adjustment",
                    AccountLedgerEntry.amount < 0,
                    AccountLedgerEntry.idempotency_key
                    == f"referral-bonus-reversal:first-topup-welcome:{self.payment_a_id}:{self.purchaser.id}",
                )
            )

            self.assertEqual(first, Decimal("200.00"))
            self.assertEqual(second, Decimal("200.00"))
            self.assertEqual(len(referrer_reversals.all()), 1)
            self.assertEqual(len(purchaser_reversals.all()), 1)

            referrer_balance = await get_account_balance(
                session, user_id=self.referrer.id
            )
            purchaser_balance = await get_account_balance(
                session, user_id=self.purchaser.id
            )
            self.assertEqual(referrer_balance.bonus_position, Decimal("0.00"))
            self.assertEqual(purchaser_balance.bonus_position, Decimal("0.00"))
