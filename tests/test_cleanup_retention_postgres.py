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
TRUNCATE_SQL = (
    "TRUNCATE webhook_inbox, tariff_quotes, users "
    "RESTART IDENTITY CASCADE"
)


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class TestCleanupRetentionPostgres(unittest.IsolatedAsyncioTestCase):
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

    async def test_live_postgres_retention_cleanup_predicates(self):
        now = now_utc()
        old_dt = now - timedelta(days=45)
        recent_dt = now - timedelta(days=2)

        async with self.sessions.begin() as session:
            # 1. Old Succeeded Webhook (SHOULD BE DELETED)
            wh_old_succeeded = WebhookInbox(
                event_id="evt_old_succeeded",
                source="yookassa",
                event_type="payment.succeeded",
                status="succeeded",
                received_at=old_dt,
                processed_at=old_dt,
            )
            # 2. Old Dead Webhook (SHOULD BE DELETED)
            wh_old_dead = WebhookInbox(
                event_id="evt_old_dead",
                source="yookassa",
                event_type="payment.canceled",
                status="dead",
                received_at=old_dt,
                processed_at=old_dt,
            )
            # 3. Old Pending Webhook (PRESERVED)
            wh_old_pending = WebhookInbox(
                event_id="evt_old_pending",
                source="yookassa",
                event_type="payment.waiting_for_capture",
                status="pending",
                received_at=old_dt,
            )
            # 4. Old Processing Webhook (PRESERVED)
            wh_old_processing = WebhookInbox(
                event_id="evt_old_processing",
                source="yookassa",
                event_type="payment.waiting_for_capture",
                status="processing",
                received_at=old_dt,
            )
            # 5. Old Retry Webhook (PRESERVED)
            wh_old_retry = WebhookInbox(
                event_id="evt_old_retry",
                source="yookassa",
                event_type="payment.succeeded",
                status="retry",
                received_at=old_dt,
            )
            # 6. Recent Succeeded Webhook (PRESERVED)
            wh_recent_succeeded = WebhookInbox(
                event_id="evt_recent_succeeded",
                source="yookassa",
                event_type="payment.succeeded",
                status="succeeded",
                received_at=recent_dt,
                processed_at=recent_dt,
            )
            # 7. Recent Dead Webhook (PRESERVED)
            wh_recent_dead = WebhookInbox(
                event_id="evt_recent_dead",
                source="yookassa",
                event_type="payment.canceled",
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

            # Quotes
            # 8. Old Expired Quote (SHOULD BE DELETED)
            q_old_expired = TariffQuote(
                user_id=self.user_id,
                quote_type="tariff_change",
                status="expired",
                amount_rub=Decimal("100.00"),
                created_at=old_dt,
                expires_at=old_dt + timedelta(hours=1),
                consumed_at=None,
            )
            # 9. Old Cancelled Quote (SHOULD BE DELETED)
            q_old_cancelled = TariffQuote(
                user_id=self.user_id,
                quote_type="tariff_change",
                status="cancelled",
                amount_rub=Decimal("100.00"),
                created_at=old_dt,
                expires_at=old_dt + timedelta(hours=1),
                consumed_at=None,
            )
            # 10. Old Consumed Quote (PRESERVED - strict financial audit invariant)
            q_old_consumed = TariffQuote(
                user_id=self.user_id,
                quote_type="tariff_change",
                status="consumed",
                amount_rub=Decimal("100.00"),
                created_at=old_dt,
                expires_at=old_dt + timedelta(hours=1),
                consumed_at=old_dt,
            )
            # 11. Old Active Quote (PRESERVED)
            q_old_active = TariffQuote(
                user_id=self.user_id,
                quote_type="tariff_change",
                status="active",
                amount_rub=Decimal("100.00"),
                created_at=old_dt,
                expires_at=now + timedelta(days=5),
                consumed_at=None,
            )
            # 12. Recent Expired Quote (PRESERVED)
            q_recent_expired = TariffQuote(
                user_id=self.user_id,
                quote_type="tariff_change",
                status="expired",
                amount_rub=Decimal("100.00"),
                created_at=recent_dt,
                expires_at=recent_dt + timedelta(hours=1),
                consumed_at=None,
            )
            # 13. Recent Cancelled Quote (PRESERVED)
            q_recent_cancelled = TariffQuote(
                user_id=self.user_id,
                quote_type="tariff_change",
                status="cancelled",
                amount_rub=Decimal("100.00"),
                created_at=recent_dt,
                expires_at=recent_dt + timedelta(hours=1),
                consumed_at=None,
            )

            session.add_all([
                q_old_expired,
                q_old_cancelled,
                q_old_consumed,
                q_old_active,
                q_recent_expired,
                q_recent_cancelled,
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
            old_quote_ids = {q_old_expired.id, q_old_cancelled.id}
            preserved_quote_ids = {
                q_old_consumed.id,
                q_old_active.id,
                q_recent_expired.id,
                q_recent_cancelled.id,
            }

        # Run retention cleanup worker function
        await _cleanup_old_records()

        # Query remaining records from live PostgreSQL
        async with self.sessions.begin() as session:
            wh_remaining = set(
                (await session.scalars(select(WebhookInbox.id))).all()
            )
            quotes_remaining = set(
                (await session.scalars(select(TariffQuote.id))).all()
            )

            # Assert old records are deleted
            for wh_id in old_wh_ids:
                self.assertNotIn(wh_id, wh_remaining, f"Old webhook {wh_id} should have been pruned")

            for q_id in old_quote_ids:
                self.assertNotIn(q_id, quotes_remaining, f"Old quote {q_id} should have been pruned")

            # Assert all non-matching records are preserved
            for wh_id in preserved_wh_ids:
                self.assertIn(wh_id, wh_remaining, f"Webhook {wh_id} must be preserved")

            for q_id in preserved_quote_ids:
                self.assertIn(q_id, quotes_remaining, f"Quote {q_id} must be preserved")


if __name__ == "__main__":
    unittest.main()
