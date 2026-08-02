"""PostgreSQL contracts for account balance, FIFO lots and reservations."""

import asyncio
import os
import unittest
import uuid
from unittest.mock import AsyncMock, patch
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import (
    AccountBalanceReservation,
    AccountLedgerAllocation,
    AccountLedgerEntry,
    EntitlementEntry,
    PaidValueLedgerEntry,
    Payment,
    PaymentFulfillmentOperation,
    PaymentProviderOperation,
    Tariff,
    TariffQuote,
    TariffVersion,
    User,
)
from database.repositories.account_ledger_repo import (
    InsufficientAccountBalanceError,
    create_payment_debit,
    create_purchase_debit,
    credit_succeeded_topup,
    get_account_balance,
    get_payment_refundable_amount,
    reserve_payment_funds,
    resolve_reservation,
)
from services.account_topup import (
    AccountTopupError,
    create_balance_topup,
    hide_balance_topup,
    settle_succeeded_topup,
    settle_succeeded_topup_by_id,
)
from services.account_purchase import (
    AccountPurchaseError,
    prepare_account_purchase,
    settle_account_purchase,
)
from utils.datetime_helpers import now_utc


DB = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class AccountLedgerPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(DB)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions.begin() as session:
            await session.execute(
                text(
                    "TRUNCATE account_balance_reservations, "
                    "account_ledger_allocations, account_ledger_entries, "
                    "entitlement_entries, paid_value_ledger, "
                    "tariff_quotes, tariff_versions, payments, users, tariffs "
                    "RESTART IDENTITY CASCADE"
                )
            )
            user = User(telegram_id=uuid.uuid4().int % 10**12)
            tariff = Tariff(
                name="Account test",
                duration_days=30,
                device_limit=2,
                price_rub=100,
                is_active=True,
            )
            session.add_all((user, tariff))
            await session.flush()
            version = TariffVersion(
                tariff_id=tariff.id,
                version_number=1,
                name_snapshot=tariff.name,
                duration_hours=720,
                device_limit=2,
                price_rub=Decimal("100"),
                currency="RUB",
            )
            session.add(version)
            await session.flush()
            self.user_id = user.id
            self.tariff_id = tariff.id
            self.version_id = version.id

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def topup(self, session, amount: int) -> Payment:
        payment = Payment(
            user_id=self.user_id,
            tariff_id=None,
            payment_kind="balance_topup",
            amount=Decimal(amount),
            currency="RUB",
            status="paid_processing",
            public_order_id="topup_" + uuid.uuid4().hex,
            provider_idempotency_key=uuid.uuid4().hex,
            provider_status="succeeded",
            fulfillment_status="not_ready",
            reconciliation_status="ok",
            checkout_status="active",
            ui_visible=True,
            snapshot_amount=Decimal(amount),
            snapshot_currency="RUB",
            provider_confirmed_at=now_utc(),
        )
        session.add(payment)
        await session.flush()
        return payment

    def topup_settings(self, **overrides):
        values = {
            "BALANCE_MIN_TOPUP_RUB": 10,
            "BALANCE_MAX_CUSTOM_TOPUP_RUB": 5000,
            "BALANCE_MAX_AVAILABLE_RUB": 10000,
            "BALANCE_MAX_UNFINISHED_TOPUPS": 3,
            "BALANCE_MAX_TOPUP_CREATIONS_24H": 10,
            "YOOKASSA_RETURN_URL": "https://t.me/{bot_username}",
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    async def quote(self, session, amount: int, operation: str = "purchase"):
        now = now_utc()
        quote = TariffQuote(
            public_id=uuid.uuid4(),
            user_id=self.user_id,
            operation_type=operation,
            target_tariff_version_id=self.version_id,
            current_paid_hours=0,
            current_paid_value_rub=0,
            bonus_hours=0,
            amount_due_rub=Decimal(amount),
            resulting_paid_hours=720,
            resulting_paid_value_rub=Decimal(amount),
            resulting_bonus_hours=0,
            rounding_loss_hours=0,
            rounding_loss_value_rub=0,
            currency="RUB",
            status="active",
            created_at=now,
            expires_at=now + timedelta(minutes=15),
        )
        session.add(quote)
        await session.flush()
        return quote

    async def test_topup_is_credited_exactly_once(self):
        async with self.sessions.begin() as session:
            payment = await self.topup(session, 100)
            first, created = await credit_succeeded_topup(
                session, payment_id=payment.id
            )
            again, created_again = await credit_succeeded_topup(
                session, payment_id=payment.id
            )
            self.assertTrue(created)
            self.assertFalse(created_again)
            self.assertEqual(first.id, again.id)

        async with self.sessions() as session:
            snapshot = await get_account_balance(session, user_id=self.user_id)
            count = await session.scalar(select(func.count(AccountLedgerEntry.id)))
            self.assertEqual(count, 1)
            self.assertEqual(snapshot.available, Decimal("100.00"))
            self.assertEqual(snapshot.debt, Decimal("0"))

    async def test_topup_creation_is_durable_and_reuses_visible_intent(self):
        async with self.sessions.begin() as session:
            first = await create_balance_topup(
                session,
                user_id=self.user_id,
                amount=499,
                bot_username="balance_bot",
                context={"operation": "purchase", "quote": "q1"},
                settings=self.topup_settings(),
            )
            repeated = await create_balance_topup(
                session,
                user_id=self.user_id,
                amount=500,
                bot_username="balance_bot",
                settings=self.topup_settings(),
            )
            self.assertTrue(first.created)
            self.assertFalse(repeated.created)
            self.assertEqual(first.payment.id, repeated.payment.id)
            self.assertEqual(first.payment.payment_kind, "balance_topup")
            self.assertIsNone(first.payment.tariff_id)
            operation = await session.scalar(
                select(PaymentProviderOperation).where(
                    PaymentProviderOperation.payment_id == first.payment.id
                )
            )
            self.assertEqual(operation.operation_type, "create_payment")
            self.assertEqual(operation.payload["description"], "Пополнение баланса")
            self.assertEqual(operation.payload["amount"]["value"], "499.00")

    async def test_hidden_topup_can_be_replaced_but_remains_financially_live(self):
        async with self.sessions.begin() as session:
            first = await create_balance_topup(
                session,
                user_id=self.user_id,
                amount=40,
                bot_username="bot",
                settings=self.topup_settings(),
            )
            await hide_balance_topup(
                session, user_id=self.user_id, payment_id=first.payment.id
            )
            replacement = await create_balance_topup(
                session,
                user_id=self.user_id,
                amount=50,
                bot_username="bot",
                settings=self.topup_settings(),
            )
            self.assertNotEqual(first.payment.id, replacement.payment.id)
            self.assertFalse(first.payment.ui_visible)
            self.assertEqual(first.payment.checkout_status, "active")
            first.payment.provider_status = "succeeded"
            first.payment.provider_confirmed_at = now_utc()
            created, snapshot = await settle_succeeded_topup(
                session,
                payment=first.payment,
                source="late_hidden_test",
                settings=self.topup_settings(),
            )
            self.assertTrue(created)
            self.assertEqual(snapshot.available, Decimal("40.00"))
            grants = await session.scalar(
                select(func.count(PaymentFulfillmentOperation.id)).where(
                    PaymentFulfillmentOperation.payment_id == first.payment.id
                )
            )
            self.assertEqual(grants, 0)

    async def test_pending_exposure_prevents_late_balance_overflow(self):
        settings = self.topup_settings(BALANCE_MAX_AVAILABLE_RUB=100)
        async with self.sessions.begin() as session:
            first = await create_balance_topup(
                session,
                user_id=self.user_id,
                amount=60,
                bot_username="bot",
                settings=settings,
            )
            await hide_balance_topup(
                session, user_id=self.user_id, payment_id=first.payment.id
            )
            with self.assertRaisesRegex(
                AccountTopupError, "topup_balance_limit_exceeded"
            ):
                await create_balance_topup(
                    session,
                    user_id=self.user_id,
                    amount=50,
                    bot_username="bot",
                    settings=settings,
                )

    async def test_concurrent_topup_recovery_credits_once(self):
        async with self.sessions.begin() as session:
            payment = await self.topup(session, 75)
            payment_id = payment.id

        async def settle():
            async with self.sessions.begin() as session:
                return await settle_succeeded_topup_by_id(
                    session,
                    payment_id=payment_id,
                    source="concurrent_recovery",
                    settings=self.topup_settings(),
                )

        results = await asyncio.gather(settle(), settle())
        self.assertEqual(sum(created for created, _ in results), 1)
        async with self.sessions() as session:
            self.assertEqual(
                await session.scalar(
                    select(func.count(AccountLedgerEntry.id)).where(
                        AccountLedgerEntry.payment_id == payment_id,
                        AccountLedgerEntry.entry_type == "payment_credit",
                    )
                ),
                1,
            )

    async def test_balance_purchase_settles_all_local_ledgers_atomically(self):
        async with self.sessions.begin() as session:
            payment = await self.topup(session, 100)
            await credit_succeeded_topup(session, payment_id=payment.id)
            intent = await prepare_account_purchase(
                session, user_id=self.user_id, tariff_id=self.tariff_id
            )
            public_id = intent.quote.public_id
            result = await settle_account_purchase(
                session,
                user_id=self.user_id,
                quote_public_id=public_id,
            )
            self.assertTrue(result.created)
            self.assertEqual(result.balance_after.available, Decimal("0.00"))

        async with self.sessions.begin() as session:
            repeated = await settle_account_purchase(
                session,
                user_id=self.user_id,
                quote_public_id=public_id,
            )
            self.assertFalse(repeated.created)
            quote = await session.scalar(
                select(TariffQuote).where(TariffQuote.public_id == public_id)
            )
            user = await session.get(User, self.user_id)
            self.assertEqual(quote.status, "consumed")
            self.assertEqual(user.current_tariff_id, self.tariff_id)
            self.assertIsNotNone(user.subscription_end)
            self.assertEqual(
                await session.scalar(
                    select(func.count(AccountLedgerEntry.id)).where(
                        AccountLedgerEntry.entry_type == "purchase_debit",
                        AccountLedgerEntry.quote_id == quote.id,
                    )
                ),
                1,
            )
            self.assertEqual(
                await session.scalar(
                    select(func.count(PaidValueLedgerEntry.id)).where(
                        PaidValueLedgerEntry.entry_type == "account_purchase",
                        PaidValueLedgerEntry.quote_id == quote.id,
                    )
                ),
                1,
            )
            self.assertEqual(
                await session.scalar(
                    select(func.count(EntitlementEntry.id)).where(
                        EntitlementEntry.entry_type == "account_purchase_grant",
                        EntitlementEntry.source_id == str(quote.id),
                    )
                ),
                1,
            )

    async def test_insufficient_balance_never_creates_purchase_side_effects(self):
        async with self.sessions.begin() as session:
            intent = await prepare_account_purchase(
                session, user_id=self.user_id, tariff_id=self.tariff_id
            )
            with self.assertRaisesRegex(
                AccountPurchaseError, "insufficient_balance"
            ):
                await settle_account_purchase(
                    session,
                    user_id=self.user_id,
                    quote_public_id=intent.quote.public_id,
                )
            self.assertEqual(
                await session.scalar(
                    select(func.count(AccountLedgerEntry.id)).where(
                        AccountLedgerEntry.entry_type == "purchase_debit"
                    )
                ),
                0,
            )
            self.assertEqual(
                await session.scalar(select(func.count(EntitlementEntry.id))),
                0,
            )

    async def test_caught_failure_after_debit_rolls_back_savepoint(self):
        async with self.sessions.begin() as session:
            payment = await self.topup(session, 100)
            await credit_succeeded_topup(session, payment_id=payment.id)
            intent = await prepare_account_purchase(
                session, user_id=self.user_id, tariff_id=self.tariff_id
            )
            with patch(
                "services.account_purchase.SubscriptionService.extend_subscription",
                new=AsyncMock(side_effect=AccountPurchaseError("forced_failure")),
            ):
                with self.assertRaisesRegex(AccountPurchaseError, "forced_failure"):
                    await settle_account_purchase(
                        session,
                        user_id=self.user_id,
                        quote_public_id=intent.quote.public_id,
                    )
            self.assertEqual(
                await session.scalar(
                    select(func.count(AccountLedgerEntry.id)).where(
                        AccountLedgerEntry.entry_type == "purchase_debit"
                    )
                ),
                0,
            )
            self.assertEqual(
                await session.scalar(select(func.count(PaidValueLedgerEntry.id))),
                0,
            )
            self.assertEqual(
                await session.scalar(select(func.count(EntitlementEntry.id))),
                0,
            )

    async def test_price_change_is_rejected_before_account_debit(self):
        async with self.sessions.begin() as session:
            payment = await self.topup(session, 200)
            await credit_succeeded_topup(session, payment_id=payment.id)
            intent = await prepare_account_purchase(
                session, user_id=self.user_id, tariff_id=self.tariff_id
            )
            tariff = await session.get(Tariff, self.tariff_id)
            tariff.price_rub = 120
            with self.assertRaisesRegex(
                AccountPurchaseError, "tariff_price_changed"
            ):
                await settle_account_purchase(
                    session,
                    user_id=self.user_id,
                    quote_public_id=intent.quote.public_id,
                )
            self.assertEqual(
                await session.scalar(
                    select(func.count(AccountLedgerEntry.id)).where(
                        AccountLedgerEntry.entry_type == "purchase_debit"
                    )
                ),
                0,
            )

    async def test_two_purchase_confirmations_create_one_debit(self):
        async with self.sessions.begin() as session:
            payment = await self.topup(session, 100)
            await credit_succeeded_topup(session, payment_id=payment.id)
            intent = await prepare_account_purchase(
                session, user_id=self.user_id, tariff_id=self.tariff_id
            )
            public_id = intent.quote.public_id

        async def confirm():
            async with self.sessions.begin() as session:
                return await settle_account_purchase(
                    session,
                    user_id=self.user_id,
                    quote_public_id=public_id,
                )

        results = await asyncio.gather(confirm(), confirm())
        self.assertEqual(sum(result.created for result in results), 1)

    async def test_purchase_allocates_fifo_and_cannot_overdraw(self):
        async with self.sessions.begin() as session:
            first = await self.topup(session, 40)
            await credit_succeeded_topup(session, payment_id=first.id)
            second = await self.topup(session, 60)
            await credit_succeeded_topup(session, payment_id=second.id)
            quote = await self.quote(session, 70)
            debit, created = await create_purchase_debit(
                session,
                user_id=self.user_id,
                quote_id=quote.id,
                amount=70,
            )
            self.assertTrue(created)
            allocations = list(
                (
                    await session.scalars(
                        select(AccountLedgerAllocation)
                        .where(AccountLedgerAllocation.debit_entry_id == debit.id)
                        .order_by(AccountLedgerAllocation.id)
                    )
                ).all()
            )
            self.assertEqual(
                [item.amount for item in allocations],
                [Decimal("40.00"), Decimal("30.00")],
            )
            self.assertEqual(
                (await get_account_balance(session, user_id=self.user_id)).available,
                Decimal("30.00"),
            )

        async with self.sessions.begin() as session:
            another = await self.quote(session, 31, operation="renew")
            with self.assertRaises(InsufficientAccountBalanceError):
                await create_purchase_debit(
                    session,
                    user_id=self.user_id,
                    quote_id=another.id,
                    amount=31,
                )

    async def test_concurrent_duplicate_confirmation_creates_one_debit(self):
        async with self.sessions.begin() as session:
            topup = await self.topup(session, 100)
            await credit_succeeded_topup(session, payment_id=topup.id)
            quote = await self.quote(session, 100)
            quote_id = quote.id

        async def confirm():
            async with self.sessions.begin() as session:
                return await create_purchase_debit(
                    session,
                    user_id=self.user_id,
                    quote_id=quote_id,
                    amount=100,
                )

        results = await asyncio.gather(confirm(), confirm())
        self.assertEqual(sum(created for _, created in results), 1)
        async with self.sessions() as session:
            self.assertEqual(
                await session.scalar(
                    select(func.count(AccountLedgerEntry.id)).where(
                        AccountLedgerEntry.entry_type == "purchase_debit"
                    )
                ),
                1,
            )
            self.assertEqual(
                await session.scalar(select(func.count(AccountLedgerAllocation.id))),
                1,
            )

    async def test_reservation_is_unspendable_and_release_restores_balance(self):
        async with self.sessions.begin() as session:
            payment = await self.topup(session, 100)
            await credit_succeeded_topup(session, payment_id=payment.id)
            reservation, created = await reserve_payment_funds(
                session,
                payment_id=payment.id,
                reservation_type="refund",
                amount=60,
                idempotency_key=f"refund-reserve:{payment.id}:60",
            )
            self.assertTrue(created)
            snapshot = await get_account_balance(session, user_id=self.user_id)
            self.assertEqual(snapshot.available, Decimal("40.00"))
            self.assertEqual(snapshot.reserved, Decimal("60.00"))
            quote = await self.quote(session, 41)
            with self.assertRaises(InsufficientAccountBalanceError):
                await create_purchase_debit(
                    session,
                    user_id=self.user_id,
                    quote_id=quote.id,
                    amount=41,
                )
            await resolve_reservation(
                session, reservation_id=reservation.id, outcome="released"
            )
            snapshot = await get_account_balance(session, user_id=self.user_id)
            self.assertEqual(snapshot.available, Decimal("100.00"))
            self.assertEqual(snapshot.reserved, Decimal("0"))

    async def test_chargeback_debt_is_repaid_before_new_money_is_available(self):
        async with self.sessions.begin() as session:
            disputed = await self.topup(session, 50)
            await credit_succeeded_topup(session, payment_id=disputed.id)
            quote = await self.quote(session, 30)
            await create_purchase_debit(
                session,
                user_id=self.user_id,
                quote_id=quote.id,
                amount=30,
            )
            await create_payment_debit(
                session,
                payment_id=disputed.id,
                entry_type="chargeback_debit",
                amount=50,
                idempotency_key=f"chargeback:{disputed.id}",
            )
            snapshot = await get_account_balance(session, user_id=self.user_id)
            self.assertEqual(snapshot.available, Decimal("0"))
            self.assertEqual(snapshot.debt, Decimal("30.00"))
            repayment = await self.topup(session, 40)
            credit, _ = await credit_succeeded_topup(
                session, payment_id=repayment.id
            )
            self.assertEqual(credit.metadata_["debt_offset_rub"], "30.00")
            snapshot = await get_account_balance(session, user_id=self.user_id)
            self.assertEqual(snapshot.debt, Decimal("0"))
            self.assertEqual(snapshot.available, Decimal("10.00"))

    async def test_refundable_amount_tracks_allocations_and_reservations(self):
        async with self.sessions.begin() as session:
            payment = await self.topup(session, 100)
            await credit_succeeded_topup(session, payment_id=payment.id)
            quote = await self.quote(session, 35)
            await create_purchase_debit(
                session,
                user_id=self.user_id,
                quote_id=quote.id,
                amount=35,
            )
            self.assertEqual(
                await get_payment_refundable_amount(
                    session, payment_id=payment.id
                ),
                Decimal("65.00"),
            )
            await reserve_payment_funds(
                session,
                payment_id=payment.id,
                reservation_type="refund",
                amount=20,
                idempotency_key=f"refund-reserve:{payment.id}:20",
            )
            self.assertEqual(
                await get_payment_refundable_amount(
                    session, payment_id=payment.id
                ),
                Decimal("45.00"),
            )

    async def test_ledger_and_allocations_reject_update_and_delete(self):
        async with self.sessions.begin() as session:
            payment = await self.topup(session, 100)
            credit, _ = await credit_succeeded_topup(
                session, payment_id=payment.id
            )
            quote = await self.quote(session, 50)
            debit, _ = await create_purchase_debit(
                session,
                user_id=self.user_id,
                quote_id=quote.id,
                amount=50,
            )
            allocation_id = await session.scalar(
                select(AccountLedgerAllocation.id).where(
                    AccountLedgerAllocation.debit_entry_id == debit.id
                )
            )
            credit_id = credit.id

        with self.assertRaises(DBAPIError):
            async with self.sessions.begin() as session:
                await session.execute(
                    update(AccountLedgerEntry)
                    .where(AccountLedgerEntry.id == credit_id)
                    .values(amount=Decimal("101"))
                )
        with self.assertRaises(DBAPIError):
            async with self.sessions.begin() as session:
                await session.execute(
                    delete(AccountLedgerAllocation).where(
                        AccountLedgerAllocation.id == allocation_id
                    )
                )

    async def test_reservation_identity_is_immutable(self):
        async with self.sessions.begin() as session:
            payment = await self.topup(session, 100)
            await credit_succeeded_topup(session, payment_id=payment.id)
            reservation, _ = await reserve_payment_funds(
                session,
                payment_id=payment.id,
                reservation_type="dispute",
                amount=20,
                idempotency_key=f"dispute:{payment.id}",
            )
            reservation_id = reservation.id
        with self.assertRaises(DBAPIError):
            async with self.sessions.begin() as session:
                await session.execute(
                    update(AccountBalanceReservation)
                    .where(AccountBalanceReservation.id == reservation_id)
                    .values(amount=Decimal("21"))
                )


if __name__ == "__main__":
    unittest.main()
