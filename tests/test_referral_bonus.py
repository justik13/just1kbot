import unittest
from decimal import Decimal

from services.referral_bonus import calculate_referral_bonus


class TestReferralBonusCalculation(unittest.TestCase):
    def test_referral_bonus_default_rate_is_fifteen_percent(self):
        assert calculate_referral_bonus(1) == Decimal(0)
        assert calculate_referral_bonus(10) == Decimal(1)
        assert calculate_referral_bonus(30) == Decimal(4)
        assert calculate_referral_bonus(99) == Decimal(14)
        assert calculate_referral_bonus(100) == Decimal(15)
        assert calculate_referral_bonus(999) == Decimal(149)
        assert calculate_referral_bonus(1000) == Decimal(150)

    def test_referral_bonus_supports_explicit_tier_rates(self):
        for rate, expected_100, expected_1000 in (
            (Decimal("0.15"), Decimal(15), Decimal(150)),
            (Decimal("0.20"), Decimal(20), Decimal(200)),
            (Decimal("0.25"), Decimal(25), Decimal(250)),
            (Decimal("0.30"), Decimal(30), Decimal(300)),
        ):
            assert calculate_referral_bonus(100, rate=rate) == expected_100
            assert calculate_referral_bonus(1000, rate=rate) == expected_1000

    def test_referral_bonus_rejects_non_positive_amounts(self):
        assert calculate_referral_bonus(0) == Decimal(0)
        assert calculate_referral_bonus(-100) == Decimal(0)


class TestReferralTiers(unittest.TestCase):
    def test_referral_tier_progression(self):
        from services.referral_bonus import get_referral_tier

        # Tier 1: 0..2 -> 15% (Standard)
        for cnt in (0, 1, 2):
            t = get_referral_tier(cnt)
            assert t.rate == Decimal("0.15")
            assert t.name == "Standard"
            assert t.needed_for_next == (3 - cnt)
            assert t.next_tier_name == "Silver"
            assert t.next_rate == Decimal("0.20")

        # Tier 2: 3..6 -> 20% (Silver)
        for cnt in (3, 4, 5, 6):
            t = get_referral_tier(cnt)
            assert t.rate == Decimal("0.20")
            assert t.name == "Silver"
            assert t.needed_for_next == (7 - cnt)
            assert t.next_tier_name == "Gold"
            assert t.next_rate == Decimal("0.25")

        # Tier 3: 7..14 -> 25% (Gold)
        for cnt in (7, 8, 9, 10, 11, 12, 13, 14):
            t = get_referral_tier(cnt)
            assert t.rate == Decimal("0.25")
            assert t.name == "Gold"
            assert t.needed_for_next == (15 - cnt)
            assert t.next_tier_name == "Platinum"
            assert t.next_rate == Decimal("0.30")

        # Tier 4: 15+ -> 30% (Platinum)
        for cnt in (15, 20, 50, 100):
            t = get_referral_tier(cnt)
            assert t.rate == Decimal("0.30")
            assert t.name == "Platinum"
            assert t.needed_for_next is None
            assert t.next_tier_name is None
            assert t.next_rate is None


class TestMaskTelegramId(unittest.TestCase):
    def test_masking_standard_ids(self):
        from bot.texts.user.referral import mask_telegram_id

        assert mask_telegram_id(8141287721) == "814***21"
        assert mask_telegram_id("8141287721") == "814***21"
        assert mask_telegram_id(123456789) == "123***89"
        assert mask_telegram_id(12345) == "123***45"

    def test_masking_short_ids(self):
        from bot.texts.user.referral import mask_telegram_id

        assert mask_telegram_id(1234) == "1***4"
        assert mask_telegram_id(12) == "1***2"
        assert mask_telegram_id(1) == "***"


class TestTariffChangeMath(unittest.TestCase):
    """Pure mathematical validation for OrderService.calculate_tariff_change."""

    def test_calculate_tariff_change_math(self):
        from datetime import datetime, timedelta, timezone
        from database.models import Tariff
        from services.order_service import OrderService

        now = datetime.now(timezone.utc)
        current_tariff = Tariff(
            id=1,
            name="100 RUB / 10 days",
            price_rub=Decimal("100.00"),
            duration_days=10,
        )
        target_tariff = Tariff(
            id=2,
            name="300 RUB / 30 days",
            price_rub=Decimal("300.00"),
            duration_days=30,
        )
        # 5 days remaining on current tariff -> 50 RUB remaining value
        subscription_end = now + timedelta(days=5)

        due_rub, resulting_days = OrderService.calculate_tariff_change(
            current_tariff, target_tariff, subscription_end, now=now
        )
        # 300 - 50 = 250 RUB
        self.assertEqual(due_rub, Decimal("250.00"))
        self.assertEqual(resulting_days, 30)

    def test_calculate_tariff_change_multi_month_proportional(self):
        from datetime import datetime, timedelta, timezone
        from database.models import Tariff
        from services.order_service import OrderService

        now = datetime.now(timezone.utc)
        base_tariff = Tariff(
            id=1,
            name="100 RUB / 30 days",
            price_rub=Decimal("100.00"),
            duration_days=30,
        )
        pro_tariff = Tariff(
            id=2,
            name="200 RUB / 30 days",
            price_rub=Decimal("200.00"),
            duration_days=30,
        )

        # Case 1: 200 days of Base -> switch to Pro
        # Value = 200 * (100 / 30) = 667 RUB >= target cost (200 RUB)
        # due_rub = 0, resulting_days = round(667 / (200 / 30)) = 100 days
        sub_end_200 = now + timedelta(days=200)
        due_rub, resulting_days = OrderService.calculate_tariff_change(
            base_tariff, pro_tariff, sub_end_200, now=now
        )
        self.assertEqual(due_rub, Decimal("0.00"))
        self.assertEqual(resulting_days, 100)

        # Case 2: 200 days of Base -> switch to another Base variant with identical daily rate
        same_rate_tariff = Tariff(
            id=3,
            name="100 RUB / 30 days variant",
            price_rub=Decimal("100.00"),
            duration_days=30,
        )
        due_rub_same, resulting_days_same = OrderService.calculate_tariff_change(
            base_tariff, same_rate_tariff, sub_end_200, now=now
        )
        self.assertEqual(due_rub_same, Decimal("0.00"))
        self.assertEqual(resulting_days_same, 200)

        # Case 3: Expired subscription (0 days left) -> full price, standard duration
        sub_end_expired = now - timedelta(days=1)
        due_expired, days_expired = OrderService.calculate_tariff_change(
            base_tariff, pro_tariff, sub_end_expired, now=now
        )
        self.assertEqual(due_expired, Decimal("200.00"))
        self.assertEqual(days_expired, 30)

    def test_calculate_tariff_change_preserves_partial_days_with_ceil(self):
        from datetime import datetime, timedelta, timezone
        from database.models import Tariff
        from services.order_service import OrderService

        now = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)
        sub_end = now + timedelta(hours=36)  # 1.5 days left
        current_tariff = Tariff(id=1, name="Basic", price_rub=Decimal("300.00"), duration_days=30)
        target_tariff = Tariff(id=2, name="Pro", price_rub=Decimal("600.00"), duration_days=30)

        cost, days = OrderService.calculate_tariff_change(
            current_tariff=current_tariff,
            target_tariff=target_tariff,
            subscription_end=sub_end,
            now=now,
        )
        self.assertEqual(cost, Decimal("580.00"))
        self.assertEqual(days, 30)


if __name__ == "__main__":
    unittest.main()
