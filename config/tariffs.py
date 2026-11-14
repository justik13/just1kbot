"""Single Source of Truth for system default tariff definitions and pricing."""

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


DEFAULT_TARIFFS_SEEDS = [
    {
        "name": "Базовый",
        "description": "Телефон и ноутбук",
        "duration_days": 7,
        "device_limit": 2,
        "price_rub": calculate_standard_price(2, 7),
        "sort_order": 10,
    },
    {
        "name": "Базовый",
        "description": "Телефон и ноутбук",
        "duration_days": 30,
        "device_limit": 2,
        "price_rub": calculate_standard_price(2, 30),
        "sort_order": 11,
    },
    {
        "name": "Базовый",
        "description": "Телефон и ноутбук",
        "duration_days": 90,
        "device_limit": 2,
        "price_rub": calculate_standard_price(2, 90),
        "sort_order": 12,
    },
    {
        "name": "Семейный",
        "description": "Для семьи и нескольких устройств",
        "duration_days": 30,
        "device_limit": 5,
        "price_rub": calculate_standard_price(5, 30),
        "sort_order": 20,
    },
    {
        "name": "Семейный",
        "description": "Для семьи и нескольких устройств",
        "duration_days": 90,
        "device_limit": 5,
        "price_rub": calculate_standard_price(5, 90),
        "sort_order": 21,
    },
    {
        "name": "Pro",
        "description": "Для большого парка гаджетов",
        "duration_days": 30,
        "device_limit": 10,
        "price_rub": calculate_standard_price(10, 30),
        "sort_order": 30,
    },
    {
        "name": "Pro",
        "description": "Для большого парка гаджетов",
        "duration_days": 90,
        "device_limit": 10,
        "price_rub": calculate_standard_price(10, 90),
        "sort_order": 31,
    },
]

__all__ = [
    "DEFAULT_TARIFFS_SEEDS",
    "calculate_standard_price",
]
