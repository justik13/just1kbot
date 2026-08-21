"""PostgreSQL contracts for manual balance-topup disputes."""

import os
import unittest
import uuid
from datetime import timezone
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.dispute_models import PaymentDispute
from database.models import (
    AccountBalanceReservation,
    AccountLedgerEntry,
    Payment,
    User,
)
from database.repositories.account_ledger_repo import (
    create_admin_adjustment,
    credit_succeeded_topup,
    get_account_balance,
)
from services.account_topup import settle_succeeded_topup
from services.payment_disputes import (
    mark_payment_dispute_manual_review,
    open_payment_dispute,
    resolve_payment_dispute,
)
from services.provider_refunds import BalanceRefundError, request_balance_topup_refund
from utils.datetime_helpers import now_utc

DB = os.getenv("TEST_DATABASE_URL")
TRUNCATE_SQL = (
    "TRUNCATE payment_disputes, provider_refund_operations, webhook_inbox, "
    "payment_refunds, payment_provider_operations, "
    "account_balance_reservations, account_ledger_allocations, "
    "account_ledger_entries, payment_events, audit_logs, payments, users "
    "RESTART IDENTITY CASCADE"
)


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class PaymentDisputesPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(DB)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions.begin() as session:
            await session.execute(text(TRUNCATE_SQL))
            user = User(telegram_id=uuid.uuid4().int % 10**12)
            session.add(user)
            await session.flush()
            self.user_id = user.id

    async def asyncTearDown(self):
        try:
            async with self.sessions.begin() as session:
                await session.execute(text(TRUNCATE_SQL))
        finally:
            await self.engine.dispose()

    async def _topup(self, session, amount, *, external_id=None):
        payment = Payment(
            user_id=self.user_id,
            amount=Decimal(amount),
            currency="RUB",
            public_order_id="topup_" + uuid.uuid4().hex,
            provider_idempotency_key=uuid.uuid4().hex,
            provider_status="succeeded",
            fulfillment_status="succeeded",
            reconciliation_status="ok",
            checkout_status="active",
            ui_visible=False,
            provider_confirmed_at=now_utc(),
            external_id=external_id or "pay_" + uuid.uuid4().hex,
        )
        session.add(payment)
        await session.flush()
        await credit_succeeded_topup(session, locked_payment=payment)
        return payment

    async def test_open_is_idempotent_reserves_and_win_releases(self):
        disputed_at = now_utc().astimezone(timezone.utc)
        async with self.sessions.begin() as session:
            payment = await self._topup(session, 100)
            first = await open_payment_dispute(
                session,
                provider_payment_id=payment.external_id,
                provider_case_id="case-open-win",
                amount=60,
                disputed_at=disputed_at,
                note="bank inquiry",
                admin_id=123,
            )
            second = await open_payment_dispute(
                session,
                provider_payment_id=payment.external_id,
                provider_case_id="case-open-win",
                amount=60,
                disputed_at=disputed_at,
                note="same command",
                admin_id=123,
            )
            self.assertTrue(first.created)
            self.assertFalse(second.created)
            self.assertEqual(first.dispute.id, second.dispute.id)
            reservation = await session.get(
                AccountBalanceReservation, first.dispute.reservation_id
            )
            user = await session.get(User, self.user_id)
            balance = await get_account_balance(session, user_id=self.user_id)
            self.assertEqual(reservation.amount, Decimal("60.00"))
            self.assertEqual(reservation.status, "active")
            self.assertEqual(balance.available, Decimal("40.00"))
            self.assertTrue(user.financial_hold)
            self.assertEqual(user.financial_block_reason, "open_payment_dispute")
            with self.assertRaises(BalanceRefundError) as ctx:
                await request_balance_topup_refund(
                    session,
                    payment_id=payment.id,
                    requested_by_admin_id=123,
                )
            self.assertEqual(ctx.exception.code, "payment_has_active_dispute")

            won = await resolve_payment_dispute(
                session,
                dispute_id=first.dispute.id,
                outcome="won_by_merchant",
                admin_id=123,
                note="merchant evidence accepted",
            )
            balance = await get_account_balance(session, user_id=self.user_id)
            self.assertEqual(won.status, "won_by_merchant")
            self.assertEqual(reservation.status, "released")
            self.assertEqual(balance.available, Decimal("100.00"))
            self.assertFalse(user.financial_hold)
            self.assertIsNone(user.financial_block_reason)
            self.assertEqual(payment.reconciliation_status, "ok")

    async def test_manual_review_preserves_reservation_and_hold(self):
        async with self.sessions.begin() as session:
            payment = await self._topup(session, 100)
            result = await open_payment_dispute(
                session,
                provider_payment_id=payment.external_id,
                provider_case_id="case-manual",
                amount=30,
                disputed_at=now_utc(),
                note=None,
                admin_id=123,
            )
            dispute = await mark_payment_dispute_manual_review(
                session,
                dispute_id=result.dispute.id,
                admin_id=123,
                note="waiting for bank documents",
            )
            reservation = await session.get(
                AccountBalanceReservation, dispute.reservation_id
            )
            user = await session.get(User, self.user_id)
            self.assertEqual(dispute.status, "manual_review")
            self.assertEqual(reservation.status, "active")
            self.assertTrue(user.financial_hold)

    async def test_partial_loss_creates_one_debit_debt_and_topup_recovers(self):
        async with self.sessions.begin() as session:
            payment = await self._topup(session, 100)
            await create_admin_adjustment(
                session,
                user_id=self.user_id,
                signed_amount=-80,
                idempotency_key="dispute-spend:" + uuid.uuid4().hex,
                metadata={"reason": "simulate spent balance"},
            )
            result = await open_payment_dispute(
                session,
                provider_payment_id=payment.external_id,
                provider_case_id="case-lost-partial",
                amount=40,
                disputed_at=now_utc(),
                note="partial chargeback",
                admin_id=123,
            )
            reservation = await session.get(
                AccountBalanceReservation, result.dispute.reservation_id
            )
            self.assertEqual(reservation.amount, Decimal("20.00"))

            lost = await resolve_payment_dispute(
                session,
                dispute_id=result.dispute.id,
                outcome="lost_by_merchant",
                admin_id=123,
            )
            duplicate = await resolve_payment_dispute(
                session,
                dispute_id=result.dispute.id,
                outcome="lost_by_merchant",
                admin_id=123,
            )
            balance = await get_account_balance(session, user_id=self.user_id)
            user = await session.get(User, self.user_id)
            self.assertEqual(lost.id, duplicate.id)
            self.assertEqual(lost.status, "lost_by_merchant")
            self.assertEqual(reservation.status, "consumed")
            self.assertEqual(balance.accounting_position, Decimal("-20.00"))
            self.assertEqual(balance.debt, Decimal("20.00"))
            self.assertEqual(balance.available, Decimal(0))
            self.assertTrue(user.financial_hold)
            self.assertEqual(user.financial_block_reason, "chargeback_debt")
            self.assertEqual(
                await session.scalar(
                    select(func.count(AccountLedgerEntry.id)).where(
                        AccountLedgerEntry.payment_id == payment.id,
                        AccountLedgerEntry.entry_type == "chargeback_debit",
                    )
                ),
                1,
            )

            recovery = Payment(
                user_id=self.user_id,
                amount=Decimal(20),
                currency="RUB",
                    public_order_id="topup_" + uuid.uuid4().hex,
                provider_idempotency_key=uuid.uuid4().hex,
                provider_status="succeeded",
                fulfillment_status="not_ready",
                reconciliation_status="ok",
                checkout_status="active",
                ui_visible=False,
                provider_confirmed_at=now_utc(),
                external_id="pay_" + uuid.uuid4().hex,
            )
            session.add(recovery)
            await session.flush()
            await settle_succeeded_topup(
                session,
                payment=recovery,
                source="dispute_test",
                settings=SimpleNamespace(BALANCE_MAX_AVAILABLE_RUB=10000),
            )
            balance = await get_account_balance(session, user_id=self.user_id)
            self.assertEqual(balance.accounting_position, Decimal("0.00"))
            self.assertEqual(balance.debt, Decimal(0))
            self.assertFalse(user.financial_hold)
            self.assertIsNone(user.financial_block_reason)
            self.assertEqual(
                await session.scalar(select(func.count(PaymentDispute.id))),
                1,
            )
