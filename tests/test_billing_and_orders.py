"""Unit tests for billing calculation, payment split, and tariff logic."""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from bot.miniapp.handlers_user import _calc_payment_split
from config.constants import MIN_TOPUP_RUB
from config.tariffs import calculate_standard_price
from integrations.payment_gateways.yookassa import YooKassaGateway, YooKassaWebhookResult
from services.fulfillment_service import FulfillmentService
from services.order_service import OrderService
from utils.datetime_helpers import now_utc


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


@pytest.mark.asyncio
async def test_yookassa_check_payment_status_partial_refund():
    gw = YooKassaGateway()
    fake_result = SimpleNamespace(
        ok=True,
        value={
            "status": "pending",
            "amount": {"value": "100.00", "currency": "RUB"},
            "refunded_amount": {"value": "50.00", "currency": "RUB"},
        },
    )
    with patch.object(gw, "_get_payment_result", AsyncMock(return_value=fake_result)):
        res = await gw.check_payment_status("pay_1")
    assert res.is_refunded is False
    assert res.is_partial_refund is True
    assert res.is_paid is False
    assert res.mismatch_reason is not None


@pytest.mark.asyncio
async def test_process_webhook_event_currency_mismatch_and_cancel_id_mismatch():
    order_id = uuid.uuid4()
    order = SimpleNamespace(
        id=order_id,
        user_id=1,
        external_id="pay_expected",
        status="pending",
        amount_rub=Decimal("100.00"),
        metadata_={},
    )
    session = AsyncMock()
    session.execute = AsyncMock(return_value=SimpleNamespace(one_or_none=lambda: (order_id, 1)))
    session.scalar = AsyncMock(side_effect=[1, order])
    session.get_bind = MagicMock(return_value=None)

    mismatch_res = YooKassaWebhookResult(
        order_id=str(order_id),
        is_paid=False,
        is_refunded=False,
        external_id="pay_expected",
        amount_rub=None,
        event_type="payment.succeeded",
        mismatch_reason="non_rub_currency:USD",
    )
    gw = MagicMock()
    gw.parse_webhook = AsyncMock(return_value=mismatch_res)
    with patch("services.order_service.get_payment_gateway", return_value=gw):
        out = await OrderService.process_webhook_event(session, {"event": "payment.succeeded"})
    assert out is order
    assert order.status == "pending"
    assert order.metadata_["requires_manual_review"] is True
    assert "payment_amount_mismatch" in order.metadata_

    # Cancel external_id mismatch
    order.metadata_ = {}
    session.scalar = AsyncMock(side_effect=[1, order])
    cancel_mismatch = YooKassaWebhookResult(
        order_id=str(order_id),
        is_paid=False,
        is_refunded=False,
        is_canceled=True,
        external_id="pay_wrong",
        event_type="payment.canceled",
    )
    gw.parse_webhook = AsyncMock(return_value=cancel_mismatch)
    with patch("services.order_service.get_payment_gateway", return_value=gw):
        out2 = await OrderService.process_webhook_event(session, {"event": "payment.canceled"})
    assert out2 is order
    assert order.status == "pending"
    assert order.metadata_["cancel_payment_id_mismatch"] == "pay_wrong"


@pytest.mark.asyncio
async def test_process_refund_webhook_ignores_canceled_order():
    order = SimpleNamespace(id=uuid.uuid4(), user_id=1, status="canceled", metadata_={})
    session = AsyncMock()
    out = await OrderService.process_refund_webhook(
        session, order, refund_amount=Decimal("100"), refund_external_id="ref_1", already_locked=True
    )
    assert out is order


