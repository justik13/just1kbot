from decimal import Decimal

from services.referral_bonus import calculate_referral_bonus


def test_referral_bonus_is_ten_percent_for_every_purchase_amount():
    assert calculate_referral_bonus(1) == Decimal("0")
    assert calculate_referral_bonus(10) == Decimal("1")
    assert calculate_referral_bonus(30) == Decimal("3")
    assert calculate_referral_bonus(99) == Decimal("9")
    assert calculate_referral_bonus(100) == Decimal("10")
    assert calculate_referral_bonus(999) == Decimal("99")
    assert calculate_referral_bonus(1000) == Decimal("100")


def test_referral_bonus_has_no_duration_or_first_purchase_gate():
    # The calculation depends only on the successfully spent amount.
    for purchase_amount in (10, 50, 123, 500, 999):
        assert calculate_referral_bonus(purchase_amount) == (
            Decimal(str(purchase_amount)) * Decimal("0.10")
        ).quantize(Decimal("1"), rounding="ROUND_DOWN")


def test_referral_bonus_rejects_non_positive_amounts():
    assert calculate_referral_bonus(0) == Decimal("0")
    assert calculate_referral_bonus(-100) == Decimal("0")
