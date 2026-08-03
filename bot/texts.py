# bot/texts.py
#
# Единый загрузчик текстов.
#
# Все тексты хранятся в:
# - bot/texts_data/user_texts.py
# - bot/texts_data/admin_texts.py
# - bot/texts_data/ui_texts.py
# - bot/texts_data/runtime_texts.py
# - bot/texts_data/overrides.py
#
# Этот файл:
# 1. Загружает user_texts и admin_texts.
# 2. Проверяет, что нет дублирующихся ключей.
# 3. Применяет точечные overrides.
# 4. Проверяет, что все ключи являются валидными Python identifier.
# 5. Публикует ключи как атрибуты модуля.

import logging
from importlib import reload
from typing import Any

from bot.texts_data import admin_texts as _admin_texts_module
from bot.texts_data import user_texts as _user_texts_module
from bot.texts_data import ui_texts as _ui_texts_module
from bot.texts_data import runtime_texts as _runtime_texts_module
from bot.texts_data import overrides as _overrides_module

logger = logging.getLogger(__name__)


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
    sources = (
        ("user_texts", dict(_user_texts_module.TEXTS)),
        ("admin_texts", dict(_admin_texts_module.TEXTS)),
        ("ui_texts", dict(_ui_texts_module.TEXTS)),
        ("runtime_texts", dict(_runtime_texts_module.TEXTS)),
    )
    merged: dict[str, Any] = {}

    for source_name, source in sources:
        for key, value in source.items():
            _validate_key(key)

            if key in merged:
                raise RuntimeError(
                    f"Duplicate text key {key!r} in {source_name}"
                )

            merged[key] = value

    # Точечные переопределения применяются последними.
    for key, value in dict(_overrides_module.OVERRIDES).items():
        _validate_key(key)
        merged[key] = value

    return merged


_TEXTS = _merge_texts()

# Публикуем все тексты как атрибуты модуля.
globals().update(_TEXTS)

__all__ = list(_TEXTS.keys()) + [
    "get_text",
    "get_all_text_keys",
    "reload_texts",
]


def get_text(key: str, default: Any = None) -> Any:
    """
    Безопасно получить текст по ключу.
    """
    return _TEXTS.get(key, default)


def get_all_text_keys() -> list[str]:
    """
    Возвращает список всех загруженных текстовых ключей.
    """
    return list(_TEXTS.keys())


def reload_texts() -> None:
    """
    Перезагружает текстовые модули.
    Полезно при разработке, чтобы не перезапускать бота.
    """
    global _TEXTS

    reload(_user_texts_module)
    reload(_admin_texts_module)
    reload(_ui_texts_module)
    reload(_runtime_texts_module)
    reload(_overrides_module)

    _TEXTS = _merge_texts()
    globals().update(_TEXTS)

    logger.info(
        "Texts reloaded successfully: %s keys",
        len(_TEXTS),
    )