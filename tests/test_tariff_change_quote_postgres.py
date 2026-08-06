"""Behavioral tariff-change quote tests against real PostgreSQL transactions."""

import asyncio
import os
import unittest
import uuid
from unittest.mock import AsyncMock, patch
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from database.models import (
    PaidValueLedgerEntry,
    Payment,
    PaymentProviderOperation,
    Tariff,
    TariffQuote,
    User,
    AccountLedgerEntry,
    EntitlementEntry,
)
from database.repositories.account_ledger_repo import create_admin_adjustment
from services.account_tariff_change import (
    AccountTariffChangeError,
    settle_account_tariff_change,
)
from services.tariff_change_quote import create_tariff_change_quote
from services.subscription_balance_service import get_subscription_balance_snapshot
from database.repositories.tariff_quotes_repo import (
    get_or_create_checkout_quote,
    lock_checkout_user,
)
from utils.datetime_helpers import now_utc

DB = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not configured")
class TariffChangeQuotePostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(DB)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE paid_value_ledger, tariff_quotes, tariff_versions, "
                    "entitlement_entries, payments, users, tariffs RESTART IDENTITY CASCADE"
                )
            )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def seed(self, *, tracked=True, active=True, current=True, duration_days=30):
        as_of = now_utc().replace(microsecond=0)
        async with self.sessions.begin() as session:
            source = (
                await session.execute(
                    text(
                        "INSERT INTO tariffs(name,duration_days,device_limit,price_rub,is_active,sort_order,created_at) "
                        "VALUES('source',:duration_days,2,90,true,1,:created_at) RETURNING id"
                    ),
                    {"duration_days": duration_days, "created_at": as_of},
                )
            ).scalar_one()
            target = (
                await session.execute(
                    text(
                        "INSERT INTO tariffs(name,duration_days,device_limit,price_rub,is_active,sort_order,created_at) "
                        "VALUES('target',:duration_days,5,180,true,2,:created_at) RETURNING id"
                    ),
                    {"duration_days": duration_days, "created_at": as_of},
                )
            ).scalar_one()
            user = (
                await session.execute(
                    text(
                        "INSERT INTO users(telegram_id,subscription_end,device_limit,current_tariff_id,"
                        "is_banned,is_bot_blocked,is_deleted,notification_retry_count,"
                        "notified_3d,notified_1d,notified_2h,notified_expired,notified_grace_12h,"
                        "device_creations_today,created_at) "
                        "VALUES(:tg,:end,2,:tariff,false,false,false,0,false,false,false,false,false,0,:created_at) "
                        "RETURNING id"
                    ),
                    {
                        "tg": uuid.uuid4().int % 10**12,
                        "end": as_of + timedelta(days=30) if active else as_of,
                        "tariff": source if current else None,
                        "created_at": as_of,
                    },
                )
            ).scalar_one()
            version = (
                await session.execute(
                    text(
                        "INSERT INTO tariff_versions(tariff_id,version_number,name_snapshot,duration_hours,"
                        "device_limit,price_rub,currency) VALUES(:t,1,'source',720,2,90,'RUB') RETURNING id"
                    ),
                    {"t": source},
                )
            ).scalar_one()
            if tracked:
                consumed = (
                    await session.execute(
                        text(
                            "INSERT INTO tariff_quotes(public_id,user_id,operation_type,target_tariff_version_id,"
                            "current_paid_hours,current_paid_value_rub,bonus_hours,amount_due_rub,"
                            "resulting_paid_hours,resulting_paid_value_rub,resulting_bonus_hours,rounding_loss_hours,"
                            "rounding_loss_value_rub,currency,status,expires_at,created_at,consumed_at) "
                            "VALUES(:p,:u,'purchase',:v,0,0,0,90,720,90,0,0,0,'RUB','consumed',:x,:n,:n) RETURNING id"
                        ),
                        {
                            "p": uuid.uuid4(),
                            "u": user,
                            "v": version,
                            "x": as_of + timedelta(minutes=15),
                            "n": as_of,
                        },
                    )
                ).scalar_one()
                await session.execute(
                    text(
                        "INSERT INTO paid_value_ledger(user_id,source_type,source_id,entry_type,paid_hours_delta,"
                        "paid_value_rub_delta,currency,tariff_version_id,quote_id,metadata,created_at) "
                        "VALUES(:u,'quote',:source,'account_purchase',720,90,'RUB',:v,:q,'{}',:n)"
                    ),
                    {
                        "u": user,
                        "source": str(consumed),
                        "v": version,
                        "q": consumed,
                        "n": as_of,
                    },
                )
                await session.execute(
                    text(
                        "INSERT INTO entitlement_entries(beneficiary_user_id,source_type,source_id,entry_type,"
                        "days_delta,hours_delta,device_limit_snapshot,tariff_id_snapshot,metadata,created_at) "
                        "VALUES(:u,'quote',:q,'account_purchase_grant',30,720,2,:t,'{}',:n)"
                    ),
                    {"u": user, "q": str(consumed), "t": source, "n": as_of},
                )
        return user, source, target, as_of

    async def test_service_freezes_projector_snapshot_and_has_no_side_effects(self):
        user, source, target, as_of = await self.seed()
        async with self.sessions.begin() as session:
            before = (await session.get(User, user)).subscription_end
            result = await create_tariff_change_quote(
                session, user_id=user, target_tariff_id=target, as_of=as_of
            )
            self.assertTrue(result.created)
            quote = result.quote
            self.assertEqual(
                (quote.created_at, quote.expires_at),
                (as_of, as_of + timedelta(minutes=15)),
            )
            self.assertEqual(
                (quote.current_paid_hours, quote.current_paid_value_rub),
                (720, Decimal("90")),
            )
            self.assertEqual(
                (quote.amount_due_rub, quote.resulting_bonus_hours),
                (Decimal("90"), 0),
            )
            self.assertIsNotNone(quote.source_tariff_version_id)
            self.assertNotEqual(
                quote.source_tariff_version_id, quote.target_tariff_version_id
            )
            self.assertEqual((await session.get(User, user)).subscription_end, before)
            self.assertEqual(await session.scalar(select(func.count(Payment.id))), 0)
            self.assertEqual(
                await session.scalar(select(func.count(PaymentProviderOperation.id))), 0
            )

    async def test_same_target_reuses_frozen_quote_until_source_history_changes(self):
        user, _, target, as_of = await self.seed()
        async with self.sessions.begin() as session:
            first = await create_tariff_change_quote(
                session, user_id=user, target_tariff_id=target, as_of=as_of
            )
            again = await create_tariff_change_quote(
                session,
                user_id=user,
                target_tariff_id=target,
                as_of=as_of + timedelta(minutes=1),
            )
            self.assertFalse(again.created)
            self.assertEqual(first.quote.id, again.quote.id)
            await session.execute(
                text(
                    "INSERT INTO entitlement_entries(beneficiary_user_id,source_type,source_id,entry_type,days_delta,"
                    "metadata,created_at) VALUES(:u,'manual','new','manual_grant',1,'{}',:n)"
                ),
                {"u": user, "n": as_of + timedelta(seconds=1)},
            )
            await session.execute(
                text(
                    "UPDATE users SET subscription_end=subscription_end+interval '1 day' WHERE id=:u"
                ),
                {"u": user},
            )
            refreshed = await create_tariff_change_quote(
                session,
                user_id=user,
                target_tariff_id=target,
                as_of=as_of + timedelta(minutes=2),
            )
            self.assertTrue(refreshed.created)
            self.assertNotEqual(first.quote.id, refreshed.quote.id)
            self.assertEqual(first.quote.status, "cancelled")
            self.assertEqual(first.quote.diagnostic_reason, "source_balance_changed")

    async def test_untracked_projection_cancels_unbound_active_quote(self):
        user, _, target, as_of = await self.seed()
        async with self.sessions.begin() as session:
            first = await create_tariff_change_quote(
                session, user_id=user, target_tariff_id=target, as_of=as_of
            )
            await session.execute(
                text(
                    "INSERT INTO paid_value_ledger("
                    "user_id,source_type,source_id,entry_type,paid_hours_delta,"
                    "paid_value_rub_delta,currency,tariff_version_id,metadata,created_at"
                    ") SELECT user_id,'manual',:source,'manual_adjustment',1,1,'RUB',"
                    "tariff_version_id,'{}',:created_at FROM paid_value_ledger "
                    "WHERE user_id=:u AND entry_type='account_purchase' LIMIT 1"
                ),
                {
                    "u": user,
                    "source": uuid.uuid4().hex,
                    "created_at": as_of + timedelta(seconds=1),
                },
            )
            snapshot = await get_subscription_balance_snapshot(
                session, user_id=user, as_of=as_of + timedelta(minutes=1)
            )
            self.assertFalse(snapshot.tracked)
            repeated = await create_tariff_change_quote(
                session,
                user_id=user,
                target_tariff_id=target,
                as_of=as_of + timedelta(minutes=1),
            )
            self.assertIsNone(repeated.quote)
            self.assertEqual(repeated.failure_code, "subscription_balance_untracked")
            self.assertEqual(
                (first.quote.status, first.quote.diagnostic_reason),
                ("cancelled", "source_balance_untracked"),
            )
            self.assertEqual(
                await session.scalar(
                    select(func.count(TariffQuote.id)).where(
                        TariffQuote.operation_type == "change"
                    )
                ),
                1,
            )

    async def test_conflicts_expiry_and_closed_preconditions(self):
        user, source, target, as_of = await self.seed()
        async with self.sessions.begin() as session:
            first = await create_tariff_change_quote(
                session, user_id=user, target_tariff_id=target, as_of=as_of
            )
            self.assertTrue(first.created)
            different = (
                await session.execute(
                    text(
                        "INSERT INTO tariffs(name,duration_days,device_limit,price_rub,is_active,sort_order,created_at) "
                        "VALUES('other',30,6,200,true,3,:created_at) RETURNING id"
                    ),
                    {"created_at": as_of},
                )
            ).scalar_one()
            conflict = await create_tariff_change_quote(
                session, user_id=user, target_tariff_id=different, as_of=as_of
            )
            self.assertTrue(conflict.created)
            
            # The first quote should now be cancelled
            first_reloaded = await session.get(TariffQuote, first.quote.id)
            self.assertEqual(first_reloaded.status, "cancelled")

            # But trying to create one for the same target should return the existing one
            same_target = await create_tariff_change_quote(
                session, user_id=user, target_tariff_id=different, as_of=as_of
            )
            self.assertFalse(same_target.created)
            self.assertEqual(same_target.quote.id, conflict.quote.id)
        legacy, _, legacy_target, legacy_as_of = await self.seed(
            tracked=False, duration_days=31
        )
        async with self.sessions.begin() as session:
            result = await create_tariff_change_quote(
                session,
                user_id=legacy,
                target_tariff_id=legacy_target,
                as_of=legacy_as_of,
            )
            self.assertEqual(result.failure_code, "subscription_balance_untracked")

    async def test_concurrent_same_target_serializes_to_one_quote(self):
        user, _, target, as_of = await self.seed()

        async def create():
            async with self.sessions.begin() as session:
                return await create_tariff_change_quote(
                    session, user_id=user, target_tariff_id=target, as_of=as_of
                )

        first, second = await asyncio.gather(create(), create())
        self.assertEqual(first.quote.id, second.quote.id)
        self.assertEqual(sorted((first.created, second.created)), [False, True])

    async def test_renew_first_blocks_change(self):
        user, source, target, as_of = await self.seed()
        async with self.sessions.begin() as session:
            await lock_checkout_user(session, user)
            source_tariff = await session.get(Tariff, source)
            await get_or_create_checkout_quote(
                session, user_id=user, tariff=source_tariff, operation_type="renew"
            )
        async with self.sessions.begin() as session:
            result = await create_tariff_change_quote(
                session, user_id=user, target_tariff_id=target, as_of=as_of
            )
            self.assertEqual(result.failure_code, "active_checkout_exists")
            self.assertEqual(
                await session.scalar(
                    select(func.count(TariffQuote.id)).where(
                        TariffQuote.status == "active"
                    )
                ),
                1,
            )

    async def test_change_first_blocks_balance_purchase_before_side_effects(self):
        from services.account_purchase import AccountPurchaseError, prepare_account_purchase

        user, source, target, as_of = await self.seed()
        async with self.sessions.begin() as session:
            change = await create_tariff_change_quote(
                session, user_id=user, target_tariff_id=target, as_of=as_of
            )
            self.assertTrue(change.created)
            before_payments = await session.scalar(select(func.count(Payment.id)))
            with self.assertRaisesRegex(
                AccountPurchaseError, "active_tariff_change_quote_exists"
            ):
                await prepare_account_purchase(
                    session, user_id=user, tariff_id=source
                )
            self.assertEqual(
                await session.scalar(select(func.count(Payment.id))), before_payments
            )
            self.assertEqual(
                await session.scalar(select(func.count(PaymentProviderOperation.id))), 0
            )
            self.assertEqual(
                await session.scalar(
                    select(func.count(TariffQuote.id)).where(
                        TariffQuote.status == "active"
                    )
                ),
                1,
            )

    async def test_database_constraints_and_lifecycle_graph(self):
        user, _, target, as_of = await self.seed()
        async with self.sessions.begin() as session:
            result = await create_tariff_change_quote(
                session, user_id=user, target_tariff_id=target, as_of=as_of
            )
            quote_id = result.quote.id
            await session.execute(
                text(
                    "UPDATE tariff_quotes SET status='consumed',consumed_at=:n WHERE id=:q"
                ),
                {"q": quote_id, "n": as_of},
            )
            with self.assertRaises(DBAPIError):
                async with session.begin_nested():
                    await session.execute(
                        text(
                            "UPDATE tariff_quotes SET status='active',consumed_at=NULL WHERE id=:q"
                        ),
                        {"q": quote_id},
                    )
            await session.execute(
                text(
                    "UPDATE tariff_quotes SET status='manual_review',manual_review_at=:n WHERE id=:q"
                ),
                {"q": quote_id, "n": as_of + timedelta(seconds=1)},
            )
            row = await session.get(TariffQuote, quote_id)
            await session.refresh(row)
            self.assertEqual(row.consumed_at, as_of)
            with self.assertRaises(DBAPIError):
                async with session.begin_nested():
                    await session.execute(
                        text(
                            "UPDATE tariff_quotes SET status='active',consumed_at=NULL,manual_review_at=NULL WHERE id=:q"
                        ),
                        {"q": quote_id},
                    )

    async def test_source_id_array_validator_contract(self):
        cases = {
            "[]": True,
            "[0,1,2]": True,
            "[-1]": False,
            "[1.5]": False,
            "[null]": False,
            '["1"]': False,
            "{}": False,
            "null": False,
        }
        async with self.sessions() as session:
            for raw, expected in cases.items():
                with self.subTest(raw=raw):
                    actual = await session.scalar(
                        text(
                            "SELECT is_nonnegative_integer_json_array(CAST(:value AS jsonb))"
                        ),
                        {"value": raw},
                    )
                    self.assertIs(actual, expected)

    async def test_outer_rollback_removes_quote(self):
        user, _, target, as_of = await self.seed()
        async with self.sessions() as session:
            transaction = await session.begin()
            result = await create_tariff_change_quote(
                session, user_id=user, target_tariff_id=target, as_of=as_of
            )
            quote_id = result.quote.id
            await transaction.rollback()
        async with self.sessions() as session:
            self.assertIsNone(await session.get(TariffQuote, quote_id))

    async def test_balance_settlement_is_atomic_and_idempotent(self):
        user, _, target, as_of = await self.seed()
        async with self.sessions.begin() as session:
            await create_admin_adjustment(
                session,
                user_id=user,
                signed_amount=90,
                idempotency_key=f"test-change-funds:{user}",
                metadata={"test": True},
            )
            quote = (
                await create_tariff_change_quote(
                    session, user_id=user, target_tariff_id=target, as_of=as_of
                )
            ).quote
            first = await settle_account_tariff_change(
                session, user_id=user, quote_public_id=quote.public_id
            )
            repeated = await settle_account_tariff_change(
                session, user_id=user, quote_public_id=quote.public_id
            )
            self.assertTrue(first.created)
            self.assertFalse(repeated.created)
            self.assertEqual(first.debit.amount, Decimal("-90"))
            self.assertEqual(first.quote.status, "consumed")
            self.assertEqual(first.entitlement.hours_delta, 720)
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
                    select(func.count(EntitlementEntry.id)).where(
                        EntitlementEntry.entry_type == "tariff_change",
                        EntitlementEntry.source_id == str(quote.id),
                    )
                ),
                1,
            )
            self.assertEqual(
                await session.scalar(select(func.count(PaymentProviderOperation.id))),
                0,
            )

    async def test_zero_due_change_needs_no_account_debit(self):
        user, _, target, as_of = await self.seed()
        async with self.sessions.begin() as session:
            tariff = await session.get(Tariff, target)
            tariff.price_rub = 45
            quote = (
                await create_tariff_change_quote(
                    session, user_id=user, target_tariff_id=target, as_of=as_of
                )
            ).quote
            self.assertEqual(quote.amount_due_rub, 0)
            result = await settle_account_tariff_change(
                session, user_id=user, quote_public_id=quote.public_id
            )
            self.assertIsNone(result.debit)
            self.assertEqual(result.quote.status, "consumed")
            self.assertEqual(result.balance_after.available, 0)

    async def test_caught_failure_after_change_debit_rolls_back_everything(self):
        user, _, target, as_of = await self.seed()
        async with self.sessions.begin() as session:
            await create_admin_adjustment(
                session,
                user_id=user,
                signed_amount=90,
                idempotency_key=f"test-change-rollback-funds:{user}",
                metadata={"test": True},
            )
            quote = (
                await create_tariff_change_quote(
                    session, user_id=user, target_tariff_id=target, as_of=as_of
                )
            ).quote
            with patch(
                "services.account_tariff_change.SubscriptionService.replace_subscription",
                new=AsyncMock(side_effect=ValueError("forced failure")),
            ):
                with self.assertRaisesRegex(
                    AccountTariffChangeError, "subscription_state_changed"
                ):
                    await settle_account_tariff_change(
                        session, user_id=user, quote_public_id=quote.public_id
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
                await session.scalar(
                    select(func.count(PaidValueLedgerEntry.id)).where(
                        PaidValueLedgerEntry.entry_type == "tariff_conversion"
                    )
                ),
                0,
            )
            self.assertEqual(
                await session.scalar(
                    select(func.count(EntitlementEntry.id)).where(
                        EntitlementEntry.entry_type == "tariff_change"
                    )
                ),
                0,
            )

    async def test_source_history_change_is_rejected_before_debit(self):
        user, _, target, as_of = await self.seed()
        async with self.sessions.begin() as session:
            await create_admin_adjustment(
                session,
                user_id=user,
                signed_amount=90,
                idempotency_key=f"test-change-stale-funds:{user}",
                metadata={"test": True},
            )
            quote = (
                await create_tariff_change_quote(
                    session, user_id=user, target_tariff_id=target, as_of=as_of
                )
            ).quote
            session.add(
                EntitlementEntry(
                    beneficiary_user_id=user,
                    source_type="manual",
                    source_id="changed-after-quote",
                    entry_type="manual_grant",
                    days_delta=1,
                    hours_delta=24,
                    metadata_={},
                    created_at=as_of + timedelta(seconds=1),
                )
            )
            db_user = await session.get(User, user)
            db_user.subscription_end += timedelta(days=1)
            with self.assertRaisesRegex(
                AccountTariffChangeError, "quote_source_history_changed"
            ):
                await settle_account_tariff_change(
                    session, user_id=user, quote_public_id=quote.public_id
                )
            self.assertEqual(
                await session.scalar(
                    select(func.count(AccountLedgerEntry.id)).where(
                        AccountLedgerEntry.entry_type == "purchase_debit"
                    )
                ),
                0,
            )


if __name__ == "__main__":
    unittest.main()
