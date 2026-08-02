import os
import unittest
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    EntitlementEntry,
    PaidValueLedgerEntry,
    Payment,
    PaymentFulfillmentOperation,
    Tariff,
    TariffQuote,
    User,
)
from database.repositories.paid_value_repo import (
    PaidValueLedgerConflictError,
    get_or_create_confirmed_payment_entry,
)
from services.payment_fulfillment import grant
from utils.datetime_helpers import now_utc


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not set")
class TariffQuotesPostgresTests(unittest.IsolatedAsyncioTestCase):
    """Database-enforced economic contracts, executed against real PostgreSQL."""

    async def asyncSetUp(self):
        self.engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
        self.connection = await self.engine.connect()
        self.transaction = await self.connection.begin()
        marker = uuid.uuid4().hex
        self.duration_days = 1000 + int(marker[:4], 16)
        self.device_limit = 1000 + int(marker[4:8], 16)
        self.user_id = (
            await self.connection.execute(
                text(
                    "INSERT INTO users(telegram_id,device_limit,referral_days,is_banned,is_bot_blocked,is_deleted,notification_retry_count,notified_3d,notified_1d,notified_2h,notified_expired,notified_grace_12h,device_creations_today,created_at) "
                    "VALUES(:telegram,0,0,false,false,false,0,false,false,false,false,false,0,now()) RETURNING id"
                ),
                {"telegram": int(marker[:12], 16)},
            )
        ).scalar_one()
        self.tariff_id = (
            await self.connection.execute(
                text(
                    "INSERT INTO tariffs(name,duration_days,device_limit,price_rub,is_active,sort_order,created_at) VALUES(:name,:days,:limit,300,true,0,now()) RETURNING id"
                ),
                {
                    "name": marker,
                    "days": self.duration_days,
                    "limit": self.device_limit,
                },
            )
        ).scalar_one()
        self.version_id = (
            await self.connection.execute(
                text(
                    "INSERT INTO tariff_versions(tariff_id,version_number,name_snapshot,duration_hours,device_limit,price_rub,currency) VALUES(:tid,1,:name,:hours,:limit,300,'RUB') RETURNING id"
                ),
                {
                    "tid": self.tariff_id,
                    "name": marker,
                    "hours": self.duration_days * 24,
                    "limit": self.device_limit,
                },
            )
        ).scalar_one()
        self.source_version_id = (
            await self.connection.execute(
                text(
                    "INSERT INTO tariff_versions(tariff_id,version_number,name_snapshot,duration_hours,device_limit,price_rub,currency) VALUES(:tid,2,:name,:hours,:limit,299,'RUB') RETURNING id"
                ),
                {
                    "tid": self.tariff_id,
                    "name": marker,
                    "hours": self.duration_days * 24,
                    "limit": self.device_limit,
                },
            )
        ).scalar_one()

    async def asyncTearDown(self):
        try:
            await self.transaction.rollback()
        finally:
            try:
                await self.connection.close()
            finally:
                await self.engine.dispose()

    async def _quote(self, operation="change", minutes=15):
        now = now_utc()
        change_columns = (
            ",source_tariff_version_id,balance_as_of,source_subscription_end,source_balance_fingerprint,source_entitlement_entry_ids,source_ledger_entry_ids"
            if operation == "change"
            else ""
        )
        change_values = (
            ",:source,:created,:subscription_end,:fingerprint,'[]'::jsonb,'[]'::jsonb"
            if operation == "change"
            else ""
        )
        return (
            await self.connection.execute(
                text(
                    "INSERT INTO tariff_quotes(public_id,user_id,operation_type,target_tariff_version_id,current_paid_hours,current_paid_value_rub,bonus_hours,amount_due_rub,resulting_paid_hours,resulting_paid_value_rub,resulting_bonus_hours,rounding_loss_hours,rounding_loss_value_rub,currency,status,expires_at,created_at"
                    + change_columns
                    + ") "
                    "VALUES(:public,:uid,:operation,:version,0,0,24,300,720,300,24,0,0,'RUB','active',:expires,:created"
                    + change_values
                    + ") RETURNING id"
                ),
                {
                    "public": uuid.uuid4(),
                    "uid": self.user_id,
                    "operation": operation,
                    "version": self.version_id,
                    "source": self.source_version_id,
                    "subscription_end": now + timedelta(days=30),
                    "fingerprint": "a" * 64,
                    "created": now,
                    "expires": now + timedelta(minutes=minutes),
                },
            )
        ).scalar_one()

    async def test_quote_expires_exactly_after_fifteen_minutes(self):
        await self._quote()
        with self.assertRaises(IntegrityError):
            await self._quote(operation="purchase", minutes=14)

    async def test_maximum_one_active_change_quote_per_user(self):
        await self._quote()
        with self.assertRaises(IntegrityError):
            await self._quote()

    async def test_quote_economic_fields_are_immutable(self):
        quote_id = await self._quote()
        with self.assertRaises(DBAPIError):
            await self.connection.execute(
                text("UPDATE tariff_quotes SET resulting_paid_hours=721 WHERE id=:id"),
                {"id": quote_id},
            )

    async def test_tariff_version_is_immutable_after_quote(self):
        await self._quote()
        with self.assertRaises(DBAPIError):
            await self.connection.execute(
                text("UPDATE tariff_versions SET price_rub=301 WHERE id=:id"),
                {"id": self.version_id},
            )

    async def test_bonus_hours_do_not_increase_paid_value(self):
        now = now_utc()
        with self.assertRaises(IntegrityError):
            await self.connection.execute(
                text(
                    "INSERT INTO tariff_quotes(public_id,user_id,operation_type,target_tariff_version_id,current_paid_hours,current_paid_value_rub,bonus_hours,amount_due_rub,resulting_paid_hours,resulting_paid_value_rub,resulting_bonus_hours,rounding_loss_hours,rounding_loss_value_rub,currency,status,expires_at,created_at) VALUES(:p,:u,'purchase',:v,0,0,100,0,1,1,100,0,0,'RUB','active',:e,:c)"
                ),
                {
                    "p": uuid.uuid4(),
                    "u": self.user_id,
                    "v": self.version_id,
                    "c": now,
                    "e": now + timedelta(minutes=15),
                },
            )

    async def test_one_conversion_entry_per_quote(self):
        quote = await self._quote()
        statement = text(
            "INSERT INTO paid_value_ledger(user_id,source_type,source_id,entry_type,paid_hours_delta,paid_value_rub_delta,currency,tariff_version_id,quote_id) VALUES(:u,'quote',:s,'tariff_conversion',0,0,'RUB',:v,:q)"
        )
        values = {"u": self.user_id, "s": str(quote), "v": self.version_id, "q": quote}
        await self.connection.execute(statement, values)
        with self.assertRaises(IntegrityError):
            await self.connection.execute(statement, values)

    async def test_confirmed_payment_requires_payment(self):
        with self.assertRaises(IntegrityError):
            await self.connection.execute(
                text(
                    "INSERT INTO paid_value_ledger(user_id,source_type,source_id,entry_type,paid_hours_delta,paid_value_rub_delta,currency,tariff_version_id,payment_id,quote_id) VALUES(:u,'payment','1','confirmed_payment',720,300,'RUB',:v,NULL,1)"
                ),
                {"u": self.user_id, "v": self.version_id},
            )

    async def test_confirmed_payment_requires_quote(self):
        with self.assertRaises(IntegrityError):
            await self.connection.execute(
                text(
                    "INSERT INTO paid_value_ledger(user_id,source_type,source_id,entry_type,paid_hours_delta,paid_value_rub_delta,currency,tariff_version_id,payment_id,quote_id) VALUES(:u,'payment','1','confirmed_payment',720,300,'RUB',:v,1,NULL)"
                ),
                {"u": self.user_id, "v": self.version_id},
            )

    async def test_conversion_requires_quote(self):
        with self.assertRaises(IntegrityError):
            await self.connection.execute(
                text(
                    "INSERT INTO paid_value_ledger(user_id,source_type,source_id,entry_type,paid_hours_delta,paid_value_rub_delta,currency,tariff_version_id) VALUES(:u,'quote','1','tariff_conversion',0,0,'RUB',:v)"
                ),
                {"u": self.user_id, "v": self.version_id},
            )

    async def test_reversal_requires_original(self):
        with self.assertRaises(IntegrityError):
            await self.connection.execute(
                text(
                    "INSERT INTO paid_value_ledger(user_id,source_type,source_id,entry_type,paid_hours_delta,paid_value_rub_delta,currency,tariff_version_id) VALUES(:u,'entry','1','payment_reversal',-1,-1,'RUB',:v)"
                ),
                {"u": self.user_id, "v": self.version_id},
            )

    async def test_confirmed_repository_is_idempotent_and_checks_economics(self):
        quote_id = await self._quote(operation="purchase")
        async with AsyncSession(
            bind=self.connection, expire_on_commit=False
        ) as session:
            payment = Payment(
                user_id=self.user_id,
                tariff_id=self.tariff_id,
                tariff_quote_id=quote_id,
                tariff_version_id=self.version_id,
                amount=Decimal("300"),
                currency="RUB",
                status="pending",
                provider_status="succeeded",
                fulfillment_status="pending",
                reconciliation_status="ok",
                checkout_status="active",
                snapshot_duration_days=self.duration_days,
                snapshot_device_limit=self.device_limit,
                snapshot_amount=Decimal("300"),
                snapshot_currency="RUB",
                provider_confirmed_at=now_utc(),
            )
            session.add(payment)
            await session.flush()
            quote = await session.get(TariffQuote, quote_id)
            quote.payment_id = payment.id
            first = await get_or_create_confirmed_payment_entry(
                session,
                user_id=self.user_id,
                payment_id=payment.id,
                quote_id=quote_id,
                tariff_version_id=self.version_id,
                paid_hours=self.duration_days * 24,
                paid_value_rub=Decimal("300"),
            )
            second = await get_or_create_confirmed_payment_entry(
                session,
                user_id=self.user_id,
                payment_id=payment.id,
                quote_id=quote_id,
                tariff_version_id=self.version_id,
                paid_hours=self.duration_days * 24,
                paid_value_rub=Decimal("300"),
            )
            self.assertEqual(first.id, second.id)
            with self.assertRaises(PaidValueLedgerConflictError):
                await get_or_create_confirmed_payment_entry(
                    session,
                    user_id=self.user_id,
                    payment_id=payment.id,
                    quote_id=quote_id,
                    tariff_version_id=self.version_id,
                    paid_hours=self.duration_days * 24,
                    paid_value_rub=Decimal("301"),
                )

    async def test_payment_tariff_must_match_immutable_version(self):
        quote_id = await self._quote(operation="purchase")
        async with AsyncSession(
            bind=self.connection, expire_on_commit=False
        ) as session:
            other = Tariff(
                name=uuid.uuid4().hex,
                duration_days=self.duration_days + 1,
                device_limit=self.device_limit,
                price_rub=300,
                is_active=True,
            )
            session.add(other)
            await session.flush()
            payment = Payment(
                user_id=self.user_id,
                tariff_id=other.id,
                tariff_quote_id=quote_id,
                tariff_version_id=self.version_id,
                amount=Decimal("300"),
                currency="RUB",
                status="pending",
                provider_status="succeeded",
                fulfillment_status="pending",
                reconciliation_status="ok",
                checkout_status="active",
                snapshot_duration_days=self.duration_days,
                snapshot_device_limit=self.device_limit,
                snapshot_amount=Decimal("300"),
                snapshot_currency="RUB",
                provider_confirmed_at=now_utc(),
            )
            session.add(payment)
            await session.flush()
            quote = await session.get(TariffQuote, quote_id)
            quote.payment_id = payment.id
            operation = PaymentFulfillmentOperation(
                payment_id=payment.id,
                operation_type="grant_subscription",
                status="processing",
                idempotency_key=uuid.uuid4().hex,
                payload={},
                attempts=1,
                next_attempt_at=now_utc(),
                locked_by="worker",
                locked_at=now_utc(),
            )
            session.add(operation)
            await session.flush()
            before = (await session.get(User, self.user_id)).subscription_end
            await grant(session, operation)
            self.assertEqual(payment.fulfillment_status, "manual_review")
            self.assertEqual(quote.status, "manual_review")
            self.assertEqual(
                (await session.get(User, self.user_id)).subscription_end, before
            )
            self.assertIsNone(
                await session.scalar(
                    select(EntitlementEntry).where(
                        EntitlementEntry.source_type == "payment",
                        EntitlementEntry.source_id == str(payment.id),
                        EntitlementEntry.entry_type == "payment_grant",
                    )
                )
            )
            self.assertIsNone(
                await session.scalar(
                    select(PaidValueLedgerEntry).where(
                        PaidValueLedgerEntry.payment_id == payment.id
                    )
                )
            )


if __name__ == "__main__":
    unittest.main()
