"""PostgreSQL live database contracts for retention cleanup in _cleanup_old_records."""

import os
import unittest
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import (
    TariffQuote,
    User,
    WebhookInbox,
)
from services.workers.cleanup import _cleanup_old_records
from utils.datetime_helpers import now_utc


DB = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class TestCleanupRetentionPostgres(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(DB)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        from database import connection
        self.old_sessionmaker = connection._sessionmaker
        connection._sessionmaker = self.sessions
        async with self.sessions.begin() as session:
            await session.execute(
                text(
                    "TRUNCATE webhook_inbox, tariff_quotes, tariff_versions, tariffs, users "
                    "RESTART IDENTITY CASCADE"
                )
            )
            tariff_id = (
                await session.execute(
                    text(
                        "INSERT INTO tariffs(name,duration_days,device_limit,price_rub,is_active,sort_order,created_at) "
                        "VALUES('test_plan',30,2,90,true,1,NOW()) RETURNING id"
                    )
                )
            ).scalar_one()
            self.version_id = (
                await session.execute(
                    text(
                        "INSERT INTO tariff_versions(tariff_id,version_number,name_snapshot,duration_hours,"
                        "device_limit,price_rub,currency) VALUES(:t,1,'test_plan',720,2,90,'RUB') RETURNING id"
                    ),
                    {"t": tariff_id},
                )
            ).scalar_one()
            user = User(telegram_id=uuid.uuid4().int % 10**12)
            session.add(user)
            await session.flush()
            self.user_id = user.id

    async def asyncTearDown(self):
        try:
            async with self.sessions.begin() as session:
                await session.execute(
                    text(
                        "TRUNCATE webhook_inbox, tariff_quotes, tariff_versions, tariffs, users "
                        "RESTART IDENTITY CASCADE"
                    )
                )
        finally:
            from database import connection
            connection._sessionmaker = self.old_sessionmaker
            await self.engine.dispose()

    async def test_live_postgres_retention_cleanup_predicates(self):
        now = now_utc()
        old_dt = now - timedelta(days=45)
        recent_dt = now - timedelta(days=2)

        async with self.sessions.begin() as session:
            # 1. Old Succeeded Webhook (SHOULD BE DELETED)
            wh_old_succeeded = WebhookInbox(
                provider="yookassa",
                event_key="evt_old_succeeded",
                provider_object_id="obj_1",
                event_type="payment.succeeded",
                payload={"test": 1},
                status="succeeded",
                received_at=old_dt,
                processed_at=old_dt,
            )
            # 2. Old Dead Webhook (SHOULD BE DELETED)
            wh_old_dead = WebhookInbox(
                provider="yookassa",
                event_key="evt_old_dead",
                provider_object_id="obj_2",
                event_type="payment.canceled",
                payload={"test": 2},
                status="dead",
                received_at=old_dt,
                processed_at=old_dt,
            )
            # 3. Old Pending Webhook (PRESERVED)
            wh_old_pending = WebhookInbox(
                provider="yookassa",
                event_key="evt_old_pending",
                provider_object_id="obj_3",
                event_type="payment.waiting_for_capture",
                payload={"test": 3},
                status="pending",
                received_at=old_dt,
            )
            # 4. Old Processing Webhook (PRESERVED)
            wh_old_processing = WebhookInbox(
                provider="yookassa",
                event_key="evt_old_processing",
                provider_object_id="obj_4",
                event_type="payment.waiting_for_capture",
                payload={"test": 4},
                status="processing",
                received_at=old_dt,
            )
            # 5. Old Retry Webhook (PRESERVED)
            wh_old_retry = WebhookInbox(
                provider="yookassa",
                event_key="evt_old_retry",
                provider_object_id="obj_5",
                event_type="payment.succeeded",
                payload={"test": 5},
                status="retry",
                received_at=old_dt,
            )
            # 6. Recent Succeeded Webhook (PRESERVED)
            wh_recent_succeeded = WebhookInbox(
                provider="yookassa",
                event_key="evt_recent_succeeded",
                provider_object_id="obj_6",
                event_type="payment.succeeded",
                payload={"test": 6},
                status="succeeded",
                received_at=recent_dt,
                processed_at=recent_dt,
            )
            # 7. Recent Dead Webhook (PRESERVED)
            wh_recent_dead = WebhookInbox(
                provider="yookassa",
                event_key="evt_recent_dead",
                provider_object_id="obj_7",
                event_type="payment.canceled",
                payload={"test": 7},
                status="dead",
                received_at=recent_dt,
                processed_at=recent_dt,
            )

            session.add_all([
                wh_old_succeeded,
                wh_old_dead,
                wh_old_pending,
                wh_old_processing,
                wh_old_retry,
                wh_recent_succeeded,
                wh_recent_dead,
            ])
            await session.flush()

            old_wh_ids = {wh_old_succeeded.id, wh_old_dead.id}
            preserved_wh_ids = {
                wh_old_pending.id,
                wh_old_processing.id,
                wh_old_retry.id,
                wh_recent_succeeded.id,
                wh_recent_dead.id,
            }

        # Run retention cleanup worker function with test session
        await _cleanup_old_records()

        # Query remaining records from live PostgreSQL
        async with self.sessions.begin() as session:
            wh_remaining = set(
                (await session.scalars(select(WebhookInbox.id))).all()
            )

            # Assert old succeeded/dead webhooks are deleted
            for wh_id in old_wh_ids:
                self.assertNotIn(wh_id, wh_remaining, f"Old webhook {wh_id} should have been pruned")

            # Assert all non-matching records (pending/processing/retry/fresh) are preserved
            for wh_id in preserved_wh_ids:
                self.assertIn(wh_id, wh_remaining, f"Webhook {wh_id} must be preserved")

    async def test_tariff_quote_deletion_is_forbidden_by_trigger(self):
        """Verify PostgreSQL database trigger reject_quote_economic_change strictly blocks quote deletions."""
        now = now_utc()
        async with self.sessions.begin() as session:
            quote = TariffQuote(
                public_id=uuid.uuid4(),
                user_id=self.user_id,
                operation_type="purchase",
                target_tariff_version_id=self.version_id,
                current_paid_hours=0,
                current_paid_value_rub=Decimal("0.00"),
                bonus_hours=0,
                amount_due_rub=Decimal("90.00"),
                resulting_paid_hours=720,
                resulting_paid_value_rub=Decimal("90.00"),
                resulting_bonus_hours=0,
                rounding_loss_hours=Decimal("0.0"),
                rounding_loss_value_rub=Decimal("0.00"),
                currency="RUB",
                status="expired",
                expires_at=now + timedelta(hours=1),
                created_at=now,
                consumed_at=None,
            )
            session.add(quote)
            await session.flush()
            quote_id = quote.id

        async with self.sessions.begin() as session:
            with self.assertRaises(Exception) as ctx:
                await session.execute(
                    text("DELETE FROM tariff_quotes WHERE id = :id"),
                    {"id": quote_id},
                )
                await session.flush()
            self.assertIn("quote deletion is forbidden", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()