@pytest.mark.asyncio
async def test_quote_standard_change_grace_autorenew():
    now = now_utc()
    vless_sub = SimpleNamespace(
        expires_at=now - timedelta(hours=2),
        device_limit=3,
        downgrade_cooldown_until=None,
    )
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)
    with (
        patch(
            "database.repositories.vless_subscription_repo.get_subscription_by_user_id",
            AsyncMock(return_value=vless_sub),
        ),
        patch(
            "services.order_service.count_active_user_devices",
            AsyncMock(return_value=1),
        ),
    ):
        quote = await OrderService.quote_standard_change(
            session, user_id=1, target_device_limit=3, target_duration_days=30, operation="renew"
        )
    assert quote["operation"] == "renew"
    assert quote["discount_rub"] == 0
    assert quote["due_rub"] == 129


@pytest.mark.asyncio
async def test_create_yookassa_order_enforces_pending_cap():
    ambiguous_orders = [
        SimpleNamespace(
            id=uuid.uuid4(),
            user_id=1,
            status="pending",
            metadata_={"payment_creation_ambiguous": True},
        )
        for _ in range(3)
    ]
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])),
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: ambiguous_orders)),
        ]
    )
    with pytest.raises(ValueError, match="3"):
        await OrderService._create_yookassa_order(
            session,
            user_id=1,
            service_type="topup",
            amount_rub=Decimal("100"),
            duration_days=0,
            description="topup",
        )


@pytest.mark.asyncio
async def test_downgrade_cap_excludes_bonus_funded_wallet_orders():
    now = now_utc()
    orders = [
        SimpleNamespace(
            payment_method="wallet",
            amount_rub=Decimal("200"),
            metadata_={"charged_bonus": 150},
            paid_at=now,
        ),
        SimpleNamespace(
            payment_method="yookassa",
            amount_rub=Decimal("100"),
            metadata_={"total_cost_rub": 150, "wallet_bonus_debit_rub": 50},
            paid_at=now,
        ),
    ]
    session = AsyncMock()
    session.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: orders))
    session.scalar = AsyncMock(return_value=0)
    cap_left, _ratio = await OrderService._get_standard_downgrade_cap(session, user_id=1)
    # 200-150=50 from order 1, 150-50=100 from order 2 => 150 total
    assert cap_left == 150


@pytest.mark.asyncio
async def test_fulfill_expired_standard_upgrade_compensates_and_revoke_restores_limits():
    now = now_utc()
    user = SimpleNamespace(id=1, telegram_id=111, notification_retry_count=0, last_notification_attempt=None)
    expired_sub = SimpleNamespace(expires_at=now - timedelta(hours=1), device_limit=2)
    upgrade_order = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=1,
        service_type="standard",
        payment_method="yookassa",
        amount_rub=Decimal("50"),
        duration_days=0,
        device_limit=5,
        metadata_={"operation": "upgrade"},
    )
    session = AsyncMock()
    session.get = AsyncMock(return_value=user)
    with (
        patch(
            "database.repositories.vless_subscription_repo.get_subscription_by_user_id",
            AsyncMock(return_value=expired_sub),
        ),
        patch(
            "database.repositories.vless_subscription_repo.get_or_create_subscription",
            AsyncMock(return_value=expired_sub),
        ),
        patch.object(
            FulfillmentService,
            "_compensate_failed_fulfillment",
            AsyncMock(),
        ) as comp_mock,
    ):
        await FulfillmentService.fulfill_order(session, upgrade_order)
    comp_mock.assert_awaited_once()
    assert expired_sub.device_limit == 2

    # Revoke Standard renew with remaining days restores previous_device_limit
    active_sub = SimpleNamespace(
        expires_at=now + timedelta(days=40),
        device_limit=5,
        is_active=True,
    )
    renew_order = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=1,
        service_type="standard",
        duration_days=30,
        metadata_={"operation": "renew", "previous_device_limit": 2},
    )
    with (
        patch(
            "database.repositories.vless_subscription_repo.get_subscription_by_user_id",
            AsyncMock(return_value=active_sub),
        ),
        patch(
            "services.fulfillment_service.SubscriptionService._sync_access_state",
            AsyncMock(),
        ),
    ):
        await FulfillmentService.revoke_order(session, renew_order)
    assert active_sub.device_limit == 2
