"""Single Source of Truth for Standard plan dynamic pricing."""

from config.constants import (
    STANDARD_MAX_DEVICES,
    STANDARD_MIN_DEVICES,
    STANDARD_PLANS,
)


def calculate_standard_price(device_limit: int, duration_days: int) -> int:
    """Calculate Standard plan price in RUB for given device_limit (2..10) and duration_days (7/30/90)."""
    if duration_days not in STANDARD_PLANS:
        raise ValueError(f"Unsupported duration_days: {duration_days}. Allowed: {sorted(STANDARD_PLANS)}")
    if device_limit < STANDARD_MIN_DEVICES or device_limit > STANDARD_MAX_DEVICES:
        raise ValueError(
            f"Unsupported device_limit: {device_limit}. Allowed: {STANDARD_MIN_DEVICES}..{STANDARD_MAX_DEVICES}"
        )
    base_price, step_price = STANDARD_PLANS[duration_days]
    return base_price + (device_limit - STANDARD_MIN_DEVICES) * step_price


__all__ = [
    "calculate_standard_price",
]
