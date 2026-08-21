import unittest
from decimal import Decimal

from services.tariff_value_calculator import (
    TariffCalculationError,
    TariffVersionSnapshot,
    calculate_tariff_value,
)


def snap(tariff_id=1, hours=720, price="300", currency="RUB", version=1):
    return TariffVersionSnapshot(tariff_id, version, hours, Decimal(price), currency)


class TariffValueCalculatorTests(unittest.TestCase):
    def test_purchase(self):
        result = calculate_tariff_value(operation_type="purchase", source_paid_hours=0, source_paid_value_rub=0,
            source_tariff=None, target_tariff=snap(), confirmed_additional_payment_rub=300, bonus_hours=24)
        self.assertEqual((result.reason_code, result.resulting_paid_hours), ("purchase", 720))
        self.assertEqual(result.retained_bonus_hours, 24)

    def test_same_tariff_renew(self):
        result = calculate_tariff_value(operation_type="renew", source_paid_hours=120, source_paid_value_rub=50,
            source_tariff=snap(), target_tariff=snap(version=2), confirmed_additional_payment_rub=300, bonus_hours=0)
        self.assertEqual(result.reason_code, "renew")
        self.assertEqual(result.resulting_paid_hours, 840)

    def test_upgrade_and_downgrade(self):
        upgrade = calculate_tariff_value(operation_type="change", source_paid_hours=360, source_paid_value_rub=150,
            source_tariff=snap(), target_tariff=snap(2, 720, "600"), confirmed_additional_payment_rub=450, bonus_hours=7)
        downgrade = calculate_tariff_value(operation_type="change", source_paid_hours=360, source_paid_value_rub=300,
            source_tariff=snap(2, 720, "600"), target_tariff=snap(), confirmed_additional_payment_rub=0, bonus_hours=7)
        self.assertEqual(upgrade.reason_code, "upgrade")
        self.assertEqual(downgrade.reason_code, "downgrade")
        self.assertEqual(upgrade.retained_bonus_hours, 7)

    def test_due_rounds_up_and_hours_round_down(self):
        result = calculate_tariff_value(operation_type="change", source_paid_hours=1, source_paid_value_rub=Decimal("0.01"),
            source_tariff=snap(hours=100, price="1"), target_tariff=snap(2, 3, "2"),
            confirmed_additional_payment_rub=Decimal(2), bonus_hours=0)
        self.assertEqual(result.required_payment_rub, Decimal(2))
        self.assertEqual(result.resulting_paid_hours, 3)
        self.assertLess(result.rounding_loss_hours, 1)

    def test_rejections(self):
        base = dict(operation_type="purchase", source_paid_hours=0, source_paid_value_rub=0, source_tariff=None,
                    target_tariff=snap(), confirmed_additional_payment_rub=300, bonus_hours=0)
        for replacement in (
            {"confirmed_additional_payment_rub": -1}, {"target_tariff": snap(hours=0)},
            {"target_tariff": snap(currency="USD")}, {"bonus_value_rub": 1},
            {"requested_duration_hours": 12}, {"confirmed_additional_payment_rub": 1.2},
            {"operation_type": "renew", "source_tariff": snap(price="0"),
             "source_paid_hours": 1, "source_paid_value_rub": 0},
        ):
            with self.subTest(replacement=replacement), self.assertRaises(TariffCalculationError):
                calculate_tariff_value(**(base | replacement))

    def test_exact_frozen_due_and_operation_shapes(self):
        for operation, source, source_hours, source_value, confirmed in (
            ("purchase", None, 0, 0, Decimal(290)),
            ("purchase", None, 0, 0, Decimal(310)),
            ("purchase", snap(), 1, Decimal("0.40"), Decimal(300)),
            ("renew", snap(2), 1, Decimal("0.40"), Decimal(300)),
            ("change", snap(), 1, Decimal("0.40"), Decimal(300)),
        ):
            with self.subTest(operation=operation), self.assertRaises(TariffCalculationError):
                calculate_tariff_value(operation_type=operation,
                    source_paid_hours=source_hours, source_paid_value_rub=source_value,
                    source_tariff=source, target_tariff=snap(),
                    confirmed_additional_payment_rub=confirmed, bonus_hours=0)

    def test_property_style_value_invariant_matrix(self):
        for source_price in ("0.01", "99.99", "300", "10000.01"):
            for target_price in ("0.01", "100", "599.95", "9999.99"):
                for hours in (1, 24, 719, 720, 8760):
                    for remaining in (0, 1, hours // 2, hours):
                        source = snap(1, hours, source_price)
                        source_value = Decimal(remaining) * Decimal(source_price) / hours
                        result = calculate_tariff_value(
                            operation_type="change", source_paid_hours=remaining, source_paid_value_rub=source_value,
                            source_tariff=source, target_tariff=snap(2, hours, target_price),
                            confirmed_additional_payment_rub=max(Decimal(0), Decimal(target_price) - source_value).quantize(Decimal(1), rounding=__import__("decimal").ROUND_CEILING), bonus_hours=168)
                        self.assertTrue(result.invariant_holds)
                        self.assertLessEqual(result.paid_value_after_rub,
                                             source_value + result.required_payment_rub)
                        self.assertLess(result.rounding_loss_hours, 1)


if __name__ == "__main__":
    unittest.main()
