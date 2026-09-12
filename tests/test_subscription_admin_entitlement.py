"""Tests for subscription admin entitlement tracking and backfill."""

from datetime import timedelta
from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, patch

import os

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import EntitlementEntry, Payment, Tariff, User
from services.subscription import SubscriptionService
from services.subscription_balance_service import get_subscription_balance_snapshot
from services.tariff_change_quote import create_tariff_change_quote
from utils.datetime_helpers import now_utc

DB = os.getenv("TEST_DATABASE_URL")


class SubscriptionAdminEntitlementUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_extend_subscription_creates_manual_grant_entitlement(self) -> None:
        session = AsyncMock()
        session.add = unittest.mock.MagicMock()
        user = User(
            id=42,
            telegram_id=123456,
            device_limit=2,
            current_tariff_id=10,
            subscription_end=now_utc() + timedelta(days=10),
        )
        session.scalar.return_value = user

        with (
            patch("services.subscription.get_user_profiles_count", new_callable=AsyncMock) as mock_profiles,
            patch.object(SubscriptionService, "_sync_access_state", new_callable=AsyncMock),
            patch("services.subscription.invalidate_user_cache"),
        ):
            mock_profiles.return_value = 1
            updated_user = await SubscriptionService.extend_subscription(
                session=session,
                telegram_id=123456,
                days=30,
                new_device_limit=3,
                new_tariff_id=11,
                create_entitlement=True,
                admin_id=999,
                reason="admin_sub_grant",
            )

        self.assertIs(updated_user, user)
        # Verify entitlement added to session
        self.assertTrue(session.add.called)
        added_obj = session.add.call_args[0][0]
        self.assertIsInstance(added_obj, EntitlementEntry)
        self.assertEqual(added_obj.beneficiary_user_id, 42)
        self.assertEqual(added_obj.source_type, "admin")
        self.assertEqual(added_obj.entry_type, "manual_grant")
        self.assertEqual(added_obj.days_delta, 30)
        self.assertEqual(added_obj.hours_delta, 720)
        self.assertEqual(added_obj.device_limit_snapshot, 3)
        self.assertEqual(added_obj.tariff_id_snapshot, 11)
        self.assertEqual(added_obj.metadata_["admin_id"], 999)
        self.assertEqual(added_obj.metadata_["reason"], "admin_sub_grant")

    async def test_extend_subscription_without_entitlement_flag_skips_creation(self) -> None:
        session = AsyncMock()
        session.add = unittest.mock.MagicMock()
        user = User(
            id=42,
            telegram_id=123456,
            device_limit=2,
            current_tariff_id=10,
            subscription_end=now_utc() + timedelta(days=10),
        )
        session.scalar.return_value = user

        with (
            patch("services.subscription.get_user_profiles_count", new_callable=AsyncMock) as mock_profiles,
            patch.object(SubscriptionService, "_sync_access_state", new_callable=AsyncMock),
            patch("services.subscription.invalidate_user_cache"),
        ):
            mock_profiles.return_value = 1
            updated_user = await SubscriptionService.extend_subscription(
                session=session,
                telegram_id=123456,
                days=30,
                create_entitlement=False,
            )

        self.assertIs(updated_user, user)
        self.assertFalse(session.add.called)


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class SubscriptionAdminEntitlementIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(DB, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_manual_grant_allows_quote_calculation_and_settlement(self) -> None:
        now = now_utc().replace(microsecond=0)
        async with self.sessions.begin() as session:
            # 1. Create base and upgrade tariffs
            t1 = Tariff(
                name="Base Tariff",
                price_rub=Decimal("300.00"),
                duration_days=30,
                device_limit=2,
                is_active=True,
            )
            t2 = Tariff(
                name="Pro Tariff",
                price_rub=Decimal("600.00"),
                duration_days=30,
                device_limit=5,
                is_active=True,
            )
            session.add_all([t1, t2])
            await session.flush()
            # 3. Create user
            user = User(
                telegram_id=888999111,
                username="manual_grant_user",
                device_limit=t1.device_limit,
                current_tariff_id=t1.id,
                subscription_end=now,
            )
            session.add(user)
            await session.flush()

            # 4. Extend subscription via SubscriptionService with manual grant
            await SubscriptionService.extend_subscription(
                session,
                telegram_id=user.telegram_id,
                days=30,
                new_device_limit=t1.device_limit,
                new_tariff_id=t1.id,
                create_entitlement=True,
                admin_id=777,
                reason="admin_sub_grant",
            )
            user_id = user.id
            t2_id = t2.id

        # 5. Check tariff change quote calculation: MUST succeed and be tracked
        async with self.sessions.begin() as session:
            quote, failure_code = await create_tariff_change_quote(
                session,
                user_id=user_id,
                target_tariff_id=t2_id,
                as_of=now,
            )
            self.assertIsNone(failure_code)
            self.assertIsNotNone(quote)
            self.assertEqual(quote.status, "pending")
            self.assertGreater(quote.final_price_rub, Decimal("0.00"))

    async def test_exact_hours_and_payment_linking_backfill_logic(self) -> None:
        now = now_utc().replace(microsecond=0)
        async with self.sessions.begin() as session:
            # User 1: Has active sub with 5 hours remaining (less than 24h, would fail if CEIL to 24h)
            # and has a succeeded payment.
            user1 = User(
                telegram_id=111222333,
                username="paid_user_5h",
                device_limit=2,
                subscription_end=now + timedelta(hours=5),
            )
            # User 2: Has active sub with 50 hours remaining and NO payment.
            user2 = User(
                telegram_id=444555666,
                username="admin_user_50h",
                device_limit=3,
                subscription_end=now + timedelta(hours=50),
            )
            session.add_all([user1, user2])
            await session.flush()

            # Payment for User 1
            payment1 = Payment(
                user_id=user1.id,
                amount=Decimal("300.00"),
                currency="RUB",
                public_order_id="ord_test_123",
                provider_idempotency_key="idemp_test_123",
                provider_status="succeeded",
                fulfillment_status="succeeded",
                paid_at=now - timedelta(days=25),
                credited_at=now - timedelta(days=25),
            )
            session.add(payment1)
            await session.flush()

            user1_id = user1.id
            user2_id = user2.id
            payment1_id = payment1.id

        # Run migration 0027 backfill SQL directly
        async with self.sessions.begin() as session:
            # First relax check constraint as migration 0027 does
            await session.execute(
                text("ALTER TABLE entitlement_entries DROP CONSTRAINT IF EXISTS ck_entitlement_entries_shape")
            )
            await session.execute(
                text(
                    """
                    ALTER TABLE entitlement_entries ADD CONSTRAINT ck_entitlement_entries_shape CHECK (
                      (entry_type IN ('account_purchase_grant', 'referral_user_bonus', 'referral_referrer_bonus', 'manual_grant')
                       AND days_delta >= 0 AND reversed_entry_id IS NULL
                       AND ((hours_delta IS NULL AND days_delta > 0) OR hours_delta > 0))
                      OR 
                      (entry_type = 'tariff_change' AND source_type = 'quote'
                       AND days_delta = 0 AND hours_delta > 0 AND reversed_entry_id IS NULL)
                      OR 
                      (entry_type = 'referral_reversal' AND days_delta < 0
                       AND reversed_entry_id IS NOT NULL
                       AND (hours_delta IS NULL OR hours_delta = days_delta * 24))
                    )
                    """
                )
            )

            # Backfill active users without entitlements
            await session.execute(
                text(
                    """
                    WITH missing_users AS (
                        SELECT 
                            u.id AS user_id,
                            u.device_limit,
                            u.current_tariff_id,
                            u.subscription_end,
                            GREATEST(1, FLOOR(EXTRACT(EPOCH FROM (u.subscription_end - NOW())) / 3600)::int) AS exact_hours,
                            p.id AS payment_id,
                            p.amount AS payment_amount,
                            COALESCE(p.paid_at, p.credited_at, p.created_at) AS payment_time
                        FROM users u
                        LEFT JOIN LATERAL (
                            SELECT p.id, p.amount, p.paid_at, p.credited_at, p.created_at
                            FROM payments p
                            WHERE p.user_id = u.id AND p.provider_status = 'succeeded'
                            ORDER BY COALESCE(p.paid_at, p.credited_at, p.created_at) DESC
                            LIMIT 1
                        ) p ON true
                        WHERE u.subscription_end > NOW()
                          AND u.is_deleted = false
                          AND NOT EXISTS (
                              SELECT 1 FROM entitlement_entries e
                              WHERE e.beneficiary_user_id = u.id
                          )
                    )
                    INSERT INTO entitlement_entries (
                        beneficiary_user_id,
                        source_type,
                        source_id,
                        entry_type,
                        days_delta,
                        hours_delta,
                        device_limit_snapshot,
                        tariff_id_snapshot,
                        metadata,
                        created_at
                    )
                    SELECT 
                        mu.user_id,
                        CASE WHEN mu.payment_id IS NOT NULL THEN 'payment' ELSE 'admin' END,
                        CASE 
                            WHEN mu.payment_id IS NOT NULL THEN 'legacy_payment_' || mu.payment_id
                            ELSE 'legacy_admin_grant_' || mu.user_id
                        END,
                        'manual_grant',
                        mu.exact_hours / 24,
                        mu.exact_hours,
                        COALESCE(mu.device_limit, 1),
                        mu.current_tariff_id,
                        CASE 
                            WHEN mu.payment_id IS NOT NULL THEN 
                                jsonb_build_object(
                                    'reason', 'legacy_payment_backfill',
                                    'payment_id', mu.payment_id,
                                    'payment_amount', mu.payment_amount,
                                    'payment_time', mu.payment_time
                                )
                            ELSE 
                                jsonb_build_object('reason', 'legacy_admin_grant_backfill')
                        END,
                        mu.subscription_end - (mu.exact_hours * INTERVAL '1 hour')
                    FROM missing_users mu
                    ON CONFLICT (beneficiary_user_id, source_type, source_id, entry_type) DO NOTHING
                    """
                )
            )

        # Verify results in DB
        async with self.sessions() as session:
            e1 = await session.scalar(
                select(EntitlementEntry).where(EntitlementEntry.beneficiary_user_id == user1_id)
            )
            self.assertIsNotNone(e1)
            self.assertEqual(e1.source_type, "payment")
            self.assertEqual(e1.source_id, f"legacy_payment_{payment1_id}")
            self.assertEqual(e1.entry_type, "manual_grant")
            self.assertIn(e1.hours_delta, (4, 5))
            self.assertEqual(e1.days_delta, 0)
            self.assertEqual(e1.metadata_["reason"], "legacy_payment_backfill")
            self.assertEqual(e1.metadata_["payment_id"], payment1_id)

            e2 = await session.scalar(
                select(EntitlementEntry).where(EntitlementEntry.beneficiary_user_id == user2_id)
            )
            self.assertIsNotNone(e2)
            self.assertEqual(e2.source_type, "admin")
            self.assertEqual(e2.source_id, f"legacy_admin_grant_{user2_id}")
            self.assertEqual(e2.entry_type, "manual_grant")
            self.assertIn(e2.hours_delta, (49, 50))
            self.assertEqual(e2.days_delta, 2)
            self.assertEqual(e2.metadata_["reason"], "legacy_admin_grant_backfill")

            # Balance projection verification: both must be tracked, no mismatch!
            snap1 = await get_subscription_balance_snapshot(session, user_id=user1_id, as_of=now)
            self.assertTrue(snap1.tracked)
            self.assertIsNone(snap1.failure_code)
            self.assertIn(snap1.remaining_bonus_hours, (4, 5))

            snap2 = await get_subscription_balance_snapshot(session, user_id=user2_id, as_of=now)
            self.assertTrue(snap2.tracked)
            self.assertIsNone(snap2.failure_code)
            self.assertIn(snap2.remaining_bonus_hours, (49, 50))
