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
from alembic.command import downgrade, upgrade
from alembic.config import Config

from database.models import (
    PaidValueLedgerEntry,
    Payment,
    PaymentFulfillmentOperation,
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
from services.tariff_change_payment import (
    TariffChangePaymentResult,
    create_tariff_change_payment,
)
from services.subscription_balance_service import get_subscription_balance_snapshot
from services.payment_service.service import PaymentService
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
                        "referral_days,is_banned,is_bot_blocked,is_deleted,notification_retry_count,"
                        "notified_3d,notified_1d,notified_2h,notified_expired,notified_grace_12h,"
                        "device_creations_today,created_at) "
                        "VALUES(:tg,:end,2,:tariff,0,false,false,false,0,false,false,false,false,false,0,:created_at) "
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
                            "current_paid_hours,current_paid_value_rub,bonus_hours,confirmed_payment_required_rub,"
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
                payment = (
                    await session.execute(
                        text(
                            "INSERT INTO payments(user_id,tariff_id,tariff_quote_id,tariff_version_id,amount,currency,"
                            "status,provider_status,fulfillment_status,reconciliation_status,checkout_status,"
                            "snapshot_duration_days,snapshot_device_limit,snapshot_amount,snapshot_currency,"
                            "referral_user_bonus_days,referral_referrer_bonus_days,created_at,updated_at) "
                            "VALUES(:u,:t,:q,:v,90,'RUB','completed','succeeded','succeeded','ok','abandoned',"
                            "30,2,90,'RUB',0,0,:n,:n) RETURNING id"
                        ),
                        {
                            "u": user,
                            "t": source,
                            "q": consumed,
                            "v": version,
                            "n": as_of,
                        },
                    )
                ).scalar_one()
                await session.execute(
                    text("UPDATE tariff_quotes SET payment_id=:p WHERE id=:q"),
                    {"p": payment, "q": consumed},
                )
                await session.execute(
                    text(
                        "INSERT INTO paid_value_ledger(user_id,source_type,source_id,entry_type,paid_hours_delta,"
                        "paid_value_rub_delta,currency,tariff_version_id,quote_id,payment_id,metadata,created_at) "
                        "VALUES(:u,'payment',:source,'confirmed_payment',720,90,'RUB',:v,:q,:p,'{}',:n)"
                    ),
                    {
                        "u": user,
                        "source": str(payment),
                        "v": version,
                        "q": consumed,
                        "p": payment,
                        "n": as_of,
                    },
                )
                await session.execute(
                    text(
                        "INSERT INTO entitlement_entries(beneficiary_user_id,source_type,source_id,entry_type,"
                        "days_delta,device_limit_snapshot,tariff_id_snapshot,metadata,created_at) "
                        "VALUES(:u,'payment',:p,'payment_grant',30,2,:t,'{}',:n)"
                    ),
                    {"u": user, "p": str(payment), "t": source, "n": as_of},
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
                (quote.confirmed_payment_required_rub, quote.resulting_bonus_hours),
                (Decimal("90"), 0),
            )
            self.assertIsNotNone(quote.source_tariff_version_id)
            self.assertNotEqual(
                quote.source_tariff_version_id, quote.target_tariff_version_id
            )
            self.assertEqual((await session.get(User, user)).subscription_end, before)
            self.assertEqual(await session.scalar(select(func.count(Payment.id))), 1)
            self.assertEqual(
                await session.scalar(select(func.count(PaymentProviderOperation.id))), 0
            )
            self.assertEqual(
                await session.scalar(
                    select(func.count(PaymentFulfillmentOperation.id))
                ),
                0,
            )

    async def test_phase6_positive_intent_is_typed_frozen_and_idempotent(self):
        user, _, target, as_of = await self.seed()
        async with self.sessions.begin() as session:
            quote_result = await create_tariff_change_quote(
                session, user_id=user, target_tariff_id=target, as_of=as_of
            )
            quote_id = quote_result.quote.public_id
            await session.execute(
                text("UPDATE tariffs SET price_rub=999999 WHERE id=:t"), {"t": target}
            )
            first = await create_tariff_change_payment(
                session,
                user_id=user,
                quote_public_id=quote_id,
                bot_username="bot",
                as_of=as_of,
            )
            again = await create_tariff_change_payment(
                session,
                user_id=user,
                quote_public_id=str(quote_id),
                bot_username="bot",
                as_of=as_of,
            )
            self.assertIsInstance(first, TariffChangePaymentResult)
            self.assertTrue(first.created)
            self.assertFalse(again.created)
            self.assertEqual(first.payment.id, again.payment.id)
            self.assertEqual(first.provider_operation.id, again.provider_operation.id)
            self.assertEqual(
                first.payment.amount, quote_result.quote.confirmed_payment_required_rub
            )
            self.assertEqual(first.payment.currency, "RUB")
            self.assertEqual(
                first.provider_operation.idempotency_key,
                first.payment.provider_idempotency_key,
            )
            self.assertEqual(
                first.provider_operation.payload["metadata"],
                {
                    "order_id": first.payment.public_order_id,
                    "local_payment_id": str(first.payment.id),
                },
            )

    async def test_phase6_retry_after_expiry_returns_immutable_intent(self):
        user, _, target, as_of = await self.seed()
        async with self.sessions.begin() as session:
            quote = (
                await create_tariff_change_quote(
                    session, user_id=user, target_tariff_id=target, as_of=as_of
                )
            ).quote
            first = await create_tariff_change_payment(
                session,
                user_id=user,
                quote_public_id=quote.public_id,
                bot_username="bot",
                as_of=as_of,
            )
            payment_id, operation_id, key = (
                first.payment.id,
                first.provider_operation.id,
                first.payment.provider_idempotency_key,
            )
        async with self.sessions.begin() as session:
            repeated = await create_tariff_change_payment(
                session,
                user_id=user,
                quote_public_id=quote.public_id,
                bot_username="different_bot",
                as_of=quote.expires_at + timedelta(seconds=1),
            )
            self.assertFalse(repeated.created)
            self.assertEqual(
                (
                    repeated.payment.id,
                    repeated.provider_operation.id,
                    repeated.payment.provider_idempotency_key,
                ),
                (payment_id, operation_id, key),
            )

    async def test_quote_less_legacy_unknown_payment_blocks_change_quote(self):
        user, source, target, as_of = await self.seed()
        async with self.sessions.begin() as session:
            await session.execute(
                text(
                    "INSERT INTO payments(user_id,tariff_id,amount,currency,status,provider_status,fulfillment_status,reconciliation_status,checkout_status,referral_user_bonus_days,referral_referrer_bonus_days,created_at,updated_at,provider_required) VALUES(:u,:t,90,'RUB','pending','unknown','not_ready','required','abandoned',0,0,:n,:n,true)"
                ),
                {"u": user, "t": source, "n": as_of},
            )
        async with self.sessions.begin() as session:
            result = await create_tariff_change_quote(
                session, user_id=user, target_tariff_id=target, as_of=as_of
            )
            self.assertEqual(result.failure_code, "unfinished_checkout_exists")

    async def test_phase6_malformed_uuid_and_exact_expiry_fail_closed(self):
        user, _, target, as_of = await self.seed()
        async with self.sessions.begin() as session:
            malformed = await create_tariff_change_payment(
                session,
                user_id=user,
                quote_public_id="not-a-uuid",
                bot_username="bot",
                as_of=as_of,
            )
            self.assertEqual(malformed.failure_code, "quote_public_id_invalid")
            quote = (
                await create_tariff_change_quote(
                    session, user_id=user, target_tariff_id=target, as_of=as_of
                )
            ).quote
            expired = await create_tariff_change_payment(
                session,
                user_id=user,
                quote_public_id=quote.public_id,
                bot_username="bot",
                as_of=quote.expires_at,
            )
            self.assertEqual(expired.failure_code, "quote_expired")
            self.assertEqual(await session.scalar(select(func.count(Payment.id))), 1)

    async def test_phase6_abandoned_ambiguous_purchase_still_conflicts(self):
        user, source, target, as_of = await self.seed()
        async with self.sessions.begin() as session:
            change = (
                await create_tariff_change_quote(
                    session, user_id=user, target_tariff_id=target, as_of=as_of
                )
            ).quote
            source_version = await session.scalar(
                text("SELECT id FROM tariff_versions WHERE tariff_id=:t"), {"t": source}
            )
            old_quote = (
                await session.execute(
                    text(
                        "INSERT INTO tariff_quotes(public_id,user_id,operation_type,target_tariff_version_id,current_paid_hours,current_paid_value_rub,bonus_hours,confirmed_payment_required_rub,resulting_paid_hours,resulting_paid_value_rub,resulting_bonus_hours,rounding_loss_hours,rounding_loss_value_rub,currency,status,expires_at,created_at,consumed_at) VALUES(:x,:u,'renew',:v,0,0,0,90,720,90,0,0,0,'RUB','consumed',:e,:n,:n) RETURNING id"
                    ),
                    {
                        "x": uuid.uuid4(),
                        "u": user,
                        "v": source_version,
                        "e": as_of + timedelta(minutes=15),
                        "n": as_of,
                    },
                )
            ).scalar_one()
            payment = (
                await session.execute(
                    text(
                        "INSERT INTO payments(user_id,tariff_id,tariff_quote_id,tariff_version_id,amount,currency,status,provider_status,fulfillment_status,reconciliation_status,checkout_status,snapshot_amount,snapshot_currency,referral_user_bonus_days,referral_referrer_bonus_days,created_at,updated_at,provider_required) VALUES(:u,:t,:q,:v,90,'RUB','pending','unknown','not_ready','required','abandoned',90,'RUB',0,0,:n,:n,true) RETURNING id"
                    ),
                    {
                        "u": user,
                        "t": source,
                        "q": old_quote,
                        "v": source_version,
                        "n": as_of,
                    },
                )
            ).scalar_one()
            await session.execute(
                text("UPDATE tariff_quotes SET payment_id=:p WHERE id=:q"),
                {"p": payment, "q": old_quote},
            )
            result = await create_tariff_change_payment(
                session,
                user_id=user,
                quote_public_id=change.public_id,
                bot_username="bot",
                as_of=as_of,
            )
            self.assertEqual(result.failure_code, "unfinished_checkout_exists")

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
                    "UPDATE payments SET snapshot_amount=snapshot_amount+1 WHERE id=("
                    "SELECT payment_id FROM paid_value_ledger WHERE user_id=:u AND entry_type='confirmed_payment')"
                ),
                {"u": user},
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

    async def test_change_quote_cannot_bind_an_unrelated_payment(self):
        user, _, target, as_of = await self.seed()
        async with self.sessions.begin() as session:
            first = await create_tariff_change_quote(
                session, user_id=user, target_tariff_id=target, as_of=as_of
            )
            source_payment_id = await session.scalar(
                select(PaidValueLedgerEntry.payment_id).where(
                    PaidValueLedgerEntry.user_id == user,
                    PaidValueLedgerEntry.entry_type == "confirmed_payment",
                )
            )
            with self.assertRaises(DBAPIError):
                async with session.begin_nested():
                    first.quote.payment_id = source_payment_id
                    await session.flush()
                    await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

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
            self.assertEqual(conflict.failure_code, "active_change_quote_exists")
            after_expiry = await create_tariff_change_quote(
                session,
                user_id=user,
                target_tariff_id=different,
                as_of=as_of + timedelta(minutes=15),
            )
            self.assertTrue(after_expiry.created)
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
        ready = asyncio.Event()

        async def create():
            async with self.sessions.begin() as session:
                ready.set()
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

    async def test_change_first_blocks_payment_flow_before_side_effects(self):
        user, source, target, as_of = await self.seed()
        async with self.sessions.begin() as session:
            change = await create_tariff_change_quote(
                session, user_id=user, target_tariff_id=target, as_of=as_of
            )
            self.assertTrue(change.created)
            before_payments = await session.scalar(select(func.count(Payment.id)))
            payment, failure = await PaymentService.create_yookassa_payment(
                session, user, source, Decimal("90"), 1, "bot"
            )
            self.assertIsNone(payment)
            self.assertEqual(failure, "active_tariff_change_quote_exists")
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
            self.assertEqual(quote.confirmed_payment_required_rub, 0)
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


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not configured")
class TariffChangeQuoteMigrationPostgresTests(unittest.IsolatedAsyncioTestCase):
    def config(self):
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", DB)
        return config

    async def test_initial_upgrade_quarantines_legacy_change_quote(self):
        config = self.config()
        await asyncio.to_thread(downgrade, config, "7a4f19c82d11")
        engine = create_async_engine(DB)
        try:
            now = now_utc().replace(microsecond=0)
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "TRUNCATE paid_value_ledger, tariff_quotes, tariff_versions, entitlement_entries, "
                        "payments, users, tariffs RESTART IDENTITY CASCADE"
                    )
                )
                tariff = (
                    await connection.execute(
                        text(
                            "INSERT INTO tariffs(name,duration_days,device_limit,price_rub,is_active,sort_order,created_at) "
                            "VALUES('legacy',30,2,90,true,1,:n) RETURNING id"
                        ),
                        {"n": now},
                    )
                ).scalar_one()
                user = (
                    await connection.execute(
                        text(
                            "INSERT INTO users(telegram_id,device_limit,referral_days,is_banned,is_bot_blocked,is_deleted,"
                            "notification_retry_count,notified_3d,notified_1d,notified_2h,notified_expired,"
                            "notified_grace_12h,device_creations_today,created_at) "
                            "VALUES(:tg,2,0,false,false,false,0,false,false,false,false,false,0,:n) RETURNING id"
                        ),
                        {"tg": uuid.uuid4().int % 10**12, "n": now},
                    )
                ).scalar_one()
                version = (
                    await connection.execute(
                        text(
                            "INSERT INTO tariff_versions(tariff_id,version_number,name_snapshot,duration_hours,"
                            "device_limit,price_rub,currency,created_at) VALUES(:t,1,'legacy',720,2,90,'RUB',:n) "
                            "RETURNING id"
                        ),
                        {"t": tariff, "n": now},
                    )
                ).scalar_one()
                quote_id = (
                    await connection.execute(
                        text(
                            "INSERT INTO tariff_quotes(public_id,user_id,operation_type,target_tariff_version_id,"
                            "current_paid_hours,current_paid_value_rub,bonus_hours,confirmed_payment_required_rub,"
                            "resulting_paid_hours,resulting_paid_value_rub,resulting_bonus_hours,rounding_loss_hours,"
                            "rounding_loss_value_rub,currency,status,expires_at,created_at) "
                            "VALUES(:p,:u,'change',:v,0,0,0,0,0,0,0,0,0,'RUB','active',:x,:n) RETURNING id"
                        ),
                        {
                            "p": uuid.uuid4(),
                            "u": user,
                            "v": version,
                            "x": now + timedelta(minutes=15),
                            "n": now,
                        },
                    )
                ).scalar_one()
            await engine.dispose()
            await asyncio.to_thread(upgrade, config, "head")
            engine = create_async_engine(DB)
            async with engine.connect() as connection:
                row = (
                    await connection.execute(
                        text(
                            "SELECT status,manual_review_at,diagnostic_reason,balance_as_of,"
                            "source_subscription_end,source_balance_fingerprint,source_entitlement_entry_ids,"
                            "source_ledger_entry_ids FROM tariff_quotes WHERE id=:q"
                        ),
                        {"q": quote_id},
                    )
                ).one()
                self.assertEqual(row.status, "manual_review")
                self.assertIsNotNone(row.manual_review_at)
                self.assertEqual(
                    row.diagnostic_reason, "legacy_change_quote_source_snapshot_missing"
                )
                self.assertTrue(all(value is None for value in row[3:]))
        finally:
            await engine.dispose()
            await asyncio.to_thread(upgrade, config, "head")


if __name__ == "__main__":
    unittest.main()
