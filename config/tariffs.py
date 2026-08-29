"""Single Source of Truth for system default tariff definitions and seed data.

This module sits at Level 0 in the application layered architecture and has zero
internal dependencies, allowing safe downward imports by database, services, and bot layers.
"""

DEFAULT_TARIFFS_SEEDS = [
    {
        "name": "Базовый",
        "description": "Телефон и ноутбук",
        "duration_days": 7,
        "device_limit": 2,
        "price_rub": 35,
        "sort_order": 10,
    },
    {
        "name": "Базовый",
        "description": "Телефон и ноутбук",
        "duration_days": 30,
        "device_limit": 2,
        "price_rub": 90,
        "sort_order": 11,
    },
    {
        "name": "Базовый",
        "description": "Телефон и ноутбук",
        "duration_days": 90,
        "device_limit": 2,
        "price_rub": 240,
        "sort_order": 12,
    },
    {
        "name": "Семейный",
        "description": "Подключите всю семью",
        "duration_days": 30,
        "device_limit": 5,
        "price_rub": 180,
        "sort_order": 20,
    },
    {
        "name": "Семейный",
        "description": "Подключите всю семью",
        "duration_days": 90,
        "device_limit": 5,
        "price_rub": 480,
        "sort_order": 21,
    },
    {
        "name": "Pro",
        "description": "Для офиса или большого парка гаджетов",
        "duration_days": 30,
        "device_limit": 10,
        "price_rub": 320,
        "sort_order": 30,
    },
    {
        "name": "Pro",
        "description": "Для офиса или большого парка гаджетов",
        "duration_days": 90,
        "device_limit": 10,
        "price_rub": 850,
        "sort_order": 31,
    },
]

__all__ = [
    "DEFAULT_TARIFFS_SEEDS",
]
