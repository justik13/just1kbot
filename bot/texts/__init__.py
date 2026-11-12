"""Canonical texts facade for backend services, notifications, and HTTP endpoints."""
from __future__ import annotations

from bot.texts import backend as _backend
from bot.texts import runtime as _runtime
from bot.texts.backend import *  # noqa: F403
from bot.texts.runtime import *  # noqa: F403


def _collect_public_names(module) -> tuple[str, ...]:
    explicit = getattr(module, "__all__", None)
    if explicit is not None:
        return tuple(explicit)
    return tuple(name for name in vars(module) if name.isupper() and not name.startswith("_"))


def _assert_no_key_collisions() -> None:
    """Fail fast if any text key is defined in two domain modules."""
    seen: dict[str, str] = {}
    for _mod, _dom in (
        (_backend, "backend"),
        (_runtime, "runtime"),
    ):
        for _name in _collect_public_names(_mod):
            if _name in seen:
                raise ImportError(
                    f"texts key collision: {_name!r} defined in both "
                    f"{seen[_name]} and {_dom}"
                )
            seen[_name] = _dom


_assert_no_key_collisions()

_TEXT_KEYS = frozenset(name for name, value in globals().items() if name.isupper() and isinstance(value, str))

_TEXTS: dict[str, str] = {name: globals()[name] for name in _TEXT_KEYS}


def get_all_text_keys() -> set[str]:
    """Return the set of canonical text keys exposed by the facade."""
    return set(_TEXT_KEYS)


def get_text(key: str, default=None, **kwargs):
    """Return a text by key and optionally format it with keyword arguments.

    Missing keys return *default* (None by default) to preserve the historical
    "probe" API. Formatting errors are surfaced loudly instead of silently
    returning the unformatted template, so placeholder typos cannot hide.
    """
    value = _TEXTS.get(key, default)
    if kwargs and isinstance(value, str):
        try:
            return value.format(**kwargs)
        except (KeyError, IndexError, ValueError) as exc:
            raise ValueError(
                f"Invalid placeholders for text key {key!r}: {exc}."
            ) from exc
    return value


def reload_texts() -> None:
    """Rebuild the derived `_TEXTS` cache from current module globals.

    The catalogue itself is static; this only re-syncs the cache so test
    monkeypatching of existing keys is picked up via `get_text`.
    """
    _TEXTS.update({name: globals()[name] for name in _TEXT_KEYS if name in globals()})
