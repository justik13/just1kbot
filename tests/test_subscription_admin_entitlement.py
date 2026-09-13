"""Tests for subscription admin entitlement tracking and backfill."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
import unittest
from unittest.mock import AsyncMock, patch
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import EntitlementEntry, Tariff, User
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

    async def test_extend_subscription_permanent_matches_effective_days(self) -> None:
        from config.constants import PERMANENT_END_DATE, PERMANENT_SUBSCRIPTION_DAYS
        session = AsyncMock()
        session.add = unittest.mock.MagicMock()
        now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        user = User(
            id=42,
            telegram_id=123456,
            device_limit=2,
            current_tariff_id=10,
            subscription_end=now,
        )
        session.scalar.return_value = user

        with (
            patch("services.subscription.get_user_profiles_count", new_callable=AsyncMock) as mock_profiles,
            patch.object(SubscriptionService, "_sync_access_state", new_callable=AsyncMock),
            patch("services.subscription.invalidate_user_cache"),
            patch("services.subscription.now_utc", return_value=now),
        ):
            mock_profiles.return_value = 1
            updated_user = await SubscriptionService.extend_subscription(
                session=session,
                telegram_id=123456,
                days=PERMANENT_SUBSCRIPTION_DAYS,
                create_entitlement=True,
                admin_id=999,
                reason="permanent_grant",
            )

        self.assertEqual(updated_user.subscription_end, PERMANENT_END_DATE)
        added_obj = session.add.call_args[0][0]
        expected_days = (PERMANENT_END_DATE - now).days
        self.assertIsNone(added_obj.hours_delta)
        self.assertEqual(added_obj.days_delta, expected_days)
        self.assertEqual(added_obj.metadata_, {"admin_id": 999, "reason": "permanent_grant"})

    async def test_extend_subscription_permanent_already_permanent_no_grant(self) -> None:
        from config.constants import PERMANENT_END_DATE, PERMANENT_SUBSCRIPTION_DAYS
        session = AsyncMock()
        session.add = unittest.mock.MagicMock()
        user = User(
            id=42,
            telegram_id=123456,
            device_limit=2,
            current_tariff_id=10,
            subscription_end=PERMANENT_END_DATE,
        )
        session.scalar.return_value = user

        with (
            patch("services.subscription.get_user_profiles_count", new_callable=AsyncMock) as mock_profiles,
            patch.object(SubscriptionService, "_sync_access_state", new_callable=AsyncMock),
            patch("services.subscription.invalidate_user_cache"),
            patch("services.subscription.now_utc", return_value=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        ):
            mock_profiles.return_value = 1
            updated_user = await SubscriptionService.extend_subscription(
                session=session,
                telegram_id=123456,
                days=PERMANENT_SUBSCRIPTION_DAYS,
                create_entitlement=True,
                admin_id=999,
                reason="permanent_regrant",
            )

        self.assertEqual(updated_user.subscription_end, PERMANENT_END_DATE)
        session.add.assert_not_called()

    async def test_extend_subscription_permanent_with_active_hours_base_end(self) -> None:
        from config.constants import PERMANENT_END_DATE, PERMANENT_SUBSCRIPTION_DAYS
        session = AsyncMock()
        session.add = unittest.mock.MagicMock()
        now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        active_end = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        user = User(
            id=42,
            telegram_id=123456,
            device_limit=2,
            current_tariff_id=10,
            subscription_end=active_end,
        )
        session.scalar.return_value = user

        with (
            patch("services.subscription.get_user_profiles_count", new_callable=AsyncMock) as mock_profiles,
            patch.object(SubscriptionService, "_sync_access_state", new_callable=AsyncMock),
            patch("services.subscription.invalidate_user_cache"),
            patch("services.subscription.now_utc", return_value=now),
        ):
            mock_profiles.return_value = 1
            updated_user = await SubscriptionService.extend_subscription(
                session=session,
                telegram_id=123456,
                days=PERMANENT_SUBSCRIPTION_DAYS,
                create_entitlement=True,
                admin_id=999,
                reason="permanent_grant",
            )

        self.assertEqual(updated_user.subscription_end, PERMANENT_END_DATE)
        added_obj = session.add.call_args[0][0]
        expected_days = (PERMANENT_END_DATE - active_end).days
        self.assertIsNone(added_obj.hours_delta)
        self.assertEqual(added_obj.days_delta, expected_days)
        self.assertGreater(added_obj.days_delta, 0)


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class SubscriptionAdminEntitlementIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(DB, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_manual_grant_allows_quote_calculation_and_settlement(self) -> None:
        now = now_utc().replace(microsecond=0)
        tg_id = int(uuid.uuid4().int % 1000000000)
        async with self.sessions.begin() as session:
            # 1. Create base and upgrade tariffs
            t1 = Tariff(
                name=f"Base Tariff {tg_id}",
                price_rub=Decimal("300.00"),
                duration_days=30,
                device_limit=2,
                is_active=True,
            )
            t2 = Tariff(
                name=f"Pro Tariff {tg_id}",
                price_rub=Decimal("600.00"),
                duration_days=30,
                device_limit=5,
                is_active=True,
            )
            session.add_all([t1, t2])
            await session.flush()
            # 3. Create user
            user = User(
                telegram_id=tg_id,
                username=f"manual_grant_user_{tg_id}",
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
            quote_res = await create_tariff_change_quote(
                session,
                user_id=user_id,
                target_tariff_id=t2_id,
                as_of=now,
            )
            self.assertIsNone(quote_res.failure_code)
            self.assertIsNotNone(quote_res.quote)
            self.assertEqual(quote_res.quote.status, "active")
            self.assertGreater(quote_res.quote.amount_due_rub, Decimal("0.00"))

    async def test_exact_hours_and_honest_legacy_backfill_logic(self) -> None:
        now = now_utc().replace(microsecond=0)
        tg_id_1 = int(uuid.uuid4().int % 1000000000)
        tg_id_2 = int(uuid.uuid4().int % 1000000000)
        async with self.sessions.begin() as session:
            # User 1: Has active sub with 5 hours remaining (less than 24h, would fail if CEIL to 24h)
            user1 = User(
                telegram_id=tg_id_1,
                username=f"legacy_user_5h_{tg_id_1}",
                device_limit=2,
                subscription_end=now + timedelta(hours=5),
            )
            # User 2: Has active sub with 48 hours remaining (2 days)
            user2 = User(
                telegram_id=tg_id_2,
                username=f"legacy_user_48h_{tg_id_2}",
                device_limit=3,
                subscription_end=now + timedelta(hours=48),
            )
            session.add_all([user1, user2])
            await session.flush()

            user1_id = user1.id
            user2_id = user2.id

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
                       AND reversed_entry_id IS NULL
                       AND (
                         (days_delta = 0 AND hours_delta > 0)
                         OR
                         (days_delta > 0 AND (hours_delta IS NULL OR hours_delta = days_delta * 24))
                       ))
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

            # Backfill active users without entitlements honestly
            await session.execute(
                text(
                    """
                    WITH missing_users AS (
                        SELECT 
                            u.id AS user_id,
                            u.device_limit,
                            u.current_tariff_id,
                            u.subscription_end,
                            GREATEST(1, CEIL(EXTRACT(EPOCH FROM (u.subscription_end - NOW())) / 3600)::int) AS exact_hours
                        FROM users u
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
                        'admin',
                        'legacy_0027_grant_' || mu.user_id,
                        'manual_grant',
                        CASE WHEN mu.exact_hours % 24 = 0 THEN mu.exact_hours / 24 ELSE 0 END,
                        mu.exact_hours,
                        COALESCE(mu.device_limit, 1),
                        mu.current_tariff_id,
                        jsonb_build_object('reason', 'legacy_active_subscription_backfill'),
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
            self.assertEqual(e1.source_type, "admin")
            self.assertEqual(e1.source_id, f"legacy_0027_grant_{user1_id}")
            self.assertEqual(e1.entry_type, "manual_grant")
            self.assertEqual(e1.hours_delta, 5)
            self.assertEqual(e1.days_delta, 0)
            self.assertEqual(e1.metadata_["reason"], "legacy_active_subscription_backfill")

            e2 = await session.scalar(
                select(EntitlementEntry).where(EntitlementEntry.beneficiary_user_id == user2_id)
            )
            self.assertIsNotNone(e2)
            self.assertEqual(e2.source_type, "admin")
            self.assertEqual(e2.source_id, f"legacy_0027_grant_{user2_id}")
            self.assertEqual(e2.entry_type, "manual_grant")
            self.assertEqual(e2.hours_delta, 48)
            self.assertEqual(e2.metadata_["reason"], "legacy_active_subscription_backfill")

            # Balance projection verification: both must be tracked, no mismatch!
            snap1 = await get_subscription_balance_snapshot(session, user_id=user1_id, as_of=now)
            self.assertTrue(snap1.tracked)
            self.assertIsNone(snap1.failure_code)
            self.assertEqual(snap1.remaining_bonus_hours, 5)

            snap2 = await get_subscription_balance_snapshot(session, user_id=user2_id, as_of=now)
            self.assertTrue(snap2.tracked)
            self.assertIsNone(snap2.failure_code)
            self.assertEqual(snap2.remaining_bonus_hours, 48)

    async def test_extend_subscription_permanent_active_hours_integration(self) -> None:
        from config.constants import PERMANENT_END_DATE, PERMANENT_SUBSCRIPTION_DAYS
        now = now_utc().replace(minute=0, second=0, microsecond=0)
        # Active subscription with 10 days and 12 hours remaining (non-zero hours)
        active_end = now + timedelta(days=10, hours=12)
        tg_id = int(uuid.uuid4().int % 1000000000)

        async with self.sessions.begin() as session:
            user = User(
                telegram_id=tg_id,
                username=f"perm_user_{tg_id}",
                device_limit=2,
                subscription_end=active_end,
            )
            session.add(user)
            await session.flush()
            user_id = user.id

            updated_user = await SubscriptionService.extend_subscription(
                session,
                telegram_id=tg_id,
                days=PERMANENT_SUBSCRIPTION_DAYS,
                create_entitlement=True,
                admin_id=999,
                reason="permanent_active_hours",
            )
            self.assertEqual(updated_user.subscription_end, PERMANENT_END_DATE)

        async with self.sessions() as session:
            ent = await session.scalar(
                select(EntitlementEntry).where(EntitlementEntry.beneficiary_user_id == user_id)
            )
            self.assertIsNotNone(ent)
            expected_days = (PERMANENT_END_DATE - active_end).days
            self.assertIsNone(ent.hours_delta)
            self.assertEqual(ent.days_delta, expected_days)
            self.assertGreater(ent.days_delta, 0)

            # Projector must track successfully
            snapshot = await get_subscription_balance_snapshot(session, user_id=user_id, as_of=now)
            self.assertTrue(snapshot.tracked)
            self.assertIsNone(snapshot.failure_code)

    async def test_ck_entitlement_entries_shape_strict_validation(self) -> None:
        from sqlalchemy.exc import IntegrityError
        now = now_utc()
        tg_id = int(uuid.uuid4().int % 1000000000)
        async with self.sessions.begin() as session:
            test_user = User(
                telegram_id=tg_id,
                username=f"constraint_test_user_{tg_id}",
                device_limit=1,
                subscription_end=now + timedelta(days=10),
            )
            session.add(test_user)
            await session.flush()
            user_id = test_user.id

        # 1. Invalid combination: days_delta = 30 and hours_delta = 5 must be rejected
        async with self.sessions.begin() as session:
            bad_entry = EntitlementEntry(
                beneficiary_user_id=user_id,
                source_type="admin",
                source_id="test_invalid_shape_1",
                entry_type="manual_grant",
                days_delta=30,
                hours_delta=5,
                device_limit_snapshot=1,
                tariff_id_snapshot=1,
            )
            session.add(bad_entry)
            with self.assertRaises(IntegrityError):
                await session.flush()

        # 2. Valid combination: sub-day grant with days_delta = 0 and hours_delta = 5 must succeed
        async with self.sessions.begin() as session:
            valid_subday = EntitlementEntry(
                beneficiary_user_id=user_id,
                source_type="admin",
                source_id="test_valid_subday_2",
                entry_type="manual_grant",
                days_delta=0,
                hours_delta=5,
                device_limit_snapshot=1,
                tariff_id_snapshot=1,
            )
            session.add(valid_subday)
            await session.flush()

        # 3. Valid combination: exact days grant with days_delta = 2 and hours_delta = 48 must succeed
        async with self.sessions.begin() as session:
            valid_days = EntitlementEntry(
                beneficiary_user_id=user_id,
                source_type="admin",
                source_id="test_valid_days_3",
                entry_type="manual_grant",
                days_delta=2,
                hours_delta=48,
                device_limit_snapshot=1,
                tariff_id_snapshot=1,
            )
            session.add(valid_days)
            await session.flush()
