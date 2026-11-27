"""Unit tests for pure billing calculation, payment split, and webhook validation."""

import uuid

import pytest

from bot.handlers.webhook import _validate_webhook_payload
from bot.miniapp.handlers_user import _calc_payment_split
from config.constants import MIN_TOPUP_RUB
from config.tariffs import calculate_standard_price
from integrations.payment_gateways.yookassa import YooKassaGateway


def test_standard_tariff_calculation():
    # 2 devices, 30 days: base is 99 (from STANDARD_PLANS[30])
    assert calculate_standard_price(2, 30) == 99

    # 3 devices, 30 days: base (99) + 1 * 30 = 129
    assert calculate_standard_price(3, 30) == 129

    # 5 devices, 30 days: base (99) + 3 * 30 = 189
    assert calculate_standard_price(5, 30) == 189

    # 2 devices, 90 days: base is 255 (from STANDARD_PLANS[90])
    assert calculate_standard_price(2, 90) == 255


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
    assert res["card_to_pay"] == int(MIN_TOPUP_RUB)
    assert res["real_used"] == 80


@pytest.mark.asyncio
async def test_yookassa_parse_webhook_currency_mismatch():
    gw = YooKassaGateway()
    res = await gw.parse_webhook({
        "event": "payment.succeeded",
        "object": {
            "id": "pay_usd",
            "status": "succeeded",
            "amount": {"value": "100.00", "currency": "USD"},
            "metadata": {"order_id": str(uuid.uuid4())},
        },
    })
    assert res.is_paid is False
    assert res.amount_rub is None
    assert getattr(res, "mismatch_reason", None) is not None


def test_validate_webhook_payload_contract():
    event, _obj, provider_id, payment_id = _validate_webhook_payload({
        "type": "notification",
        "event": "payment.succeeded",
        "object": {"id": "pay_123"},
    })
    assert event == "payment.succeeded"
    assert provider_id == "pay_123"
    assert payment_id == "pay_123"

    r_event, _r_obj, r_prov_id, r_pay_id = _validate_webhook_payload({
        "type": "notification",
        "event": "refund.succeeded",
        "object": {"id": "ref_1", "payment_id": "pay_123"},
    })
    assert r_event == "refund.succeeded"
    assert r_prov_id == "ref_1"
    assert r_pay_id == "pay_123"

    with pytest.raises(ValueError, match="unsupported_event"):
        _validate_webhook_payload({
            "type": "notification",
            "event": "payout.succeeded",
            "object": {"id": "po_1"},
        })

    with pytest.raises(ValueError, match="identity"):
        _validate_webhook_payload({
            "type": "notification",
            "event": "refund.succeeded",
            "object": {"id": "ref_only"},
        })
