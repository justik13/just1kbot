"""Single Source of Truth for system default tariff definitions and seed data.

This module sits at Level 0 in the application layered architecture and has zero
internal dependencies, allowing safe downward imports by database, services, and bot layers.
"""


DEFAULT_TARIFFS_SEEDS = [
    {
        "name": "Стандартный",
        "description": "2 устройства, безлимит",
        "duration_days": 30,
        "device_limit": 2,
        "price_rub": 99,
        "sort_order": 10,
        "service_type": "standard",
    },
    {
        "name": "Белый Интернет",
        "description": "1 устройство, 50 ГБ",
        "duration_days": 30,
        "device_limit": 1,
        "price_rub": 250,
        "sort_order": 20,
        "service_type": "white_internet",
    },
]

__all__ = [
    "DEFAULT_TARIFFS_SEEDS",
]
