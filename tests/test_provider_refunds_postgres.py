"""PostgreSQL contracts for the durable YooKassa refund outbox."""

import os
import unittest
import uuid
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import (
    AccountBalanceReservation,
    AccountLedgerEntry,
    Payment,
    User,
)
from database.refund_models import ProviderRefundOperation
from database.repositories.account_ledger_repo import (
    credit_succeeded_topup,
    get_account_balance,
)
from services.provider_refunds import (
    claim,
    finalize,
    perform_http,
    request_balance_topup_refund,
)
from services.yookassa_service import YooKassaErrorKind, YooKassaResult
from utils.datetime_helpers import now_utc


DB = os.getenv("TEST_DATABASE_URL")
TRUNCATE_SQL = (
    "TRUNCATE provider_refund_operations, webhook_inbox, payment_refunds, "
    "payment_provider_operations, "
    "account_balance_reservations, account_ledger_allocations, "
    "account_ledger_entries, payment_events, audit_logs, payments, users, "
    "system_settings, payment_disputes "
    "RESTART IDENTITY CASCADE"
)


class PendingThenSucceededTransport:
    post_calls = 0
    get_calls = 0
    _last_payload = {}

    @classmethod
    async def create_refund_result(cls, payload, *, idempotency_key):
        cls.post_calls += 1
        cls._last_payload[payload.get("payment_id", "pay_refund_test")] = payload
        return YooKassaResult(
            True,
            value={
                "id": "refund_pending_test",
                "payment_id": payload["payment_id"],
                "status": "pending",
                "amount": payload["amount"],
            },
        )

    @classmethod
    async def get_refund_result(cls, refund_id):
        cls.get_calls += 1
        saved = (
            cls._last_payload.get("pay_refund_test", {})
            if not cls._last_payload
            else list(cls._last_payload.values())[-1]
        )
        return YooKassaResult(
            True,
            value={
                "id": refund_id,
                "payment_id": saved.get("payment_id", "pay_refund_test"),
                "status": "succeeded",
                "amount": saved.get("amount", {"value": "100.00", "currency": "RUB"}),
            },
        )


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class ProviderRefundPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(DB)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        PendingThenSucceededTransport.post_calls = 0
        PendingThenSucceededTransport.get_calls = 0
        async with self.sessions.begin() as session:
            await session.execute(text(TRUNCATE_SQL))
            user = User(telegram_id=uuid.uuid4().int % 10**12)
            session.add(user)
            await session.flush()
            self.user_id = user.id
            payment = Payment(
                user_id=user.id,
                amount=Decimal("100"),
                currency="RUB",
                public_order_id="topup_" + uuid.uuid4().hex,
                provider_idempotency_key=uuid.uuid4().hex,
                provider_status="succeeded",
                fulfillment_status="succeeded",
                reconciliation_status="ok",
                checkout_status="active",
                ui_visible=False,
                provider_confirmed_at=now_utc(),
                external_id="pay_refund_test",
            )
            session.add(payment)
            await session.flush()
            await credit_succeeded_topup(session, locked_payment=payment)
            self.payment_id = payment.id

    async def asyncTearDown(self):
        try:
            async with self.sessions.begin() as session:
                await session.execute(text(TRUNCATE_SQL))
        finally:
            await self.engine.dispose()

    async def test_pending_refund_reconciles_without_duplicate_command(self):
        async with self.sessions.begin() as session:
            first = await request_balance_topup_refund(
                session,
                payment_id=self.payment_id,
                requested_by_admin_id=123,
            )
            second = await request_balance_topup_refund(
                session,
                payment_id=self.payment_id,
                requested_by_admin_id=123,
            )
            self.assertTrue(first.created)
            self.assertFalse(second.created)
            self.assertEqual(first.operation.id, second.operation.id)
            self.assertEqual(first.reservation.status, "active")
            balance = await get_account_balance(session, user_id=self.user_id)
            self.assertEqual(balance.available, Decimal("0.00"))
            self.assertEqual(balance.reserved, Decimal("100.00"))

        async with self.sessions.begin() as session:
            first_claim = await claim(session, "refund-worker")
        first_result = await perform_http(
            first_claim, transport=PendingThenSucceededTransport
        )
        async with self.sessions.begin() as session:
            await finalize(session, first_claim, first_result)
            operation = await session.get(
                ProviderRefundOperation, first_claim.operation_id
            )
            self.assertEqual(operation.status, "retry")
            self.assertEqual(operation.provider_status, "pending")
            operation.next_attempt_at = now_utc()

        async with self.sessions.begin() as session:
            second_claim = await claim(session, "refund-worker")
        second_result = await perform_http(
            second_claim, transport=PendingThenSucceededTransport
        )
        async with self.sessions.begin() as session:
            await finalize(session, second_claim, second_result)
            operation = await session.get(
                ProviderRefundOperation, second_claim.operation_id
            )
            reservation = await session.get(
                AccountBalanceReservation, operation.reservation_id
            )
            payment = await session.get(Payment, self.payment_id)
            balance = await get_account_balance(session, user_id=self.user_id)
            self.assertEqual(operation.status, "completed")
            self.assertEqual(operation.provider_status, "succeeded")
            self.assertEqual(reservation.status, "consumed")
            self.assertEqual(payment.provider_status, "refunded")
            self.assertEqual(payment.fulfillment_status, "reversed")
            self.assertEqual(balance.available, Decimal("0"))
            self.assertEqual(
                await session.scalar(
                    select(func.count(AccountLedgerEntry.id)).where(
                        AccountLedgerEntry.payment_id == self.payment_id,
                        AccountLedgerEntry.entry_type == "refund_debit",
                    )
                ),
                1,
            )
            self.assertEqual(PendingThenSucceededTransport.post_calls, 1)
            self.assertEqual(PendingThenSucceededTransport.get_calls, 1)

    async def test_ambiguous_terminal_failure_keeps_hold_and_reservation(self):
        async with self.sessions.begin() as session:
            request = await request_balance_topup_refund(
                session,
                payment_id=self.payment_id,
                requested_by_admin_id=123,
            )
            request.operation.max_attempts = 1

        async with self.sessions.begin() as session:
            operation_claim = await claim(session, "refund-worker")
        async with self.sessions.begin() as session:
            await finalize(
                session,
                operation_claim,
                YooKassaResult(
                    False,
                    error_kind=YooKassaErrorKind.TIMEOUT,
                    retryable=True,
                    ambiguous=True,
                ),
            )
            operation = await session.get(
                ProviderRefundOperation, operation_claim.operation_id
            )
            reservation = await session.get(
                AccountBalanceReservation, operation.reservation_id
            )
            user = await session.get(User, self.user_id)
            self.assertEqual(operation.status, "failed")
            self.assertEqual(reservation.status, "active")
            self.assertTrue(user.financial_hold)
            self.assertTrue(user.topup_blocked)
            self.assertEqual(
                user.financial_block_reason,
                "provider_refund_outcome_ambiguous",
            )

    async def test_refund_reverses_referral_bonuses(self):
        from services.account_topup import settle_succeeded_topup

        async with self.sessions.begin() as session:
            referrer = User(telegram_id=uuid.uuid4().int % 10**12)
            session.add(referrer)
            await session.flush()

            purchaser = User(
                telegram_id=uuid.uuid4().int % 10**12,
                referred_by=referrer.telegram_id,
            )
            session.add(purchaser)
            await session.flush()

            payment = Payment(
                user_id=purchaser.id,
                amount=Decimal("1000.00"),
                currency="RUB",
                public_order_id="topup_" + uuid.uuid4().hex,
                provider_idempotency_key=uuid.uuid4().hex,
                provider_status="succeeded",
                fulfillment_status="not_ready",
                reconciliation_status="ok",
                checkout_status="active",
                ui_visible=False,
                provider_confirmed_at=now_utc(),
                external_id="pay_refund_bonus_test",
            )
            session.add(payment)
            await session.flush()

            await settle_succeeded_topup(session, payment=payment, source="test")
            self.payment_id_bonus = payment.id
            self.referrer_id = referrer.id
            self.purchaser_id = purchaser.id

        async with self.sessions.begin() as session:
            referrer_balance = await get_account_balance(session, user_id=self.referrer_id)
            purchaser_balance = await get_account_balance(session, user_id=self.purchaser_id)

            self.assertEqual(referrer_balance.bonus_position, Decimal("100.00"))  # 10%
            self.assertEqual(purchaser_balance.real_position, Decimal("1000.00"))
            self.assertEqual(purchaser_balance.bonus_position, Decimal("100.00"))  # 10%

        # Request refund
        async with self.sessions.begin() as session:
            await request_balance_topup_refund(
                session,
                payment_id=self.payment_id_bonus,
                requested_by_admin_id=123,
            )

        # Process refund (first pass — pending)
        async with self.sessions.begin() as session:
            claim_op = await claim(session, "refund-worker")

        result = await perform_http(
            claim_op, transport=PendingThenSucceededTransport
        )

        async with self.sessions.begin() as session:
            await finalize(session, claim_op, result)

        # Advance time to allow retry
        async with self.sessions.begin() as session:
            await session.execute(text(
                "UPDATE provider_refund_operations "
                "SET next_attempt_at = next_attempt_at - interval '10 minutes'"
            ))

        # Second pass (succeeded)
        async with self.sessions.begin() as session:
            claim_op2 = await claim(session, "refund-worker")

        result2 = await perform_http(
            claim_op2, transport=PendingThenSucceededTransport
        )

        async with self.sessions.begin() as session:
            await finalize(session, claim_op2, result2)

        # Verify balances after refund
        async with self.sessions.begin() as session:
            referrer_balance = await get_account_balance(session, user_id=self.referrer_id)
            self.assertEqual(referrer_balance.bonus_position, Decimal("0.00"))
            self.assertEqual(referrer_balance.real_position, Decimal("0.00"))

            purchaser_balance = await get_account_balance(session, user_id=self.purchaser_id)
            self.assertEqual(purchaser_balance.real_position, Decimal("0.00"))
            self.assertEqual(purchaser_balance.bonus_position, Decimal("0.00"))

            # Verify exactly 2 reversal entries were created (referrer + purchaser)
            reversals = await session.scalars(
                select(AccountLedgerEntry).where(
                    AccountLedgerEntry.entry_type == "admin_adjustment",
                    AccountLedgerEntry.amount < 0
                )
            )
            reversal_list = [
                r for r in reversals.all()
                if r.metadata_
                and r.metadata_.get("topup_payment_id") == self.payment_id_bonus
            ]
            self.assertEqual(len(reversal_list), 2)
