# bot/texts.py
import logging
import importlib
import pkgutil
from typing import Any
import bot.texts_data

logger = logging.getLogger(__name__)

_ALL_MODULES = []

def _load_all_modules():
    global _ALL_MODULES
    _ALL_MODULES = []
    
    def iter_namespace(ns_pkg):
        return pkgutil.iter_modules(ns_pkg.__path__, ns_pkg.__name__ + ".")
        
    for _finder, name, ispkg in iter_namespace(bot.texts_data):
        if ispkg:
            # e.g. bot.texts_data.admin
            pkg = importlib.import_module(name)
            for _sub_finder, sub_name, _sub_ispkg in iter_namespace(pkg):
                mod = importlib.import_module(sub_name)
                _ALL_MODULES.append((sub_name.replace("bot.texts_data.", ""), mod))
        else:
            mod = importlib.import_module(name)
            _ALL_MODULES.append((name.replace("bot.texts_data.", ""), mod))

_load_all_modules()

def _validate_key(key: Any) -> None:
    if not isinstance(key, str):
        raise RuntimeError(f"Text key must be string, got {type(key).__name__}: {key!r}")
    if not key.isidentifier():
        raise RuntimeError(f"Text key must be a valid Python identifier: {key!r}")

def _merge_texts() -> dict[str, Any]:
    merged: dict[str, Any] = {}
    key_sources: dict[str, str] = {}

    for source_name, module in _ALL_MODULES:
        source_texts = getattr(module, "TEXTS", {})
        
        # also collect any uppercase module-level variables
        for var_name in dir(module):
            if var_name.isupper() and not var_name.startswith('_') and var_name != 'TEXTS':
                source_texts[var_name] = getattr(module, var_name)
                
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
    return _TEXTS.get(key, default)

def get_all_text_keys() -> list[str]:
    return list(_TEXTS.keys())

def reload_texts() -> None:
    global _TEXTS
    for _, module in _ALL_MODULES:
        importlib.reload(module)
    _TEXTS = _merge_texts()
    globals().update(_TEXTS)
