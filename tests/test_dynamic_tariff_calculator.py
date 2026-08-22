import unittest
from types import SimpleNamespace

from bot.keyboards.payment import (
    format_dynamic_tariff_button,
    get_tariff_duration_keyboard,
    get_renew_keyboard,
)


class TestDynamicTariffCalculator(unittest.TestCase):
    def setUp(self):
        self.t7 = SimpleNamespace(id=1, duration_days=7, price_rub=90)
        self.t30 = SimpleNamespace(id=2, duration_days=30, price_rub=150)
        self.t90 = SimpleNamespace(id=3, duration_days=90, price_rub=400)
        self.t180 = SimpleNamespace(id=4, duration_days=180, price_rub=700)
        self.t365 = SimpleNamespace(id=5, duration_days=365, price_rub=1400)

    def test_base_tariff_has_no_discount_badge(self):
        text = format_dynamic_tariff_button(self.t30, self.t30)
        self.assertEqual(text, '⏱ 30 дн. — 150 ₽')

    def test_90_days_savings_and_discount(self):
        text = format_dynamic_tariff_button(self.t90, self.t30)
        self.assertEqual(text, '⏱ 90 дн. — 400 ₽ (133 ₽/мес • -11%) 🔥')

    def test_180_days_savings_and_discount(self):
        text = format_dynamic_tariff_button(self.t180, self.t30)
        self.assertEqual(text, '⚡️ 180 дн. — 700 ₽ (117 ₽/мес • -22%) 🔥')

    def test_365_days_savings_and_discount(self):
        text = format_dynamic_tariff_button(self.t365, self.t30)
        self.assertEqual(text, '💎 365 дн. — 1400 ₽ (115 ₽/мес • -23%) 🔥')

    def test_tariff_shorter_than_base_does_not_show_negative_discount(self):
        text = format_dynamic_tariff_button(self.t7, self.t30)
        self.assertEqual(text, '⏱ 7 дн. — 90 ₽')

    def test_more_expensive_per_day_tariff_shows_no_discount(self):
        expensive = SimpleNamespace(id=10, duration_days=90, price_rub=500)
        text = format_dynamic_tariff_button(expensive, self.t30)
        self.assertEqual(text, '⏱ 90 дн. — 500 ₽')

    def test_none_base_tariff(self):
        text = format_dynamic_tariff_button(self.t90, None)
        self.assertEqual(text, '⏱ 90 дн. — 400 ₽')

    def test_zero_duration_tariff(self):
        zero_days = SimpleNamespace(id=11, duration_days=0, price_rub=100)
        text = format_dynamic_tariff_button(zero_days, self.t30)
        self.assertEqual(text, '⏱ 0 дн. — 100 ₽')

    def test_keyboard_selects_30_days_base_even_if_7_days_exists(self):
        tariffs = [self.t7, self.t30, self.t90, self.t365]
        kb = get_tariff_duration_keyboard(tariffs)
        texts_in_kb = [btn.text for row in kb.inline_keyboard for btn in row]
        
        self.assertIn('⏱ 7 дн. — 90 ₽', texts_in_kb)
        self.assertIn('⏱ 30 дн. — 150 ₽', texts_in_kb)
        self.assertIn('⏱ 90 дн. — 400 ₽ (133 ₽/мес • -11%) 🔥', texts_in_kb)
        self.assertIn('💎 365 дн. — 1400 ₽ (115 ₽/мес • -23%) 🔥', texts_in_kb)

    def test_renew_keyboard_uses_dynamic_formatting(self):
        tariffs = [self.t30, self.t90]
        kb = get_renew_keyboard(tariffs)
        texts_in_kb = [btn.text for row in kb.inline_keyboard for btn in row]
        self.assertIn('⏱ 30 дн. — 150 ₽', texts_in_kb)
        self.assertIn('⏱ 90 дн. — 400 ₽ (133 ₽/мес • -11%) 🔥', texts_in_kb)

    def test_precision_boundary_discount_calculation(self):
        t_base = SimpleNamespace(id=20, duration_days=30, price_rub=149)
        t_target = SimpleNamespace(id=21, duration_days=90, price_rub=399)
        text = format_dynamic_tariff_button(t_target, t_base)
        self.assertEqual(text, '⏱ 90 дн. — 399 ₽ (133 ₽/мес • -11%) 🔥')

    def test_decimal_prices_and_attribute_edge_cases(self):
        from decimal import Decimal
        t_base = SimpleNamespace(id=30, duration_days=30, price_rub=Decimal("150.00"))
        t_target = SimpleNamespace(id=31, duration_days=90, price_rub=Decimal("400.00"))
        text = format_dynamic_tariff_button(t_target, t_base)
        self.assertEqual(text, '⏱ 90 дн. — 400 ₽ (133 ₽/мес • -11%) 🔥')

    def test_duration_boundary_tiers_31_89_179_359_360(self):
        t30 = SimpleNamespace(id=2, duration_days=30, price_rub=150)
        t31 = SimpleNamespace(id=40, duration_days=31, price_rub=140)
        t89 = SimpleNamespace(id=41, duration_days=89, price_rub=350)
        t179 = SimpleNamespace(id=42, duration_days=179, price_rub=650)
        t359 = SimpleNamespace(id=43, duration_days=359, price_rub=1300)
        t360 = SimpleNamespace(id=44, duration_days=360, price_rub=1300)

        self.assertIn('-10%', format_dynamic_tariff_button(t31, t30))
        self.assertIn('-21%', format_dynamic_tariff_button(t89, t30))
        self.assertIn('⏱ 179 дн.', format_dynamic_tariff_button(t179, t30))
        self.assertIn('⚡️ 359 дн.', format_dynamic_tariff_button(t359, t30))
        self.assertIn('💎 360 дн.', format_dynamic_tariff_button(t360, t30))

    def test_empty_or_none_fields_handled_safely(self):
        empty_obj = SimpleNamespace()
        self.assertEqual(format_dynamic_tariff_button(empty_obj, None), '⏱ 0 дн. — 0 ₽')


if __name__ == '__main__':
    unittest.main()
