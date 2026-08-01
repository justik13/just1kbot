"""PostgreSQL contract tests for the read-only balance boundary."""

import asyncio
import os
import unittest
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import (
    EntitlementEntry,
    PaidValueLedgerEntry,
    Payment,
    PaymentFulfillmentOperation,
    Tariff,
    TariffQuote,
    TariffVersion,
    User,
)
from services.payment_fulfillment import grant, referral, reverse
from services.subscription_balance_service import get_subscription_balance_snapshot
from utils.datetime_helpers import now_utc

DB = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class SubscriptionBalancePostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(DB)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as c:
            await c.execute(
                text(
                    "TRUNCATE paid_value_ledger, tariff_quotes, tariff_versions, entitlement_entries, payments, users, tariffs RESTART IDENTITY CASCADE"
                )
            )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def seed(self, *, bonus=False, active=True, ledger=True, count=1):
        as_of = now_utc().replace(microsecond=0)
        grant_created_at = as_of if active else as_of - timedelta(days=31)
        end = (
            (grant_created_at + timedelta(days=30 * count))
            if not active
            else as_of + timedelta(days=30 * count)
        )
        async with self.sessions.begin() as s:
            tariff = (
                await s.execute(
                    text(
                        "INSERT INTO tariffs(name,duration_days,device_limit,price_rub,is_active,sort_order,created_at) VALUES('T',30,2,90,true,0,:n) RETURNING id"
                    ),
                    {"n": grant_created_at},
                )
            ).scalar_one()
            user = (
                await s.execute(
                    text(
                        "INSERT INTO users(telegram_id,device_limit,referral_days,is_banned,is_bot_blocked,is_deleted,notification_retry_count,created_at,notified_3d,notified_1d,notified_2h,notified_expired,notified_grace_12h,device_creations_today,subscription_end) VALUES(:tg,2,0,false,false,false,0,:n,false,false,false,false,false,0,:e) RETURNING id"
                    ),
                    {"tg": uuid.uuid4().int % 10**12, "n": grant_created_at, "e": end},
                )
            ).scalar_one()
            for i in range(count):
                version = (
                    await s.execute(
                        text(
                            "INSERT INTO tariff_versions(tariff_id,version_number,name_snapshot,duration_hours,device_limit,price_rub,currency,created_at) VALUES(:t,:v,'T',720,2,:price,'RUB',:n) RETURNING id"
                        ),
                        {
                            "t": tariff,
                            "v": i + 1,
                            "price": 90 + i,
                            "n": grant_created_at,
                        },
                    )
                ).scalar_one()
                quote = (
                    await s.execute(
                        text(
                            "INSERT INTO tariff_quotes(public_id,user_id,operation_type,target_tariff_version_id,current_paid_hours,current_paid_value_rub,bonus_hours,confirmed_payment_required_rub,resulting_paid_hours,resulting_paid_value_rub,resulting_bonus_hours,rounding_loss_hours,rounding_loss_value_rub,currency,status,expires_at,created_at,consumed_at) VALUES(:pub,:u,:op,:v,0,0,0,:price,720,:price,0,0,0,'RUB','consumed',:x,:n,:n) RETURNING id"
                        ),
                        {
                            "pub": uuid.uuid4(),
                            "u": user,
                            "op": "purchase" if i == 0 else "renew",
                            "v": version,
                            "price": 90 + i,
                            "x": grant_created_at + timedelta(minutes=15),
                            "n": grant_created_at,
                        },
                    )
                ).scalar_one()
                payment = (
                    await s.execute(
                        text(
                            "INSERT INTO payments(user_id,tariff_id,tariff_quote_id,tariff_version_id,amount,currency,status,provider_status,fulfillment_status,reconciliation_status,checkout_status,snapshot_duration_days,snapshot_device_limit,snapshot_amount,snapshot_currency,referral_user_bonus_days,referral_referrer_bonus_days,created_at,updated_at) VALUES(:u,:t,:q,:v,:price,'RUB','completed','succeeded','succeeded','ok','active',30,2,:price,'RUB',0,0,:n,:n) RETURNING id"
                        ),
                        {
                            "u": user,
                            "t": tariff,
                            "q": quote,
                            "v": version,
                            "price": 90 + i,
                            "n": grant_created_at,
                        },
                    )
                ).scalar_one()
                await s.execute(
                    text("UPDATE tariff_quotes SET payment_id=:p WHERE id=:q"),
                    {"p": payment, "q": quote},
                )
                if ledger:
                    await s.execute(
                        text(
                            "INSERT INTO paid_value_ledger(user_id,source_type,source_id,entry_type,paid_hours_delta,paid_value_rub_delta,currency,tariff_version_id,quote_id,payment_id,metadata,created_at) VALUES(:u,'payment',:ps,'confirmed_payment',720,:price,'RUB',:v,:q,:p,'{}',:n)"
                        ),
                        {
                            "u": user,
                            "ps": str(payment),
                            "price": 90 + i,
                            "v": version,
                            "q": quote,
                            "p": payment,
                            "n": grant_created_at,
                        },
                    )
                await s.execute(
                    text(
                        "INSERT INTO entitlement_entries(beneficiary_user_id,source_type,source_id,entry_type,days_delta,device_limit_snapshot,tariff_id_snapshot,metadata,created_at) VALUES(:u,'payment',:p,'payment_grant',30,2,:t,'{}',:n) ON CONFLICT DO NOTHING"
                    ),
                    {"u": user, "p": str(payment), "t": tariff, "n": grant_created_at},
                )
            if bonus:
                await s.execute(
                    text(
                        "INSERT INTO entitlement_entries(beneficiary_user_id,source_type,source_id,entry_type,days_delta,metadata,created_at) VALUES(:u,'payment','bonus','referral_user_bonus',5,'{}',:n)"
                    ),
                    {"u": user, "n": grant_created_at},
                )
                await s.execute(
                    text(
                        "UPDATE users SET subscription_end=subscription_end+interval '5 days' WHERE id=:u"
                    ),
                    {"u": user},
                )
        return user, as_of, tariff

    async def real_grant(
        self,
        session,
        *,
        price=Decimal("90"),
        number=10,
        user=None,
        tariff=None,
        do_grant=True,
    ):
        if user is None:
            tariff = Tariff(
                name=f"real-{number}",
                duration_days=30,
                device_limit=2,
                price_rub=int(price),
                is_active=True,
            )
            user = User(telegram_id=600000 + uuid.uuid4().int % 99999)
            session.add_all((tariff, user))
            await session.flush()
        version = TariffVersion(
            tariff_id=tariff.id,
            version_number=number,
            name_snapshot=tariff.name,
            duration_hours=720,
            device_limit=2,
            price_rub=price,
            currency="RUB",
        )
        session.add(version)
        await session.flush()
        now = now_utc()
        quote = TariffQuote(
            public_id=uuid.uuid4(),
            user_id=user.id,
            operation_type="purchase" if user.subscription_end is None else "renew",
            target_tariff_version_id=version.id,
            current_paid_hours=0,
            current_paid_value_rub=0,
            bonus_hours=0,
            confirmed_payment_required_rub=price,
            resulting_paid_hours=720,
            resulting_paid_value_rub=price,
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
        payment = Payment(
            user_id=user.id,
            tariff_id=tariff.id,
            tariff_quote_id=quote.id,
            tariff_version_id=version.id,
            amount=price,
            currency="RUB",
            status="completed",
            provider_status="succeeded",
            fulfillment_status="pending",
            reconciliation_status="ok",
            checkout_status="active",
            snapshot_duration_days=30,
            snapshot_device_limit=2,
            snapshot_amount=price,
            snapshot_currency="RUB",
            provider_confirmed_at=now,
        )
        session.add(payment)
        await session.flush()
        quote.payment_id = payment.id
        operation = PaymentFulfillmentOperation(
            payment_id=payment.id,
            operation_type="grant_subscription",
            status="processing",
            idempotency_key=f"real-grant:{payment.id}",
            payload={},
            attempts=1,
            max_attempts=3,
            next_attempt_at=now,
            locked_by="test",
            locked_at=now,
        )
        session.add(operation)
        await session.flush()
        if do_grant:
            with patch("services.payment_fulfillment.queue_post_commit_task"):
                await grant(session, operation)
        return user, tariff, payment, operation

    async def real_reverse(self, session, payment):
        operation = PaymentFulfillmentOperation(
            payment_id=payment.id,
            operation_type="reverse_payment",
            status="processing",
            idempotency_key=f"real-reverse:{payment.id}",
            payload={},
            attempts=1,
            max_attempts=3,
            next_attempt_at=now_utc(),
            locked_by="test",
            locked_at=now_utc(),
        )
        session.add(operation)
        await session.flush()
        await reverse(session, operation)

    async def test_application_grant_and_duplicate_project_once(self):
        async with self.sessions.begin() as session:
            user, _, payment, operation = await self.real_grant(session)
            with patch("services.payment_fulfillment.queue_post_commit_task"):
                await grant(session, operation)
            start = await session.scalar(
                select(func.min(EntitlementEntry.created_at)).where(
                    EntitlementEntry.beneficiary_user_id == user.id
                )
            )
            snapshot = await get_subscription_balance_snapshot(
                session, user_id=user.id, as_of=start
            )
            self.assertEqual((snapshot.tracked, len(snapshot.paid_lots)), (True, 1))

    async def test_application_reverse_latest_removes_value(self):
        async with self.sessions.begin() as session:
            user, _, payment, _ = await self.real_grant(session, number=11)
            await self.real_reverse(session, payment)
            snapshot = await get_subscription_balance_snapshot(
                session, user_id=user.id, as_of=now_utc()
            )
            self.assertEqual(
                (snapshot.tracked, snapshot.remaining_paid_value_rub),
                (True, Decimal(0)),
            )

    async def test_application_fulfillment_rollback_is_atomic(self):
        async with self.sessions() as session:
            transaction = await session.begin()
            user, _, _, _ = await self.real_grant(session, number=12)
            uid = user.id
            await transaction.rollback()
        async with self.sessions() as session:
            self.assertIsNone(await session.get(User, uid))
            self.assertEqual(
                await session.scalar(select(func.count(PaidValueLedgerEntry.id))), 0
            )

    async def test_entitlement_update_is_rejected(self):
        u, _, _ = await self.seed()
        with self.assertRaises(Exception):
            async with self.sessions.begin() as s:
                await s.execute(
                    text(
                        "UPDATE entitlement_entries SET days_delta=1 WHERE beneficiary_user_id=:u"
                    ),
                    {"u": u},
                )

    async def test_entitlement_delete_is_rejected(self):
        u, _, _ = await self.seed()
        with self.assertRaises(Exception):
            async with self.sessions.begin() as s:
                await s.execute(
                    text(
                        "DELETE FROM entitlement_entries WHERE beneficiary_user_id=:u"
                    ),
                    {"u": u},
                )

    async def test_entitlement_insert_still_works(self):
        u, _, _ = await self.seed()
        async with self.sessions() as s:
            self.assertEqual(
                await s.scalar(
                    text(
                        "SELECT count(*) FROM entitlement_entries WHERE beneficiary_user_id=:u"
                    ),
                    {"u": u},
                ),
                1,
            )

    async def test_entitlement_invalid_sign_is_rejected(self):
        u, _, _ = await self.seed()
        with self.assertRaises(Exception):
            async with self.sessions.begin() as s:
                await s.execute(
                    text(
                        "INSERT INTO entitlement_entries(beneficiary_user_id,source_type,source_id,entry_type,days_delta,metadata) VALUES(:u,'manual','bad-sign','manual_grant',-1,'{}')"
                    ),
                    {"u": u},
                )

    async def test_quote_fulfillment_projects_paid_lot(self):
        u, n, _ = await self.seed()
        async with self.sessions() as s:
            snap = await get_subscription_balance_snapshot(s, user_id=u, as_of=n)
            self.assertEqual((snap.tracked, len(snap.paid_lots)), (True, 1))

    async def test_raw_consistent_history_projects_one_lot(self):
        u, n, _ = await self.seed()
        async with self.sessions() as s:
            self.assertEqual(
                len(
                    (
                        await get_subscription_balance_snapshot(s, user_id=u, as_of=n)
                    ).paid_lots
                ),
                1,
            )

    async def test_referral_is_zero_value_bonus(self):
        u, n, _ = await self.seed(bonus=True)
        async with self.sessions() as s:
            snap = await get_subscription_balance_snapshot(s, user_id=u, as_of=n)
            self.assertEqual(
                (snap.remaining_bonus_hours, snap.bonus_lots[0].paid_value_rub),
                (120, 0),
            )

    async def test_inconsistent_reversal_fails_closed(self):
        u, n, _ = await self.seed()
        async with self.sessions.begin() as s:
            await s.execute(
                text(
                    "INSERT INTO paid_value_ledger(user_id,source_type,source_id,entry_type,paid_hours_delta,paid_value_rub_delta,currency,tariff_version_id,payment_id,reversal_of_id,metadata) SELECT user_id,'paid_value_entry',id::text,'payment_reversal',-paid_hours_delta,-paid_value_rub_delta,'RUB',tariff_version_id,payment_id,id,'{}' FROM paid_value_ledger WHERE user_id=:u"
                ),
                {"u": u},
            )
        async with self.sessions() as s:
            self.assertEqual(
                (
                    await get_subscription_balance_snapshot(s, user_id=u, as_of=n)
                ).failure_code,
                "ledger_reversal_without_entitlement_reversal",
            )

    async def test_successful_reversal_is_reflected(self):
        u, n, _ = await self.seed()
        async with self.sessions.begin() as s:
            await s.execute(
                text(
                    "INSERT INTO paid_value_ledger(user_id,source_type,source_id,entry_type,paid_hours_delta,paid_value_rub_delta,currency,tariff_version_id,quote_id,payment_id,reversal_of_id,metadata,created_at) SELECT user_id,'paid_value_entry',id::text,'payment_reversal',-paid_hours_delta,-paid_value_rub_delta,'RUB',tariff_version_id,quote_id,payment_id,id,'{}',:n FROM paid_value_ledger WHERE user_id=:u"
                ),
                {"u": u, "n": n},
            )
            await s.execute(
                text(
                    "INSERT INTO entitlement_entries(beneficiary_user_id,source_type,source_id,entry_type,days_delta,metadata,reversed_entry_id,created_at) SELECT beneficiary_user_id,source_type,source_id,'payment_reversal',-days_delta,'{}',id,:n FROM entitlement_entries WHERE beneficiary_user_id=:u AND entry_type='payment_grant'"
                ),
                {"u": u, "n": n},
            )
            await s.execute(
                text("UPDATE users SET subscription_end=:n WHERE id=:u"),
                {"u": u, "n": n},
            )
        async with self.sessions() as s:
            snap = await get_subscription_balance_snapshot(s, user_id=u, as_of=n)
            self.assertEqual(
                (
                    snap.tracked,
                    snap.remaining_paid_hours,
                    snap.remaining_paid_value_rub,
                ),
                (True, 0, Decimal(0)),
            )

    async def test_active_legacy_fails_closed(self):
        u, n, _ = await self.seed(ledger=False)
        async with self.sessions() as s:
            self.assertFalse(
                (await get_subscription_balance_snapshot(s, user_id=u, as_of=n)).tracked
            )

    async def test_expired_legacy_is_zero(self):
        u, n, _ = await self.seed(active=False, ledger=False)
        async with self.sessions() as s:
            snapshot = await get_subscription_balance_snapshot(s, user_id=u, as_of=n)
            self.assertEqual(
                (
                    snapshot.tracked,
                    snapshot.remaining_paid_hours,
                    snapshot.remaining_paid_value_rub,
                    snapshot.remaining_bonus_hours,
                ),
                (True, 0, Decimal("0"), 0),
            )

    async def test_two_renewals_are_sequential_lots(self):
        u, n, _ = await self.seed(count=2)
        async with self.sessions() as s:
            self.assertEqual(
                len(
                    (
                        await get_subscription_balance_snapshot(s, user_id=u, as_of=n)
                    ).paid_lots
                ),
                2,
            )

    async def test_mutable_tariff_does_not_change_projection(self):
        u, n, t = await self.seed()
        async with self.sessions.begin() as s:
            await s.execute(
                text("UPDATE tariffs SET price_rub=999 WHERE id=:t"), {"t": t}
            )
        async with self.sessions() as s:
            self.assertEqual(
                (
                    await get_subscription_balance_snapshot(s, user_id=u, as_of=n)
                ).remaining_paid_value_rub,
                Decimal(90),
            )

    async def test_raw_projection_reads_immutable_tariff_version(self):
        u, n, _ = await self.seed()
        async with self.sessions() as s:
            self.assertEqual(
                (await get_subscription_balance_snapshot(s, user_id=u, as_of=n))
                .paid_lots[0]
                .tariff_version_id,
                1,
            )

    async def test_for_update_serializes_with_user_writer(self):
        u, n, _ = await self.seed()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def reader():
            async with self.sessions.begin() as s:
                snap = await get_subscription_balance_snapshot(
                    s, user_id=u, as_of=n, for_update=True
                )
                entered.set()
                await release.wait()
                return snap

        task = asyncio.create_task(reader())
        await entered.wait()
        writer = asyncio.create_task(self._write_user(u))
        await asyncio.sleep(0.1)
        self.assertFalse(writer.done())
        release.set()
        await task
        await writer

    async def _write_user(self, u):
        async with self.sessions.begin() as s:
            await s.execute(
                text("UPDATE users SET device_limit=device_limit WHERE id=:u"), {"u": u}
            )

    async def test_outer_rollback_leaves_no_projection_changes(self):
        u, n, _ = await self.seed()
        async with self.sessions() as s:
            await get_subscription_balance_snapshot(
                s, user_id=u, as_of=n, for_update=True
            )
            await s.rollback()
        async with self.sessions() as s:
            self.assertEqual(
                await s.scalar(
                    text("SELECT count(*) FROM paid_value_ledger WHERE user_id=:u"),
                    {"u": u},
                ),
                1,
            )

    async def test_inactive_user_with_future_history_fails_closed(self):
        u, n, _ = await self.seed()
        async with self.sessions.begin() as s:
            await s.execute(
                text("UPDATE users SET subscription_end=:past WHERE id=:u"),
                {"past": n - timedelta(seconds=1), "u": u},
            )
        async with self.sessions() as s:
            self.assertEqual(
                (
                    await get_subscription_balance_snapshot(s, user_id=u, as_of=n)
                ).failure_code,
                "subscription_end_projection_mismatch",
            )

    async def test_null_subscription_end_with_future_history_fails_closed(self):
        u, n, _ = await self.seed()
        async with self.sessions.begin() as s:
            await s.execute(
                text("UPDATE users SET subscription_end=NULL WHERE id=:u"), {"u": u}
            )
        async with self.sessions() as s:
            self.assertEqual(
                (
                    await get_subscription_balance_snapshot(s, user_id=u, as_of=n)
                ).failure_code,
                "subscription_end_projection_mismatch",
            )

    async def test_real_referral_after_grant_projects_zero_value_bonus(self):
        async with self.sessions.begin() as session:
            referrer = User(telegram_id=500000 + uuid.uuid4().int % 99999)
            tariff = Tariff(
                name="referral-real",
                duration_days=30,
                device_limit=2,
                price_rub=90,
                is_active=True,
            )
            user = User(
                telegram_id=400000 + uuid.uuid4().int % 99999,
                referred_by=referrer.telegram_id,
            )
            session.add_all((referrer, tariff, user))
            await session.flush()
            user, _, payment, _ = await self.real_grant(
                session, user=user, tariff=tariff, number=1
            )
            operation = PaymentFulfillmentOperation(
                payment_id=payment.id,
                operation_type="grant_referral",
                status="processing",
                idempotency_key=f"real-referral:{payment.id}",
                payload={},
                attempts=1,
                max_attempts=3,
                next_attempt_at=now_utc(),
                locked_by="test",
                locked_at=now_utc(),
            )
            session.add(operation)
            await session.flush()
            await referral(session, operation)
            start = await session.scalar(
                select(func.min(EntitlementEntry.created_at)).where(
                    EntitlementEntry.beneficiary_user_id == user.id
                )
            )
            snapshot = await get_subscription_balance_snapshot(
                session, user_id=user.id, as_of=start
            )
            self.assertEqual(snapshot.remaining_bonus_hours, 120)
            self.assertTrue(all(lot.paid_value_rub == 0 for lot in snapshot.bonus_lots))

    async def test_real_payment_a_b_reverse_b_leaves_only_a(self):
        async with self.sessions.begin() as session:
            user, tariff, _, _ = await self.real_grant(session, number=1)
            _, _, payment_b, _ = await self.real_grant(
                session, price=Decimal("300"), number=2, user=user, tariff=tariff
            )
            await self.real_reverse(session, payment_b)
            snapshot = await get_subscription_balance_snapshot(
                session, user_id=user.id, as_of=now_utc()
            )
            self.assertTrue(snapshot.tracked)
            self.assertEqual(len(snapshot.paid_lots), 1)
            self.assertEqual(
                snapshot.paid_lots[0].original_paid_value_rub, Decimal("90")
            )
            self.assertLessEqual(snapshot.remaining_paid_value_rub, Decimal("90"))

    async def test_real_payment_a_b_reverse_a_fails_cross_source(self):
        async with self.sessions.begin() as session:
            user, tariff, payment_a, _ = await self.real_grant(session, number=1)
            await self.real_grant(
                session, price=Decimal("300"), number=2, user=user, tariff=tariff
            )
            await self.real_reverse(session, payment_a)
            snapshot = await get_subscription_balance_snapshot(
                session, user_id=user.id, as_of=now_utc()
            )
            self.assertEqual(
                (snapshot.failure_code, snapshot.remaining_paid_value_rub),
                ("reversal_crosses_unrelated_segments", None),
            )

    async def test_for_update_serializes_with_real_grant(self):
        async with self.sessions.begin() as session:
            user, tariff, _, _ = await self.real_grant(session, number=1)
            _, _, payment_b, operation = await self.real_grant(
                session,
                price=Decimal("300"),
                number=2,
                user=user,
                tariff=tariff,
                do_grant=False,
            )
            user_id = user.id
            operation_id = operation.id
        locked = asyncio.Event()
        release = asyncio.Event()

        async def reader():
            async with self.sessions.begin() as session:
                await get_subscription_balance_snapshot(
                    session, user_id=user_id, as_of=now_utc(), for_update=True
                )
                locked.set()
                await release.wait()

        async def fulfill():
            async with self.sessions.begin() as session:
                operation = await session.get(PaymentFulfillmentOperation, operation_id)
                with patch("services.payment_fulfillment.queue_post_commit_task"):
                    await grant(session, operation)

        reader_task = asyncio.create_task(reader())
        await locked.wait()
        grant_task = asyncio.create_task(fulfill())
        await asyncio.sleep(0.1)
        self.assertFalse(grant_task.done())
        release.set()
        await reader_task
        await grant_task
        async with self.sessions() as session:
            self.assertEqual(
                await session.scalar(
                    select(func.count(EntitlementEntry.id)).where(
                        EntitlementEntry.source_id == str(payment_b.id),
                        EntitlementEntry.entry_type == "payment_grant",
                    )
                ),
                1,
            )
            self.assertEqual(
                await session.scalar(
                    select(func.count(PaidValueLedgerEntry.id)).where(
                        PaidValueLedgerEntry.payment_id == payment_b.id,
                        PaidValueLedgerEntry.entry_type == "confirmed_payment",
                    )
                ),
                1,
            )
