"""Pure Decimal calculations for EntitlementGrant residual economic value and hours.

This module knows nothing about persistence, Telegram, or payment providers.
"""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Any, Protocol


class EntitlementGrantLike(Protocol):
    coverage_start: datetime
    coverage_end: datetime
    original_duration_hours: int
    paid_value_rub: Decimal
    status: str


def calculate_remaining_paid_value(
    grants: Sequence[EntitlementGrantLike | Any],
    as_of: datetime,
) -> Decimal:
    """Calculate the total unexpired paid value in RUB for active grants at timestamp `as_of`.

    - Grants with status != 'active' are ignored.
    - Grants with paid_value_rub <= 0 (bonuses, compensations, gifts) contribute 0.
    - Future queued grants (as_of <= coverage_start) retain 100% of their paid_value_rub.
    - Expired grants (as_of >= coverage_end) contribute 0.
    - Currently active grants (coverage_start < as_of < coverage_end) are prorated against original_duration_hours.
    """
    total_value = Decimal("0.000000")

    with localcontext() as ctx:
        ctx.prec = 50
        for grant in grants:
            if getattr(grant, "status", None) != "active":
                continue

            paid_value = Decimal(getattr(grant, "paid_value_rub", Decimal(0)))
            if paid_value <= Decimal(0):
                continue

            start = grant.coverage_start
            end = grant.coverage_end

            if as_of >= end:
                continue

            if as_of <= start:
                total_value += paid_value
                continue

            # Active grant: coverage_start < as_of < coverage_end
            remaining_seconds = Decimal((end - as_of).total_seconds())
            orig_hours = getattr(grant, "original_duration_hours", 0)
            if orig_hours <= 0:
                continue

            orig_seconds = Decimal(orig_hours) * Decimal(3600)
            if orig_seconds <= 0:
                continue

            prorated = paid_value * (remaining_seconds / orig_seconds)
            prorated = min(paid_value, max(Decimal(0), prorated))
            total_value += prorated

    return total_value.quantize(Decimal("0.000001"))


def calculate_remaining_hours(
    grants: Sequence[EntitlementGrantLike | Any],
    as_of: datetime,
) -> tuple[int, int]:
    """Calculate (remaining_paid_hours, remaining_bonus_hours) from active grants."""
    paid_hours = 0
    bonus_hours = 0

    for grant in grants:
        if getattr(grant, "status", None) != "active":
            continue

        end = grant.coverage_end
        if as_of >= end:
            continue

        start = grant.coverage_start
        effective_start = max(start, as_of)
        duration_seconds = max(0.0, (end - effective_start).total_seconds())
        whole_hours = int(duration_seconds // 3600)

        paid_value = Decimal(getattr(grant, "paid_value_rub", Decimal(0)))
        if paid_value > Decimal(0):
            paid_hours += whole_hours
        else:
            bonus_hours += whole_hours

    return paid_hours, bonus_hours
