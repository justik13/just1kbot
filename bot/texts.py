# bot/texts.py
#
# Единый реестр и загрузчик текстов приложения (SSOT Text Registry).
#
# Строгие правила Single Source of Truth:
# 1. Один ключ = Один канонический файл = Один источник истины.
# 2. Никаких неявных переопределений (overrides) или дубликатов.
#    Если ключ встречается в двух разных файлах, загрузчик немедленно выбрасывает RuntimeError.

import logging
from importlib import reload
from typing import Any

from bot.texts_data.admin import (
    broadcast as _admin_broadcast,
    dashboard as _admin_dashboard,
    disputes as _admin_disputes,
    payments as _admin_payments,
    queues as _admin_queues,
    servers as _admin_servers,
    subscriptions as _admin_subscriptions,
    tariffs as _admin_tariffs,
    users as _admin_users,
)
from bot.texts_data.common import (
    buttons as _common_buttons,
    errors as _common_errors,
    status as _common_status,
)
from bot.texts_data.runtime import (
    alerts as _runtime_alerts,
    workers as _runtime_workers,
)
from bot.texts_data.user import (
    connection as _user_connection,
    devices as _user_devices,
    hub as _user_hub,
    payments as _user_payments,
    referral as _user_referral,
    subscription as _user_subscription,
    support as _user_support,
)

logger = logging.getLogger(__name__)

_ALL_MODULES = [
    ("common.buttons", _common_buttons),
    ("common.errors", _common_errors),
    ("common.status", _common_status),
    ("user.hub", _user_hub),
    ("user.connection", _user_connection),
    ("user.devices", _user_devices),
    ("user.payments", _user_payments),
    ("user.subscription", _user_subscription),
    ("user.referral", _user_referral),
    ("user.support", _user_support),
    ("admin.broadcast", _admin_broadcast),
    ("admin.dashboard", _admin_dashboard),
    ("admin.disputes", _admin_disputes),
    ("admin.payments", _admin_payments),
    ("admin.queues", _admin_queues),
    ("admin.servers", _admin_servers),
    ("admin.subscriptions", _admin_subscriptions),
    ("admin.tariffs", _admin_tariffs),
    ("admin.users", _admin_users),
    ("runtime.alerts", _runtime_alerts),
    ("runtime.workers", _runtime_workers),
]


def _validate_key(key: Any) -> None:
    if not isinstance(key, str):
        raise RuntimeError(
            f"Text key must be string, got {type(key).__name__}: {key!r}"
        )

    if not key.isidentifier():
        raise RuntimeError(
            f"Text key must be a valid Python identifier: {key!r}"
        )


def _merge_texts() -> dict[str, Any]:
    merged: dict[str, Any] = {}
    key_sources: dict[str, str] = {}

    for source_name, module in _ALL_MODULES:
        source_texts = getattr(module, "TEXTS", {})
        for key, value in source_texts.items():
            _validate_key(key)

            if key in merged:
                first_source = key_sources[key]
                raise RuntimeError(
                    f"SSOT Violation: Duplicate text key {key!r} found in {source_name!r} "
                    f"(already defined in {first_source!r}). Each text key must have exactly ONE canonical location."
                )

            merged[key] = value
            key_sources[key] = source_name

    return merged


_TEXTS = _merge_texts()

globals().update(_TEXTS)

__all__ = list(_TEXTS.keys()) + [
    "get_text",
    "get_all_text_keys",
    "reload_texts",
]


def get_text(key: str, default: Any = None) -> Any:
    """Безопасно получить текст по ключу."""
    return _TEXTS.get(key, default)


def get_all_text_keys() -> list[str]:
    """Возвращает список всех загруженных текстовых ключей."""
    return list(_TEXTS.keys())


def reload_texts() -> None:
    """
    Перезагружает текстовые модули.
    Полезно при разработке, чтобы не перезапускать бота.
    """
    global _TEXTS

    for _, module in _ALL_MODULES:
        reload(module)

    _TEXTS = _merge_texts()
    globals().update(_TEXTS)

    logger.info(
        "Texts reloaded successfully: %s keys from %s domain modules.",
        len(_TEXTS),
        len(_ALL_MODULES),
    )
