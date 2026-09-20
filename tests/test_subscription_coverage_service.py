"""Unit tests for SubscriptionCoverageService with mocked AsyncSession and repositories."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, patch

from config.constants import PERMANENT_END_DATE, PERMANENT_SUBSCRIPTION_DAYS
from config.enums import EntitlementGrantStatus, EntitlementGrantType
from database.models import EntitlementGrant, User
from services import subscription_coverage_service


class SubscriptionCoverageServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_session = AsyncMock()
        self.mock_session.flush = AsyncMock()
        self.mock_session.scalar = AsyncMock()
        self.sample_user = User(
            id=42,
            telegram_id=12345678,
            subscription_end=None,
            device_limit=1,
        )

    async def test_append_awg_grant_empty_grants(self):
        base_time = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)

        with patch(
            "database.repositories.entitlement_grants_repo.get_active_grants_for_user",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_get_grants, patch(
            "database.repositories.entitlement_grants_repo.create_grant",
            new_callable=AsyncMock,
        ) as mock_create_grant:
            mock_create_grant.side_effect = lambda session, **kwargs: EntitlementGrant(**kwargs)

            grant = await subscription_coverage_service.append_awg_grant(
                self.mock_session,
                user_id=42,
                duration_hours=720,
                source_type="purchase",
                source_id="101",
                paid_value_rub=Decimal("300.000000"),
                device_limit=2,
                as_of=base_time,
                locked_user=self.sample_user,
            )

            mock_get_grants.assert_awaited_once_with(self.mock_session, 42, for_update=True)
            mock_create_grant.assert_awaited_once()

            self.assertEqual(grant.coverage_start, base_time)
            self.assertEqual(grant.coverage_end, base_time + timedelta(hours=720))
            self.assertEqual(grant.original_duration_hours, 720)
            self.assertEqual(grant.paid_value_rub, Decimal("300.000000"))
            self.assertEqual(self.sample_user.subscription_end, base_time + timedelta(hours=720))
            self.assertEqual(self.sample_user.device_limit, 2)

    async def test_append_awg_grant_sequential_queue(self):
        base_time = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)
        existing_end = base_time + timedelta(days=15)
        self.sample_user.subscription_end = existing_end

        existing_grant = EntitlementGrant(
            id=1,
            user_id=42,
            service_type="awg",
            source_type="purchase",
            source_id="1",
            grant_type=EntitlementGrantType.PAID_PURCHASE,
            coverage_start=base_time - timedelta(days=15),
            coverage_end=existing_end,
            original_duration_hours=720,
            paid_value_rub=Decimal("300.000000"),
            status=EntitlementGrantStatus.ACTIVE,
        )

        with patch(
            "database.repositories.entitlement_grants_repo.get_active_grants_for_user",
            new_callable=AsyncMock,
            return_value=[existing_grant],
        ), patch(
            "database.repositories.entitlement_grants_repo.create_grant",
            new_callable=AsyncMock,
        ) as mock_create_grant:
            mock_create_grant.side_effect = lambda session, **kwargs: EntitlementGrant(**kwargs)

            grant = await subscription_coverage_service.append_awg_grant(
                self.mock_session,
                user_id=42,
                duration_hours=720,
                source_type="purchase",
                source_id="102",
                paid_value_rub=Decimal("300.000000"),
                device_limit=1,
                as_of=base_time,
                locked_user=self.sample_user,
            )

            self.assertEqual(grant.coverage_start, existing_end)
            self.assertEqual(grant.coverage_end, existing_end + timedelta(hours=720))
            self.assertEqual(self.sample_user.subscription_end, existing_end + timedelta(hours=720))

    async def test_append_awg_grant_permanent_protection(self):
        base_time = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)
        perm_grant = EntitlementGrant(
            id=1,
            user_id=42,
            service_type="awg",
            source_type="admin",
            source_id="perm",
            grant_type=EntitlementGrantType.ADMIN_GIFT,
            coverage_start=base_time,
            coverage_end=PERMANENT_END_DATE,
            original_duration_hours=PERMANENT_SUBSCRIPTION_DAYS * 24,
            paid_value_rub=Decimal("0.000000"),
            status=EntitlementGrantStatus.ACTIVE,
        )

        with patch(
            "database.repositories.entitlement_grants_repo.get_active_grants_for_user",
            new_callable=AsyncMock,
            return_value=[perm_grant],
        ):
            with self.assertRaises(ValueError):
                await subscription_coverage_service.append_awg_grant(
                    self.mock_session,
                    user_id=42,
                    duration_hours=720,
                    source_type="purchase",
                    source_id="103",
                    as_of=base_time,
                    locked_user=self.sample_user,
                )

    async def test_replace_awg_coverage_splits_paid_and_bonus(self):
        base_time = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)
        old_grant = EntitlementGrant(
            id=1,
            user_id=42,
            coverage_start=base_time - timedelta(days=10),
            coverage_end=base_time + timedelta(days=20),
            original_duration_hours=720,
            paid_value_rub=Decimal("300.000000"),
            status=EntitlementGrantStatus.ACTIVE,
        )

        with patch(
            "database.repositories.entitlement_grants_repo.get_active_grants_for_user",
            new_callable=AsyncMock,
            return_value=[old_grant],
        ), patch(
            "database.repositories.entitlement_grants_repo.revoke_grants_by_ids",
            new_callable=AsyncMock,
        ) as mock_revoke, patch(
            "database.repositories.entitlement_grants_repo.create_grant",
            new_callable=AsyncMock,
        ) as mock_create_grant:
            mock_create_grant.side_effect = lambda session, **kwargs: EntitlementGrant(**kwargs)

            paid_g, bonus_g = await subscription_coverage_service.replace_awg_coverage(
                self.mock_session,
                user_id=42,
                source_purchase_id=999,
                new_paid_hours=200,
                new_paid_value_rub=Decimal("450.000000"),
                retained_bonus_hours=50,
                target_tariff_id=5,
                target_tariff_version_id=12,
                target_device_limit=3,
                as_of=base_time,
                locked_user=self.sample_user,
            )

            mock_revoke.assert_awaited_once_with(self.mock_session, [1])
            self.assertIsNotNone(paid_g)
            self.assertEqual(paid_g.grant_type, EntitlementGrantType.PAID_CHANGE)
            self.assertEqual(paid_g.coverage_start, base_time)
            self.assertEqual(paid_g.coverage_end, base_time + timedelta(hours=200))
            self.assertEqual(paid_g.paid_value_rub, Decimal("450.000000"))

            self.assertIsNotNone(bonus_g)
            self.assertEqual(bonus_g.grant_type, EntitlementGrantType.BONUS_CHANGE)
            self.assertEqual(bonus_g.coverage_start, base_time + timedelta(hours=200))
            self.assertEqual(bonus_g.coverage_end, base_time + timedelta(hours=250))
            self.assertEqual(bonus_g.paid_value_rub, Decimal("0.000000"))

            self.assertEqual(self.sample_user.subscription_end, base_time + timedelta(hours=250))
            self.assertEqual(self.sample_user.current_tariff_id, 5)
            self.assertEqual(self.sample_user.device_limit, 3)

    async def test_reduce_awg_coverage_cascade(self):
        base_time = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)
        # Grant 1: [T, T+10d]
        g1 = EntitlementGrant(
            id=1,
            coverage_start=base_time,
            coverage_end=base_time + timedelta(days=10),
            status=EntitlementGrantStatus.ACTIVE,
        )
        # Grant 2: [T+10d, T+20d]
        g2 = EntitlementGrant(
            id=2,
            coverage_start=base_time + timedelta(days=10),
            coverage_end=base_time + timedelta(days=20),
            status=EntitlementGrantStatus.ACTIVE,
        )
        # Grant 3: [T+20d, T+30d]
        g3 = EntitlementGrant(
            id=3,
            coverage_start=base_time + timedelta(days=20),
            coverage_end=base_time + timedelta(days=30),
            status=EntitlementGrantStatus.ACTIVE,
        )

        # We reduce to T + 15 days (so g3 is fully revoked, g2 is truncated to T+15d, g1 untouched)
        target_end = base_time + timedelta(days=15)

        with patch(
            "database.repositories.entitlement_grants_repo.get_active_grants_for_user",
            new_callable=AsyncMock,
            return_value=[g1, g2, g3],
        ), patch(
            "database.repositories.entitlement_grants_repo.set_grant_status",
            new_callable=AsyncMock,
        ) as mock_set_status, patch(
            "database.repositories.entitlement_grants_repo.update_grant_coverage_end",
            new_callable=AsyncMock,
        ) as mock_update_end:
            await subscription_coverage_service.reduce_awg_coverage(
                self.mock_session,
                user_id=42,
                new_end=target_end,
                locked_user=self.sample_user,
            )

            mock_set_status.assert_awaited_once_with(self.mock_session, g3, EntitlementGrantStatus.REVOKED)
            mock_update_end.assert_awaited_once_with(self.mock_session, g2, target_end)
            self.assertEqual(self.sample_user.subscription_end, target_end)

    async def test_sync_user_subscription_projection(self):
        base_time = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)
        g1 = EntitlementGrant(
            id=1,
            coverage_start=base_time - timedelta(days=5),
            coverage_end=base_time + timedelta(days=25),
            status=EntitlementGrantStatus.ACTIVE,
        )

        with patch(
            "database.repositories.entitlement_grants_repo.get_active_grants_for_user",
            new_callable=AsyncMock,
            return_value=[g1],
        ):
            synced_end = await subscription_coverage_service.sync_user_subscription_projection(
                self.mock_session,
                user_id=42,
                as_of=base_time,
                locked_user=self.sample_user,
            )
            self.assertEqual(synced_end, base_time + timedelta(days=25))
            self.assertEqual(self.sample_user.subscription_end, base_time + timedelta(days=25))
