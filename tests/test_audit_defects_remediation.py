"""Tests for full audit defects remediation."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from bot.handlers.webhook import _get_real_ip
from bot.middlewares.action_lock import LOCKED_ACTION_PREFIXES, STALE_ACTION_PREFIXES
from database.models import (
    PAYMENT_FULFILLMENT_STATUSES,
    PAYMENT_PROVIDER_STATUSES,
    AccountBalanceReservation,
    Server,
)
from database.repositories.servers_repo import update_server_health_snapshot
from services.provider_refunds import _consume_matching_reservation
from services.referral_bonus import reverse_referral_bonus_for_topup
from services.workers.node_monitor import AUTO_DISABLED_CHECK_INTERVAL
from services.workers.payments import _needs_recovery
from services.workers.webhook_inbox import auto_resolve_untracked_canceled_webhooks


def test_payment_status_constants_and_quote_type():
    assert "waiting_for_capture" in PAYMENT_PROVIDER_STATUSES
    assert "pending" not in PAYMENT_FULFILLMENT_STATUSES
    assert "reversal_pending" not in PAYMENT_FULFILLMENT_STATUSES
    assert PAYMENT_FULFILLMENT_STATUSES == (
        "not_ready",
        "processing",
        "succeeded",
        "failed",
        "reversed",
        "manual_review",
    )


def test_action_lock_prefixes_updated():
    assert "admin_payment_refund_confirm:" in LOCKED_ACTION_PREFIXES
    assert "admin_payment_refund_confirm:" in STALE_ACTION_PREFIXES
    assert "confirm_admin_balance_apply" in LOCKED_ACTION_PREFIXES
    assert "confirm_admin_balance_apply" in STALE_ACTION_PREFIXES
    assert "confirm_mass_bonus_apply" in LOCKED_ACTION_PREFIXES
    assert "confirm_mass_bonus_apply" in STALE_ACTION_PREFIXES
    assert "admin_dispute_apply:" in LOCKED_ACTION_PREFIXES
    assert "admin_dispute_apply:" in STALE_ACTION_PREFIXES
    assert "balance_resume_purchase:" in LOCKED_ACTION_PREFIXES
    assert "balance_resume_purchase:" in STALE_ACTION_PREFIXES
    assert "aq:x:" in LOCKED_ACTION_PREFIXES
    assert "aq:x:" in STALE_ACTION_PREFIXES


def test_auto_disabled_check_interval_is_one_hour():
    assert AUTO_DISABLED_CHECK_INTERVAL == 3600.0


def test_get_real_ip_forwarded_for():
    # Loopback remote -> reads X-Forwarded-For first element
    request_mock = MagicMock(spec=web.Request)
    request_mock.remote = "127.0.0.1"
    request_mock.headers = {
        "X-Forwarded-For": "185.71.76.10, 10.0.0.2",
    }
    assert _get_real_ip(request_mock) == "185.71.76.10"

    # Private IP remote with X-Real-IP
    request_mock_real = MagicMock(spec=web.Request)
    request_mock_real.remote = "10.0.0.5"
    request_mock_real.headers = {
        "X-Real-IP": "185.71.76.15",
        "X-Forwarded-For": "185.71.76.10, 10.0.0.2",
    }
    assert _get_real_ip(request_mock_real) == "185.71.76.15"

    # Public remote -> ignores forwarded headers
    request_public = MagicMock(spec=web.Request)
    request_public.remote = "8.8.8.8"
    request_public.headers = {
        "X-Real-IP": "1.1.1.1",
        "X-Forwarded-For": "2.2.2.2",
    }
    assert _get_real_ip(request_public) == "8.8.8.8"


def test_needs_recovery_expression_structure():
    # Verify expression evaluates cleanly
    expr = _needs_recovery()
    assert expr is not None


@pytest.mark.asyncio
async def test_untracked_refund_not_auto_resolved():
    session = AsyncMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []
    session.scalars.return_value = mock_scalars
    # Should only query payment.canceled, not refund.succeeded
    resolved = await auto_resolve_untracked_canceled_webhooks(session)
    assert resolved == 0


@pytest.mark.asyncio
async def test_welcome_bonus_reversal_without_referrer():
    session = AsyncMock()

    payment = MagicMock()
    payment.id = 55
    payment.user_id = 10

    purchaser = MagicMock()
    purchaser.id = 10
    purchaser.referred_by = None  # No referrer!

    welcome_credit = MagicMock()
    welcome_credit.id = 100
    welcome_credit.user_id = 10
    welcome_credit.amount = Decimal("50")
    welcome_credit.metadata_ = {
        "topup_payment_id": 55,
        "reason": "first_topup_welcome",
    }

    added_entries = []

    def fake_add(item):
        added_entries.append(item)

    session.get = AsyncMock(return_value=payment)
    session.scalar = AsyncMock(side_effect=[purchaser, None])  # purchaser, existing rev = None
    session.scalars = AsyncMock(
        return_value=MagicMock(all=MagicMock(return_value=[welcome_credit]))
    )
    session.add = fake_add
    session.flush = AsyncMock()

    from unittest.mock import patch

    with patch(
        "services.referral_bonus._credit_capacity",
        AsyncMock(return_value=Decimal("50")),
    ):
        total_reversed = await reverse_referral_bonus_for_topup(session, payment_id=55)

    assert total_reversed == Decimal("50")
    assert len(added_entries) >= 1
    assert added_entries[0].amount == Decimal("-50")


@pytest.mark.asyncio
async def test_partial_refund_reservation_split():
    session = AsyncMock()

    existing_reservation = AccountBalanceReservation(
        id=77,
        user_id=10,
        payment_id=42,
        reservation_type="refund",
        amount=Decimal("500"),
        currency="RUB",
        status="active",
    )

    added_items = []

    def fake_add(item):
        added_items.append(item)

    session.scalar = AsyncMock(return_value=existing_reservation)
    session.add = fake_add
    session.flush = AsyncMock()

    result = await _consume_matching_reservation(
        session,
        payment_id=42,
        amount=Decimal("200"),
        reservation_id=77,
    )

    assert result is existing_reservation
    assert existing_reservation.status == "consumed"
    assert len(added_items) == 1
    split_res = added_items[0]
    assert isinstance(split_res, AccountBalanceReservation)
    assert split_res.amount == Decimal("300")
    assert split_res.status == "active"
    assert split_res.payment_id == 42
    assert split_res.metadata_["split_from_reservation_id"] == 77
    assert split_res.metadata_["consumed_amount"] == "200"
    assert split_res.metadata_["remaining_amount"] == "300"


@pytest.mark.asyncio
async def test_update_server_health_snapshot_auto_disabled_allowed():
    session = AsyncMock()

    server = Server(
        id=3,
        name="Auto Server",
        is_active=False,
        health_state="AUTO_DISABLED",
        disabled_reason="AUTO_UNAVAILABLE",
        consecutive_fails=5,
        consecutive_successes=0,
        recovery_notice_sent=False,
    )

    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=server))
    )
    session.flush = AsyncMock()
    session.refresh = AsyncMock()

    res_server, applied = await update_server_health_snapshot(
        session,
        server_id=3,
        expected_health_state="AUTO_DISABLED",
        expected_consecutive_fails=5,
        expected_consecutive_successes=0,
        new_health_state="AUTO_DISABLED",
        consecutive_successes=1,
        recovery_notice_sent=True,
    )

    assert applied is True
    assert res_server.consecutive_successes == 1
    assert res_server.recovery_notice_sent is True
    assert res_server.is_active is False
