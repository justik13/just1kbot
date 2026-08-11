# bot/texts.py
#
# Единый загрузчик текстов.
#
# Все тексты хранятся в:
# - bot/texts_data/user_texts.py
# - bot/texts_data/referral_texts.py
# - bot/texts_data/admin_texts.py
# - bot/texts_data/ui_texts.py
# - bot/texts_data/runtime_texts.py
# - bot/texts_data/overrides.py

import logging
from importlib import reload
from typing import Any

from bot.texts_data import admin_texts as _admin_texts_module
from bot.texts_data import referral_texts as _referral_texts_module
from bot.texts_data import user_texts as _user_texts_module
from bot.texts_data import ui_texts as _ui_texts_module
from bot.texts_data import runtime_texts as _runtime_texts_module
from bot.texts_data import overrides as _overrides_module

logger = logging.getLogger(__name__)

# Legacy user-facing wording can survive in overrides even after the source
# text is corrected. Keep these replacements narrowly scoped to the affected
# message so technical identifiers such as AmneziaVPN, .vpn and vpn:// remain
# untouched.
_LEGACY_USER_TEXT_REPLACEMENTS: dict[str, tuple[tuple[str, str], ...]] = {
    "FAQ_TEXT": (
        (
            "Что делать, если VPN не подключается или медленно работает?",
            "Что делать, если подключение не работает или работает медленно?",
        ),
        (
            "Что делать, если ВПН не подключается или медленно работает?",
            "Что делать, если подключение не работает или работает медленно?",
        ),
    ),
}


def _validate_key(key: Any) -> None:
    if not isinstance(key, str):
        raise RuntimeError(
            f"Text key must be string, got {type(key).__name__}: {key!r}"
        )

    if not key.isidentifier():
        raise RuntimeError(
            f"Text key must be a valid Python identifier: {key!r}"
        )


def _apply_legacy_user_text_replacements(merged: dict[str, Any]) -> None:
    """Normalize known stale user wording after all text sources are merged."""
    for key, replacements in _LEGACY_USER_TEXT_REPLACEMENTS.items():
        value = merged.get(key)
        if not isinstance(value, str):
            continue

        for old, new in replacements:
            value = value.replace(old, new)

        merged[key] = value


def _merge_texts() -> dict[str, Any]:
    sources = (
        ("user_texts", dict(_user_texts_module.TEXTS)),
        ("referral_texts", dict(_referral_texts_module.REFERRAL_TEXTS)),
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

    for key, value in dict(_overrides_module.OVERRIDES).items():
        _validate_key(key)
        merged[key] = value

    _apply_legacy_user_text_replacements(merged)
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

    reload(_user_texts_module)
    reload(_referral_texts_module)
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
