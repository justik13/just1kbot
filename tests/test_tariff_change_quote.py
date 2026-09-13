import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from services.subscription_balance_projector import (
    ProjectedBonusLot,
    ProjectedPaidLot,
    SubscriptionBalanceSnapshot,
)
from services.tariff_change_quote import (
    SnapshotCanonicalizationError,
    balance_snapshot_fingerprint,
    calculate_subscription_remaining_value,
    calculate_surcharge_option,
    calculate_transfer_option,
)
from services.tariff_value_calculator import (
    TariffCalculationError,
    TariffVersionSnapshot,
    calculate_tariff_value,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def snapshot():
    paid = ProjectedPaidLot(
        entitlement_entry_id=2,
        paid_value_ledger_entry_id=20,
        tariff_version_id=200,
        original_paid_hours=720,
        original_paid_value_rub=Decimal(90),
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
            source_paid_value_rub=Decimal(150), source_tariff=TariffVersionSnapshot(1, 1, 720, Decimal(300)),
            target_tariff=TariffVersionSnapshot(2, 2, 720, Decimal(600)),
            confirmed_additional_payment_rub=Decimal(450), bonus_hours=24)
        downgrade = calculate_tariff_value(operation_type="change", source_paid_hours=720,
            source_paid_value_rub=Decimal(600), source_tariff=TariffVersionSnapshot(2, 2, 720, Decimal(600)),
            target_tariff=TariffVersionSnapshot(1, 1, 720, Decimal(300)),
            confirmed_additional_payment_rub=Decimal(0), bonus_hours=24)
        self.assertEqual(upgrade.required_payment_rub, Decimal(450))
        self.assertEqual(downgrade.required_payment_rub, Decimal(0))
        self.assertEqual(upgrade.retained_bonus_hours, 24)
        self.assertLess(upgrade.rounding_loss_hours, 1)
        self.assertLessEqual(upgrade.paid_value_after_rub,
                             upgrade.paid_value_before_rub + upgrade.required_payment_rub)

    def test_fractional_due_and_hours_rounding(self):
        result = calculate_tariff_value(operation_type="change", source_paid_hours=1,
            source_paid_value_rub=Decimal("0.01"),
            source_tariff=TariffVersionSnapshot(1, 1, 100, Decimal(1)),
            target_tariff=TariffVersionSnapshot(2, 2, 3, Decimal(2)),
            confirmed_additional_payment_rub=Decimal(2), bonus_hours=0)
        self.assertEqual(result.required_payment_rub, Decimal(2))
        self.assertEqual(result.resulting_paid_hours, 3)

    def test_unsupported_currency_and_arbitrary_duration_fail_closed(self):
        common = dict(operation_type="change", source_paid_hours=1,
            source_paid_value_rub=Decimal(1),
            source_tariff=TariffVersionSnapshot(1, 1, 24, Decimal(24)),
            target_tariff=TariffVersionSnapshot(2, 2, 24, Decimal(48)),
            confirmed_additional_payment_rub=Decimal(47), bonus_hours=0)
        with self.assertRaises(TariffCalculationError):
            calculate_tariff_value(**(common | {"target_tariff": TariffVersionSnapshot(2, 2, 24, Decimal(48), "USD")}))
        with self.assertRaises(TariffCalculationError):
            calculate_tariff_value(**(common | {"requested_duration_hours": 12}))

    def test_historical_value_is_authoritative_when_current_price_increased_or_decreased(self):
        for current_price in (Decimal(30), Decimal(300)):
            result = calculate_tariff_value(operation_type="change", source_paid_hours=10,
                source_paid_value_rub=Decimal(150),
                source_tariff=TariffVersionSnapshot(1, 9, 720, current_price),
                target_tariff=TariffVersionSnapshot(2, 10, 720, Decimal(200)),
                confirmed_additional_payment_rub=Decimal(50), bonus_hours=7)
            self.assertEqual(result.paid_value_before_rub, Decimal(150))
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

    def test_daily_rate_precision_and_no_integer_truncation(self):
        # 250 RUB / 30 days = 8.333333333333333... RUB/day (previously 8 RUB/day via //)
        rem = calculate_subscription_remaining_value(
            subscription_end=T0 + timedelta(days=29),
            price_rub=250,
            duration_days=30,
            as_of=T0,
        )
        self.assertEqual(rem.remaining_days, 29)
        self.assertEqual(rem.daily_rate, Decimal(250) / Decimal(30))
        self.assertAlmostEqual(float(rem.remaining_value), 241.666667, places=4)
        # Verify no truncation loss: previously 29 * 8 = 232 RUB (loss of ~9.67 RUB)
        self.assertGreater(rem.remaining_value, Decimal(240))

        # Promo tariff: 15 RUB / 30 days = 0.5 RUB/day (previously 0 via //)
        promo = calculate_subscription_remaining_value(
            subscription_end=T0 + timedelta(days=10),
            price_rub=15,
            duration_days=30,
            as_of=T0,
        )
        self.assertEqual(promo.daily_rate, Decimal("0.5"))
        self.assertEqual(promo.remaining_value, Decimal("5.0"))

    def test_transfer_option_precision_and_minimum_days(self):
        # Available: target_days >= 7
        calc = calculate_transfer_option(
            remaining_value=Decimal("241.67"),
            target_price_rub=Decimal(300),
            target_duration_days=30,
        )
        self.assertTrue(calc.is_available)
        self.assertEqual(calc.target_days, 24)
        self.assertEqual(calc.leftover_rub, 1)

        # Below threshold: target_days < 7
        calc_small = calculate_transfer_option(
            remaining_value=Decimal(60),
            target_price_rub=Decimal(300),
            target_duration_days=30,
        )
        self.assertFalse(calc_small.is_available)
        self.assertEqual(calc_small.target_days, 6)

    def test_surcharge_option_calculation(self):
        # Surcharge ceiling
        surcharge = calculate_surcharge_option(
            remaining_value=Decimal("241.67"),
            target_price_rub=Decimal(300),
        )
        self.assertEqual(surcharge, 59)

        # Zero surcharge when remaining value >= target price
        zero_surcharge = calculate_surcharge_option(
            remaining_value=Decimal(300),
            target_price_rub=Decimal(250),
        )
        self.assertEqual(zero_surcharge, 0)

    def test_bonus_lots_have_zero_monetary_value_and_do_not_reduce_surcharge(self):
        bonus = ProjectedBonusLot(
            entitlement_entry_id=3,
            source_type="quote",
            source_id="201",
            bonus_type="referral_user_bonus",
            original_hours=720,
            remaining_whole_hours=720,
            segment_start=T0,
            segment_end=T0 + timedelta(hours=720),
        )
        # Bonus lots explicitly have paid_value_rub = 0
        self.assertEqual(bonus.paid_value_rub, Decimal(0))

        # Snapshot with only bonus hours: paid value is 0
        bonus_snapshot = SubscriptionBalanceSnapshot(
            as_of=T0,
            tracked=True,
            failure_code=None,
            coverage_end=T0 + timedelta(hours=720),
            remaining_paid_hours=0,
            remaining_paid_value_rub=Decimal(0),
            remaining_bonus_hours=720,
            rounding_loss_hours=Decimal(0),
            paid_lots=(),
            bonus_lots=(bonus,),
            source_ledger_entry_ids=(),
            source_entitlement_entry_ids=(3,),
        )
        self.assertEqual(bonus_snapshot.remaining_paid_value_rub, Decimal(0))

        # Surcharge is full target price, not reduced by bonus time
        surcharge = calculate_surcharge_option(
            remaining_value=bonus_snapshot.remaining_paid_value_rub,
            target_price_rub=Decimal(300),
        )
        self.assertEqual(surcharge, 300)

        # Transfer is not available with 0 paid value
        transfer = calculate_transfer_option(
            remaining_value=bonus_snapshot.remaining_paid_value_rub,
            target_price_rub=Decimal(300),
            target_duration_days=30,
        )
        self.assertFalse(transfer.is_available)
        self.assertEqual(transfer.target_days, 0)

    def test_historical_lot_value_evaluated_correctly(self):
        # User bought at 90 RUB / 720 hours. 360 hours remain -> historical value is 45 RUB.
        # Even if current catalog price changed to 200 RUB, remaining value is based on lot
        paid = ProjectedPaidLot(
            entitlement_entry_id=2,
            paid_value_ledger_entry_id=20,
            tariff_version_id=200,
            original_paid_hours=720,
            original_paid_value_rub=Decimal(90),
            remaining_whole_hours=360,
            remaining_paid_value_rub=Decimal("45.000000"),
            segment_start=T0,
            segment_end=T0 + timedelta(hours=720),
        )
        hist_snapshot = SubscriptionBalanceSnapshot(
            as_of=T0,
            tracked=True,
            failure_code=None,
            coverage_end=T0 + timedelta(hours=720),
            remaining_paid_hours=360,
            remaining_paid_value_rub=Decimal("45.000000"),
            remaining_bonus_hours=0,
            rounding_loss_hours=Decimal(0),
            paid_lots=(paid,),
            bonus_lots=(),
            source_ledger_entry_ids=(20,),
            source_entitlement_entry_ids=(2,),
        )
        # Surcharge against target price 180 is 180 - 45 = 135 RUB
        surcharge = calculate_surcharge_option(
            remaining_value=hist_snapshot.remaining_paid_value_rub,
            target_price_rub=Decimal(180),
        )
        self.assertEqual(surcharge, 135)

    def test_full_snapshot_fingerprint_changes_on_lots_and_sources(self):
        value = snapshot()
        base_fp = balance_snapshot_fingerprint(
            user_id=7, subscription_end=value.coverage_end, snapshot=value
        )
        # Modify lot details
        changed_paid_lot = ProjectedPaidLot(
            entitlement_entry_id=2,
            paid_value_ledger_entry_id=20,
            tariff_version_id=201,  # changed version
            original_paid_hours=720,
            original_paid_value_rub=Decimal(90),
            remaining_whole_hours=300,
            remaining_paid_value_rub=Decimal("37.500000"),
            segment_start=T0,
            segment_end=T0 + timedelta(hours=720),
        )
        fp_lot_changed = balance_snapshot_fingerprint(
            user_id=7,
            subscription_end=value.coverage_end,
            snapshot=replace(value, paid_lots=(changed_paid_lot,)),
        )
        self.assertNotEqual(base_fp, fp_lot_changed)

        # Modify source ledger IDs
        fp_ledger_changed = balance_snapshot_fingerprint(
            user_id=7,
            subscription_end=value.coverage_end,
            snapshot=replace(value, source_ledger_entry_ids=(20, 21)),
        )
        self.assertNotEqual(base_fp, fp_ledger_changed)


if __name__ == "__main__":
    unittest.main()
