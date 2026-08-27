"""Unified text catalogue facade.

Canonical user-facing text ownership lives exclusively in the domain modules below.
This module is intentionally a thin backwards-compatible facade: application code may
continue to use ``from bot import texts`` without knowing the domain layout.
"""

from bot.texts.admin import *  # noqa: F401,F403
from bot.texts.common import *  # noqa: F401,F403
from bot.texts.connection import *  # noqa: F401,F403
from bot.texts.payment import *  # noqa: F401,F403
from bot.texts.runtime import *  # noqa: F401,F403
from bot.texts.user import *  # noqa: F401,F403

_TEXT_KEYS = frozenset(name for name in globals() if name.isupper())


def get_all_text_keys() -> set[str]:
    """Return the set of canonical text keys exposed by the facade."""
    return set(_TEXT_KEYS)


def get_text(key: str, default=None, **kwargs):
    """Return a text by key and optionally format it with keyword arguments."""
    value = globals().get(key, default)
    if kwargs and isinstance(value, str):
        try:
            return value.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return value
    return value


__all__ = ["get_all_text_keys", "get_text", *_TEXT_KEYS]
