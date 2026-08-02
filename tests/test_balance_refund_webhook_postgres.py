"""PostgreSQL contracts for refund webhooks against account-balance top-ups."""

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
    PaymentFulfillmentOperation,
    PaymentRefund,
    User,
    WebhookInbox,
)
from database.refund_models import ProviderRefundOperation
from database.repositories.account_ledger_repo import (
    create_admin_adjustment,
    credit_succeeded_topup,
    get_account_balance,
)
from services.provider_refunds import (
    claim as claim_provider_refund,
    request_balance_topup_refund,
)
from services.workers.webhook_inbox import InboxClaim, finalize
from utils.datetime_helpers import now_utc


DB = os.getenv("TEST_DATABASE_URL")
TRUNCATE_SQL = (
    "TRUNCATE provider_refund_operations, webhook_inbox, payment_refunds, "
    "payment_fulfillment_operations, account_balance_reservations, "
    "account_ledger_allocations, account_ledger_entries, "
    "payment_events, audit_logs, payments, users RESTART IDENTITY CASCADE"
)


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class BalanceRefundWebhookPostgresTests(unittest.IsolatedAsyncioTestCase):
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

    async def _topup(self, session, amount=100):
        external_id = "pay_" + uuid.uuid4().hex
        payment = Payment(
            user_id=self.user_id,
            tariff_id=None,
            payment_kind="balance_topup",
            amount=Decimal(amount),
            currency="RUB",
            status="completed",
            public_order_id="topup_" + uuid.uuid4().hex,
            provider_idempotency_key=uuid.uuid4().hex,
            provider_status="succeeded",
            fulfillment_status="succeeded",
            reconciliation_status="ok",
            checkout_status="active",
            ui_visible=False,
            snapshot_amount=Decimal(amount),
            snapshot_currency="RUB",
            provider_confirmed_at=now_utc(),
            external_id=external_id,
        )
        session.add(payment)
        await session.flush()
        await credit_succeeded_topup(session, locked_payment=payment)
        return payment

    async def _refund(self, session, payment, *, refund_id, event_key, amount):
        payload = {
            "object": {
                "id": refund_id,
                "payment_id": payment.external_id,
                "amount": {"value": f"{Decimal(amount):.2f}", "currency": "RUB"},
            }
        }
        row = WebhookInbox(
            provider="yookassa",
            event_key=event_key,
            event_type="refund.succeeded",
            provider_object_id=refund_id,
            payment_external_id=payment.external_id,
            public_order_id=payment.public_order_id,
            payload=payload,
            status="processing",
            attempts=1,
            max_attempts=30,
            next_attempt_at=now_utc(),
            locked_at=now_utc(),
            locked_by="refund-test-worker",
        )
        session.add(row)
        await session.flush()
        claim = InboxClaim(
            row.id,
            "refund-test-worker",
            1,
            "refund.succeeded",
            payment.external_id,
            payment.public_order_id,
            payload,
            event_key,
        )
        await finalize(session, claim, None)
        return row

    async def test_full_topup_refund_debits_balance_exactly_once(self):
        async with self.sessions.begin() as session:
            payment = await self._topup(session, 100)
            refund_id = "refund_" + uuid.uuid4().hex
            await self._refund(
                session,
                payment,
                refund_id=refund_id,
                event_key=uuid.uuid4().hex,
                amount=100,
            )
            await self._refund(
                session,
                payment,
                refund_id=refund_id,
                event_key=uuid.uuid4().hex,
                amount=100,
            )

            balance = await get_account_balance(session, user_id=self.user_id)
            self.assertEqual(balance.available, Decimal("0.00"))
            self.assertEqual(balance.debt, Decimal("0"))
            self.assertEqual(payment.provider_status, "refunded")
            self.assertEqual(payment.fulfillment_status, "reversed")
            self.assertIsNotNone(payment.reversed_at)
            self.assertEqual(
                await session.scalar(
                    select(func.count(PaymentRefund.id)).where(
                        PaymentRefund.payment_id == payment.id
                    )
                ),
                1,
            )
            self.assertEqual(
                await session.scalar(
                    select(func.count(AccountLedgerEntry.id)).where(
                        AccountLedgerEntry.payment_id == payment.id,
                        AccountLedgerEntry.entry_type == "refund_debit",
                    )
                ),
                1,
            )
            self.assertEqual(
                await session.scalar(
                    select(func.count(PaymentFulfillmentOperation.id)).where(
                        PaymentFulfillmentOperation.payment_id == payment.id
                    )
                ),
                0,
            )

    async def test_refund_after_spending_creates_debt_instead_of_free_money(self):
        async with self.sessions.begin() as session:
            payment = await self._topup(session, 100)
            await create_admin_adjustment(
                session,
                user_id=self.user_id,
                signed_amount=-80,
                idempotency_key="refund-test-spend:" + uuid.uuid4().hex,
                metadata={"reason": "simulate_already_spent_balance"},
            )
            await self._refund(
                session,
                payment,
                refund_id="refund_" + uuid.uuid4().hex,
                event_key=uuid.uuid4().hex,
                amount=100,
            )

            balance = await get_account_balance(session, user_id=self.user_id)
            self.assertEqual(balance.accounting_position, Decimal("-80.00"))
            self.assertEqual(balance.available, Decimal("0"))
            self.assertEqual(balance.debt, Decimal("80.00"))
            self.assertEqual(payment.fulfillment_status, "reversed")

    async def test_webhook_completes_matching_outbox_before_worker_claim(self):
        async with self.sessions.begin() as session:
            payment = await self._topup(session, 100)
            request = await request_balance_topup_refund(
                session,
                payment_id=payment.id,
                requested_by_admin_id=123,
            )
            operation_id = request.operation.id
            reservation_id = request.reservation.id
            refund_id = "refund_" + uuid.uuid4().hex

            await self._refund(
                session,
                payment,
                refund_id=refund_id,
                event_key=uuid.uuid4().hex,
                amount=100,
            )

            operation = await session.get(ProviderRefundOperation, operation_id)
            reservation = await session.get(
                AccountBalanceReservation, reservation_id
            )
            self.assertEqual(operation.status, "completed")
            self.assertEqual(operation.provider_status, "succeeded")
            self.assertEqual(operation.provider_refund_id, refund_id)
            self.assertIsNotNone(operation.completed_at)
            self.assertIsNone(operation.locked_at)
            self.assertIsNone(operation.locked_by)
            self.assertEqual(reservation.status, "consumed")
            self.assertIsNone(
                await claim_provider_refund(session, "late-refund-worker")
            )
