"""Unit tests for billing calculation, payment split, and tariff logic."""

from bot.miniapp.handlers_user import _calc_payment_split
from config.constants import MIN_TOPUP_RUB
from config.tariffs import calculate_standard_price


def test_standard_tariff_calculation():
    # 2 devices, 30 days: base is 99 (from STANDARD_PLANS[30])
    price_2_30 = calculate_standard_price(2, 30)
    assert price_2_30 == 99

    # 3 devices, 30 days: base (99) + 1 * 30 = 129
    price_3_30 = calculate_standard_price(3, 30)
    assert price_3_30 == 129

    # 5 devices, 30 days: base (99) + 3 * 30 = 189
    price_5_30 = calculate_standard_price(5, 30)
    assert price_5_30 == 189

    # 2 devices, 90 days: base is 255 (from STANDARD_PLANS[90])
    price_2_90 = calculate_standard_price(2, 90)
    assert price_2_90 == 255


def test_payment_split_card_threshold():
    # When use_balance is False, full amount goes to card
    res = _calc_payment_split(100, real_avail=50, bonus_avail=0, use_balance=False)
    assert res == {"bonus_used": 0, "real_used": 0, "card_to_pay": 100}

    # When full balance available, 0 card to pay
    res = _calc_payment_split(100, real_avail=100, bonus_avail=0, use_balance=True)
    assert res == {"bonus_used": 0, "real_used": 100, "card_to_pay": 0}

    # Bonus is used first
    res = _calc_payment_split(100, real_avail=50, bonus_avail=70, use_balance=True)
    assert res == {"bonus_used": 70, "real_used": 30, "card_to_pay": 0}

    # Min card topup threshold enforcement:
    # If remaining card_to_pay would be < MIN_TOPUP_RUB (20), clamp card_to_pay to 20
    # and reduce wallet_used accordingly
    res = _calc_payment_split(100, real_avail=90, bonus_avail=0, use_balance=True)
    assert res["card_to_pay"] == int(MIN_TOPUP_RUB)  # 20
    assert res["real_used"] == 80  # 100 - 20
