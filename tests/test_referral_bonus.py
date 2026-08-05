from decimal import Decimal

from services.referral_bonus import calculate_referral_bonus


def test_referral_bonus_is_ten_percent_rounded_down():
    assert calculate_referral_bonus(100) == Decimal("10")
    assert calculate_referral_bonus(99) == Decimal("9")


def test_referral_bonus_rejects_non_positive_amounts():
    assert calculate_referral_bonus(0) == Decimal("0")
    assert calculate_referral_bonus(-100) == Decimal("0")
