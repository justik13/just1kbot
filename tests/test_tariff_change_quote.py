import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from services.subscription_balance_projector import (
    ProjectedBonusLot, ProjectedPaidLot, SubscriptionBalanceSnapshot,
)
from services.tariff_change_quote import balance_snapshot_fingerprint
from services.tariff_value_calculator import TariffVersionSnapshot, calculate_tariff_value

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def snapshot():
    paid = ProjectedPaidLot(2, 20, 200, 100, 720, Decimal("90"), 300,
                           Decimal("37.500000"), T0, T0 + timedelta(hours=720))
    bonus = ProjectedBonusLot(3, "payment", "201", "referral_user_bonus", 24, 24,
                              T0 + timedelta(hours=720), T0 + timedelta(hours=744))
    return SubscriptionBalanceSnapshot(T0, True, None, T0 + timedelta(hours=744), 300,
        Decimal("37.500000"), 24, Decimal("0.25"), (paid,), (bonus,), (20,), (3, 2))


class TariffChangeQuoteTests(unittest.TestCase):
    def test_historical_value_is_authoritative_when_current_price_increased_or_decreased(self):
        for current_price in (Decimal("30"), Decimal("300")):
            result = calculate_tariff_value(operation_type="change", source_paid_hours=10,
                source_paid_value_rub=Decimal("150"),
                source_tariff=TariffVersionSnapshot(1, 9, 720, current_price),
                target_tariff=TariffVersionSnapshot(2, 10, 720, Decimal("200")),
                confirmed_additional_payment_rub=Decimal("50"), bonus_hours=7)
            self.assertEqual(result.paid_value_before_rub, Decimal("150"))
            self.assertEqual(result.retained_bonus_hours, 7)

    def test_fingerprint_is_deterministic_and_ids_are_order_independent(self):
        value = snapshot()
        first = balance_snapshot_fingerprint(user_id=7, subscription_end=value.coverage_end, snapshot=value)
        reordered = replace(value, source_entitlement_entry_ids=(2, 3))
        self.assertEqual(first, balance_snapshot_fingerprint(
            user_id=7, subscription_end=value.coverage_end, snapshot=reordered))
        self.assertRegex(first, r"^[0-9a-f]{64}$")

    def test_each_economic_aggregate_changes_fingerprint(self):
        value = snapshot()
        original = balance_snapshot_fingerprint(user_id=7, subscription_end=value.coverage_end, snapshot=value)
        changes = (
            replace(value, remaining_paid_hours=301),
            replace(value, remaining_paid_value_rub=Decimal("37.51")),
            replace(value, remaining_bonus_hours=25),
            replace(value, rounding_loss_hours=Decimal("0.5")),
        )
        for changed in changes:
            self.assertNotEqual(original, balance_snapshot_fingerprint(
                user_id=7, subscription_end=value.coverage_end, snapshot=changed))


if __name__ == "__main__":
    unittest.main()
