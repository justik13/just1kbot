import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from services.subscription_balance_projector import (
    ProjectedBonusLot, ProjectedPaidLot, SubscriptionBalanceSnapshot,
)
from services.tariff_change_quote import SnapshotCanonicalizationError, balance_snapshot_fingerprint
from services.tariff_value_calculator import TariffCalculationError, TariffVersionSnapshot, calculate_tariff_value

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def snapshot():
    paid = ProjectedPaidLot(
        entitlement_entry_id=2,
        paid_value_ledger_entry_id=20,
        tariff_version_id=200,
        original_paid_hours=720,
        original_paid_value_rub=Decimal("90"),
        remaining_whole_hours=300,
        remaining_paid_value_rub=Decimal("37.500000"),
        segment_start=T0,
        segment_end=T0 + timedelta(hours=720),
    )
    bonus = ProjectedBonusLot(
        entitlement_entry_id=3,
        source_type="quote",
        source_id="201",
        bonus_type="referral_user_bonus",
        original_hours=24,
        remaining_whole_hours=24,
        segment_start=T0 + timedelta(hours=720),
        segment_end=T0 + timedelta(hours=744),
    )
    return SubscriptionBalanceSnapshot(
        as_of=T0,
        tracked=True,
        failure_code=None,
        coverage_end=T0 + timedelta(hours=744),
        remaining_paid_hours=300,
        remaining_paid_value_rub=Decimal("37.500000"),
        remaining_bonus_hours=24,
        rounding_loss_hours=Decimal("0.25"),
        paid_lots=(paid,),
        bonus_lots=(bonus,),
        source_ledger_entry_ids=(20,),
        source_entitlement_entry_ids=(3, 2),
    )


class TariffChangeQuoteTests(unittest.TestCase):
    def test_change_calculation_contract(self):
        upgrade = calculate_tariff_value(operation_type="change", source_paid_hours=360,
            source_paid_value_rub=Decimal("150"), source_tariff=TariffVersionSnapshot(1, 1, 720, Decimal("300")),
            target_tariff=TariffVersionSnapshot(2, 2, 720, Decimal("600")),
            confirmed_additional_payment_rub=Decimal("450"), bonus_hours=24)
        downgrade = calculate_tariff_value(operation_type="change", source_paid_hours=720,
            source_paid_value_rub=Decimal("600"), source_tariff=TariffVersionSnapshot(2, 2, 720, Decimal("600")),
            target_tariff=TariffVersionSnapshot(1, 1, 720, Decimal("300")),
            confirmed_additional_payment_rub=Decimal("0"), bonus_hours=24)
        self.assertEqual(upgrade.required_payment_rub, Decimal("450"))
        self.assertEqual(downgrade.required_payment_rub, Decimal("0"))
        self.assertEqual(upgrade.retained_bonus_hours, 24)
        self.assertLess(upgrade.rounding_loss_hours, 1)
        self.assertLessEqual(upgrade.paid_value_after_rub,
                             upgrade.paid_value_before_rub + upgrade.required_payment_rub)

    def test_fractional_due_and_hours_rounding(self):
        result = calculate_tariff_value(operation_type="change", source_paid_hours=1,
            source_paid_value_rub=Decimal("0.01"),
            source_tariff=TariffVersionSnapshot(1, 1, 100, Decimal("1")),
            target_tariff=TariffVersionSnapshot(2, 2, 3, Decimal("2")),
            confirmed_additional_payment_rub=Decimal("2"), bonus_hours=0)
        self.assertEqual(result.required_payment_rub, Decimal("2"))
        self.assertEqual(result.resulting_paid_hours, 3)

    def test_unsupported_currency_and_arbitrary_duration_fail_closed(self):
        common = dict(operation_type="change", source_paid_hours=1,
            source_paid_value_rub=Decimal("1"),
            source_tariff=TariffVersionSnapshot(1, 1, 24, Decimal("24")),
            target_tariff=TariffVersionSnapshot(2, 2, 24, Decimal("48")),
            confirmed_additional_payment_rub=Decimal("47"), bonus_hours=0)
        with self.assertRaises(TariffCalculationError):
            calculate_tariff_value(**(common | {"target_tariff": TariffVersionSnapshot(2, 2, 24, Decimal("48"), "USD")}))
        with self.assertRaises(TariffCalculationError):
            calculate_tariff_value(**(common | {"requested_duration_hours": 12}))

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

    def test_decimal_and_timezone_canonicalization(self):
        value = snapshot()
        variants = [replace(value, remaining_paid_value_rub=Decimal(text))
                    for text in ("37.5", "37.500000", "037.5000")]
        hashes = {balance_snapshot_fingerprint(user_id=7, subscription_end=value.coverage_end,
                                               snapshot=item) for item in variants}
        self.assertEqual(len(hashes), 1)
        offset = timezone(timedelta(hours=3))
        shifted = replace(value, as_of=value.as_of.astimezone(offset))
        self.assertEqual(balance_snapshot_fingerprint(user_id=7, subscription_end=value.coverage_end, snapshot=value),
                         balance_snapshot_fingerprint(user_id=7, subscription_end=value.coverage_end.astimezone(offset), snapshot=shifted))

    def test_naive_or_nonfinite_values_are_rejected(self):
        value = snapshot()
        with self.assertRaises(SnapshotCanonicalizationError):
            balance_snapshot_fingerprint(user_id=7, subscription_end=value.coverage_end,
                                         snapshot=replace(value, as_of=value.as_of.replace(tzinfo=None)))
        with self.assertRaises(SnapshotCanonicalizationError):
            balance_snapshot_fingerprint(user_id=7, subscription_end=value.coverage_end,
                                         snapshot=replace(value, remaining_paid_value_rub=Decimal("NaN")))

    def test_balance_as_of_is_fingerprinted(self):
        value = snapshot()
        first = balance_snapshot_fingerprint(user_id=7, subscription_end=value.coverage_end, snapshot=value)
        second = balance_snapshot_fingerprint(user_id=7, subscription_end=value.coverage_end,
            snapshot=replace(value, as_of=value.as_of + timedelta(seconds=1)))
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
