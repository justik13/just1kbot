import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from services.subscription_balance_projector import (
    EntitlementEvent,
    LedgerEntry,
    TariffVersionSnapshot,
    project_subscription_balance,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


class SubscriptionBalanceProjectorTests(unittest.TestCase):
    def project(self, *, events, ledger=(), end=None):
        return project_subscription_balance(
            as_of=T0,
            subscription_end=end or T0 + timedelta(hours=24),
            entitlement_events=events,
            ledger_entries=ledger,
            tariff_versions={
                100: TariffVersionSnapshot(
                    id=100,
                    tariff_id=10,
                    duration_hours=24,
                    price_rub=Decimal("49"),
                    currency="RUB",
                )
            },
        )

    def test_account_purchase_is_projected_from_quote_backed_entries(self):
        event = EntitlementEvent(
            id=1,
            user_id=7,
            source_type="quote",
            source_id="55",
            entry_type="account_purchase_grant",
            hours_delta=24,
            created_at=T0,
        )
        ledger = LedgerEntry(
            id=11,
            user_id=7,
            entry_type="account_purchase",
            paid_hours_delta=24,
            paid_value_rub_delta=Decimal("49"),
            currency="RUB",
            tariff_version_id=100,
            quote_id=55,
            created_at=T0,
        )
        result = self.project(events=(event,), ledger=(ledger,))
        self.assertTrue(result.tracked)
        self.assertEqual(result.remaining_paid_hours, 24)
        self.assertEqual(result.remaining_paid_value_rub, Decimal("49"))
        self.assertEqual(result.source_entitlement_entry_ids, (1,))
        self.assertEqual(result.source_ledger_entry_ids, (11,))

    def test_purchase_without_matching_entitlement_fails_closed(self):
        ledger = LedgerEntry(
            id=11,
            user_id=7,
            entry_type="account_purchase",
            paid_hours_delta=24,
            paid_value_rub_delta=Decimal("49"),
            currency="RUB",
            tariff_version_id=100,
            quote_id=55,
            created_at=T0,
        )
        result = self.project(events=(), ledger=(ledger,))
        self.assertFalse(result.tracked)
        self.assertEqual(result.failure_code, "account_purchase_without_entitlement_grant")

    def test_bonus_hours_need_no_paid_value_entry(self):
        event = EntitlementEvent(
            id=2,
            user_id=7,
            source_type="referral",
            source_id="abc",
            entry_type="referral_user_bonus",
            hours_delta=12,
            created_at=T0,
        )
        result = self.project(
            events=(event,),
            end=T0 + timedelta(hours=12),
        )
        self.assertTrue(result.tracked)
        self.assertEqual(result.remaining_paid_hours, 0)
        self.assertEqual(result.remaining_bonus_hours, 12)

    def test_removed_payment_grant_shape_is_rejected(self):
        event = EntitlementEvent(
            id=3,
            user_id=7,
            source_type="payment",
            source_id="9",
            entry_type="payment_grant",
            hours_delta=24,
            created_at=T0,
        )
        result = self.project(events=(event,))
        self.assertFalse(result.tracked)
        self.assertEqual(result.failure_code, "invalid_entitlement_shape")

    def test_naive_projection_time_is_not_silently_accepted(self):
        event = EntitlementEvent(
            id=4,
            user_id=7,
            source_type="referral",
            source_id="abc",
            entry_type="referral_user_bonus",
            hours_delta=1,
            created_at=T0,
        )
        with self.assertRaises((TypeError, ValueError)):
            project_subscription_balance(
                as_of=T0.replace(tzinfo=None),
                subscription_end=T0,
                entitlement_events=(event,),
                ledger_entries=(),
                tariff_versions={},
            )

    def test_sub_hour_remaining_subscription_duration_is_tracked(self):
        # 30-minute fractional gap between entitlement coverage (1 hour) and subscription_end (1 hour 30 minutes)
        event = EntitlementEvent(
            id=10,
            user_id=7,
            source_type="admin",
            source_id="1",
            entry_type="manual_grant",
            hours_delta=1,
            created_at=T0,
        )
        result = project_subscription_balance(
            as_of=T0,
            subscription_end=T0 + timedelta(hours=1, minutes=30),
            entitlement_events=(event,),
            ledger_entries=(),
            tariff_versions={},
        )
        self.assertTrue(result.tracked)
        self.assertIsNone(result.failure_code)
        self.assertEqual(result.remaining_paid_hours, 0)
        self.assertEqual(result.remaining_bonus_hours, 1)


if __name__ == "__main__":
    unittest.main()
