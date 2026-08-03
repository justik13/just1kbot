"""PostgreSQL integration for the greenfield subscription-balance reader."""

import os
import unittest
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import (
    EntitlementEntry,
    PaidValueLedgerEntry,
    Tariff,
    TariffQuote,
    TariffVersion,
    User,
)
from services.subscription_balance_service import get_subscription_balance_snapshot
from utils.datetime_helpers import now_utc

DB = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class SubscriptionBalancePostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(DB)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions.begin() as session:
            await session.execute(
                text(
                    "TRUNCATE entitlement_entries, paid_value_ledger, "
                    "tariff_quotes, tariff_versions, users, tariffs "
                    "RESTART IDENTITY CASCADE"
                )
            )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def seed_purchase(self, session, *, hours=24, amount="49"):
        created = now_utc().replace(microsecond=0)
        tariff = Tariff(
            name="Balance reader",
            duration_days=hours // 24,
            device_limit=2,
            price_rub=int(Decimal(amount)),
            is_active=True,
        )
        user = User(
            telegram_id=uuid.uuid4().int % 10**12,
            subscription_end=created + timedelta(hours=hours),
            current_tariff_id=None,
        )
        session.add_all((tariff, user))
        await session.flush()
        user.current_tariff_id = tariff.id
        version = TariffVersion(
            tariff_id=tariff.id,
            version_number=1,
            name_snapshot=tariff.name,
            duration_hours=hours,
            device_limit=2,
            price_rub=Decimal(amount),
            currency="RUB",
            created_at=created,
        )
        session.add(version)
        await session.flush()
        quote = TariffQuote(
            public_id=uuid.uuid4(),
            user_id=user.id,
            operation_type="purchase",
            target_tariff_version_id=version.id,
            current_paid_hours=0,
            current_paid_value_rub=0,
            bonus_hours=0,
            amount_due_rub=Decimal(amount),
            resulting_paid_hours=hours,
            resulting_paid_value_rub=Decimal(amount),
            resulting_bonus_hours=0,
            rounding_loss_hours=0,
            rounding_loss_value_rub=0,
            currency="RUB",
            status="consumed",
            created_at=created,
            expires_at=created + timedelta(minutes=15),
            consumed_at=created,
        )
        session.add(quote)
        await session.flush()
        session.add_all(
            (
                PaidValueLedgerEntry(
                    user_id=user.id,
                    source_type="quote",
                    source_id=str(quote.id),
                    entry_type="account_purchase",
                    paid_hours_delta=hours,
                    paid_value_rub_delta=Decimal(amount),
                    currency="RUB",
                    tariff_version_id=version.id,
                    quote_id=quote.id,
                    created_at=created,
                ),
                EntitlementEntry(
                    beneficiary_user_id=user.id,
                    source_type="quote",
                    source_id=str(quote.id),
                    entry_type="account_purchase_grant",
                    days_delta=hours // 24,
                    hours_delta=hours,
                    device_limit_snapshot=2,
                    tariff_id_snapshot=tariff.id,
                    created_at=created,
                ),
            )
        )
        await session.flush()
        return user, created

    async def test_valid_purchase_history_is_projected(self):
        async with self.sessions.begin() as session:
            user, created = await self.seed_purchase(session)
            snapshot = await get_subscription_balance_snapshot(
                session, user_id=user.id, as_of=created
            )
            self.assertTrue(snapshot.tracked)
            self.assertEqual(snapshot.remaining_paid_hours, 24)
            self.assertEqual(snapshot.remaining_paid_value_rub, Decimal("49"))

    async def test_reader_does_not_commit_or_mutate_history(self):
        async with self.sessions.begin() as session:
            user, created = await self.seed_purchase(session)
            before = user.subscription_end
            await get_subscription_balance_snapshot(
                session, user_id=user.id, as_of=created
            )
            self.assertEqual(user.subscription_end, before)
            self.assertFalse(session.dirty)


if __name__ == "__main__":
    unittest.main()
