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
    calculate_surcharge_option,
    calculate_transfer_option,
    create_tariff_change_quote,
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


class TariffChangeQuoteTests(unittest.IsolatedAsyncioTestCase):
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

    def test_multi_lot_historical_valuation_and_calculation(self):
        lot1 = ProjectedPaidLot(
            entitlement_entry_id=1,
            paid_value_ledger_entry_id=10,
            tariff_version_id=101,
            original_paid_hours=720,
            original_paid_value_rub=Decimal(90),
            remaining_whole_hours=360,
            remaining_paid_value_rub=Decimal("45.000000"),
            segment_start=T0,
            segment_end=T0 + timedelta(hours=360),
        )
        lot2 = ProjectedPaidLot(
            entitlement_entry_id=2,
            paid_value_ledger_entry_id=20,
            tariff_version_id=102,
            original_paid_hours=720,
            original_paid_value_rub=Decimal(150),
            remaining_whole_hours=720,
            remaining_paid_value_rub=Decimal("150.000000"),
            segment_start=T0 + timedelta(hours=360),
            segment_end=T0 + timedelta(hours=1080),
        )
        multi_snapshot = SubscriptionBalanceSnapshot(
            as_of=T0,
            tracked=True,
            failure_code=None,
            coverage_end=T0 + timedelta(hours=1080),
            remaining_paid_hours=1080,
            remaining_paid_value_rub=lot1.remaining_paid_value_rub + lot2.remaining_paid_value_rub,
            remaining_bonus_hours=0,
            rounding_loss_hours=Decimal(0),
            paid_lots=(lot1, lot2),
            bonus_lots=(),
            source_ledger_entry_ids=(10, 20),
            source_entitlement_entry_ids=(1, 2),
        )
        self.assertEqual(multi_snapshot.remaining_paid_value_rub, Decimal("195.000000"))

        # Transfer calculation with 195 RUB towards 300 RUB / 30d (10 RUB/d)
        transfer = calculate_transfer_option(
            remaining_value=multi_snapshot.remaining_paid_value_rub,
            target_price_rub=Decimal(300),
            target_duration_days=30,
        )
        self.assertTrue(transfer.is_available)
        self.assertEqual(transfer.target_days, 19)
        self.assertEqual(transfer.leftover_rub, 5)

        # Surcharge calculation: 300 - 195 = 105 RUB
        surcharge = calculate_surcharge_option(
            remaining_value=multi_snapshot.remaining_paid_value_rub,
            target_price_rub=Decimal(300),
        )
        self.assertEqual(surcharge, 105)

    def test_sub_ruble_residue_and_precision_boundaries(self):
        # Sub-ruble: 0.99 RUB cannot buy any days, surcharge ceiling rounds up
        transfer_sub = calculate_transfer_option(
            remaining_value=Decimal("0.99"),
            target_price_rub=Decimal(300),
            target_duration_days=30,
        )
        self.assertFalse(transfer_sub.is_available)
        self.assertEqual(transfer_sub.target_days, 0)
        self.assertEqual(transfer_sub.leftover_rub, 0)

        surcharge_sub = calculate_surcharge_option(
            remaining_value=Decimal("0.99"),
            target_price_rub=Decimal(300),
        )
        # 300 - 0.99 = 299.01 -> ceiling = 300
        self.assertEqual(surcharge_sub, 300)

        # Exact boundary: 70.01 RUB -> target_days = 7, available!
        transfer_boundary = calculate_transfer_option(
            remaining_value=Decimal("70.01"),
            target_price_rub=Decimal(300),
            target_duration_days=30,
        )
        self.assertTrue(transfer_boundary.is_available)
        self.assertEqual(transfer_boundary.target_days, 7)
        self.assertEqual(transfer_boundary.leftover_rub, 0)

        # 69.99 RUB -> target_days = 6, below 7 days threshold!
        transfer_below = calculate_transfer_option(
            remaining_value=Decimal("69.99"),
            target_price_rub=Decimal(300),
            target_duration_days=30,
        )
        self.assertFalse(transfer_below.is_available)
        self.assertEqual(transfer_below.target_days, 6)
        self.assertEqual(transfer_below.leftover_rub, 9)

    def test_mixed_paid_and_bonus_lots_zero_monetization(self):
        paid = ProjectedPaidLot(
            entitlement_entry_id=1,
            paid_value_ledger_entry_id=10,
            tariff_version_id=101,
            original_paid_hours=720,
            original_paid_value_rub=Decimal(90),
            remaining_whole_hours=360,
            remaining_paid_value_rub=Decimal("45.000000"),
            segment_start=T0,
            segment_end=T0 + timedelta(hours=360),
        )
        bonus = ProjectedBonusLot(
            entitlement_entry_id=2,
            source_type="quote",
            source_id="202",
            bonus_type="referral_user_bonus",
            original_hours=720,
            remaining_whole_hours=720,
            segment_start=T0 + timedelta(hours=360),
            segment_end=T0 + timedelta(hours=1080),
        )
        mixed_snapshot = SubscriptionBalanceSnapshot(
            as_of=T0,
            tracked=True,
            failure_code=None,
            coverage_end=T0 + timedelta(hours=1080),
            remaining_paid_hours=360,
            remaining_paid_value_rub=Decimal("45.000000"),
            remaining_bonus_hours=720,
            rounding_loss_hours=Decimal(0),
            paid_lots=(paid,),
            bonus_lots=(bonus,),
            source_ledger_entry_ids=(10,),
            source_entitlement_entry_ids=(1, 2),
        )
        # Surcharge against 300 RUB must only deduct the 45 RUB paid lot, not the 720 bonus hours
        surcharge = calculate_surcharge_option(
            remaining_value=mixed_snapshot.remaining_paid_value_rub,
            target_price_rub=Decimal(300),
        )
        self.assertEqual(surcharge, 255)

        # Transfer option uses only paid value: 45 // 10 = 4 days (< 7d threshold)
        transfer = calculate_transfer_option(
            remaining_value=mixed_snapshot.remaining_paid_value_rub,
            target_price_rub=Decimal(300),
            target_duration_days=30,
        )
        self.assertFalse(transfer.is_available)
        self.assertEqual(transfer.target_days, 4)

    async def test_create_tariff_change_quote_invalid_option_type(self):
        from unittest.mock import AsyncMock
        mock_session = AsyncMock()
        res = await create_tariff_change_quote(
            mock_session,
            user_id=1,
            target_tariff_id=2,
            as_of=T0,
            option_type="invalid_option",
        )
        self.assertEqual(res.failure_code, "invalid_option_type")


if __name__ == "__main__":
    unittest.main()
