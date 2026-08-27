# bot/texts.py
"""Centralized Source-of-Truth (SSOT) texts loader and registry for bot UI."""

from __future__ import annotations

import importlib
import logging
import pkgutil
from types import ModuleType
from typing import Any

import bot.texts_data

logger = logging.getLogger(__name__)

_ALL_MODULES: list[tuple[str, ModuleType]] = []
_TEXTS: dict[str, Any] = {}
_CORE_EXPORTS = {"get_text", "get_all_text_keys", "reload_texts"}


def _discover_modules() -> list[tuple[str, ModuleType]]:
    """Discover and import/reload all modules in bot.texts_data package."""
    modules: list[tuple[str, ModuleType]] = []

    def iter_namespace(ns_pkg):
        return pkgutil.iter_modules(ns_pkg.__path__, ns_pkg.__name__ + ".")

    for _finder, name, ispkg in iter_namespace(bot.texts_data):
        if ispkg:
            pkg = importlib.import_module(name)
            for _sub_finder, sub_name, _sub_ispkg in iter_namespace(pkg):
                mod = importlib.import_module(sub_name)
                modules.append((sub_name.replace("bot.texts_data.", ""), mod))
        else:
            mod = importlib.import_module(name)
            modules.append((name.replace("bot.texts_data.", ""), mod))

    return modules


def _validate_key(key: Any) -> None:
    if not isinstance(key, str):
        raise RuntimeError(f"Text key must be string, got {type(key).__name__}: {key!r}")
    if not key.isidentifier():
        raise RuntimeError(f"Text key must be a valid Python identifier: {key!r}")


def _merge_texts(modules: list[tuple[str, ModuleType]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    key_sources: dict[str, str] = {}

    for source_name, module in modules:
        source_texts: dict[str, Any] = dict(getattr(module, "TEXTS", {}))

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


def _rebuild() -> None:
    global _ALL_MODULES, _TEXTS
    # 1. Discover all current modules
    current_modules = _discover_modules()

    # 2. Reload each module
    reloaded_modules: list[tuple[str, ModuleType]] = []
    for source_name, mod in current_modules:
        reloaded_mod = importlib.reload(mod)
        reloaded_modules.append((source_name, reloaded_mod))

    # 3. Merge texts atomically
    new_texts = _merge_texts(reloaded_modules)

    # 4. Remove stale keys from globals()
    stale_keys = set(_TEXTS.keys()) - set(new_texts.keys())
    g = globals()
    for stale_k in stale_keys:
        g.pop(stale_k, None)

    # 5. Update globals() with new texts and sync __all__
    g.update(new_texts)
    g["__all__"] = list(new_texts.keys()) + list(_CORE_EXPORTS)

    # 6. Atomic swap
    _ALL_MODULES = reloaded_modules
    _TEXTS = new_texts


# Initial load
_ALL_MODULES = _discover_modules()
_TEXTS = _merge_texts(_ALL_MODULES)
globals().update(_TEXTS)

__all__ = list(_TEXTS.keys()) + list(_CORE_EXPORTS)


def get_text(key: str, default: Any = None) -> Any:
    return _TEXTS.get(key, default)


def get_all_text_keys() -> list[str]:
    return list(_TEXTS.keys())


def reload_texts() -> None:
    """Safely rebuild the entire texts registry, discovering and reloading all modules."""
    _rebuild()
