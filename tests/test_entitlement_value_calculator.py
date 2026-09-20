"""Unit tests for pure Decimal calculations in EntitlementValueCalculator."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from services.entitlement_value_calculator import (
    calculate_remaining_hours,
    calculate_remaining_paid_value,
)


@dataclass
class DummyGrant:
    coverage_start: datetime
    coverage_end: datetime
    original_duration_hours: int
    paid_value_rub: Decimal
    status: str = "active"


def test_calculate_remaining_paid_value_future_active_expired():
    base_time = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Past/expired grant (30 days ago, ended 1 hour ago)
    g_expired = DummyGrant(
        coverage_start=base_time - timedelta(days=30),
        coverage_end=base_time - timedelta(hours=1),
        original_duration_hours=720,
        paid_value_rub=Decimal("300.000000"),
        status="active",
    )

    # 2. Currently active grant (started 10 days ago, ends in 20 days -> 2/3 remaining)
    # Total duration: 30 days = 720 hours
    # Remaining: 20 days = 480 hours
    # Value: 300 * (480 / 720) = 200.000000
    g_active = DummyGrant(
        coverage_start=base_time - timedelta(days=10),
        coverage_end=base_time + timedelta(days=20),
        original_duration_hours=720,
        paid_value_rub=Decimal("300.000000"),
        status="active",
    )

    # 3. Future queued grant (starts in 20 days, ends in 50 days)
    # Retains 100% of paid value
    g_future = DummyGrant(
        coverage_start=base_time + timedelta(days=20),
        coverage_end=base_time + timedelta(days=50),
        original_duration_hours=720,
        paid_value_rub=Decimal("300.000000"),
        status="active",
    )

    # 4. Inactive grant (status = revoked) -> ignored
    g_revoked = DummyGrant(
        coverage_start=base_time - timedelta(days=5),
        coverage_end=base_time + timedelta(days=25),
        original_duration_hours=720,
        paid_value_rub=Decimal("300.000000"),
        status="revoked",
    )

    # 5. Bonus grant (paid_value_rub = 0) -> contributes 0
    g_bonus = DummyGrant(
        coverage_start=base_time - timedelta(days=5),
        coverage_end=base_time + timedelta(days=25),
        original_duration_hours=720,
        paid_value_rub=Decimal("0.000000"),
        status="active",
    )

    grants = [g_expired, g_active, g_future, g_revoked, g_bonus]
    remaining_value = calculate_remaining_paid_value(grants, as_of=base_time)

    # Expected: 0 (expired) + 200.000000 (active) + 300.000000 (future) + 0 (revoked) + 0 (bonus) = 500.000000
    assert remaining_value == Decimal("500.000000")


def test_calculate_remaining_paid_value_edge_cases():
    base_time = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)

    # Zero or negative original hours
    g_zero_hours = DummyGrant(
        coverage_start=base_time - timedelta(hours=1),
        coverage_end=base_time + timedelta(hours=1),
        original_duration_hours=0,
        paid_value_rub=Decimal("100.000000"),
    )
    assert calculate_remaining_paid_value([g_zero_hours], as_of=base_time) == Decimal("0.000000")

    # Negative paid value
    g_neg_value = DummyGrant(
        coverage_start=base_time - timedelta(hours=1),
        coverage_end=base_time + timedelta(hours=1),
        original_duration_hours=2,
        paid_value_rub=Decimal("-10.000000"),
    )
    assert calculate_remaining_paid_value([g_neg_value], as_of=base_time) == Decimal("0.000000")


def test_calculate_remaining_hours_split():
    base_time = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)

    # Active paid grant: 10 hours remaining
    g_paid = DummyGrant(
        coverage_start=base_time - timedelta(hours=2),
        coverage_end=base_time + timedelta(hours=10),
        original_duration_hours=12,
        paid_value_rub=Decimal("50.000000"),
        status="active",
    )

    # Active bonus grant: 5 hours remaining
    g_bonus = DummyGrant(
        coverage_start=base_time + timedelta(hours=10),
        coverage_end=base_time + timedelta(hours=15),
        original_duration_hours=5,
        paid_value_rub=Decimal("0.000000"),
        status="active",
    )

    # Expired grant
    g_expired = DummyGrant(
        coverage_start=base_time - timedelta(hours=5),
        coverage_end=base_time - timedelta(hours=1),
        original_duration_hours=4,
        paid_value_rub=Decimal("50.000000"),
        status="active",
    )

    # Revoked grant
    g_revoked = DummyGrant(
        coverage_start=base_time,
        coverage_end=base_time + timedelta(hours=20),
        original_duration_hours=20,
        paid_value_rub=Decimal("100.000000"),
        status="revoked",
    )

    paid_h, bonus_h = calculate_remaining_hours([g_paid, g_bonus, g_expired, g_revoked], as_of=base_time)
    assert paid_h == 10
    assert bonus_h == 5
