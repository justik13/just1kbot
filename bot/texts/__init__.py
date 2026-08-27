"""Unified text catalogue facade.

Canonical user-facing text ownership lives exclusively in the domain modules below.
This module is intentionally a thin backwards-compatible facade: application code may
continue to use ``from bot import texts`` without knowing the domain layout.
"""

from bot.texts.admin import *  # noqa: F403
from bot.texts.common import *  # noqa: F403
from bot.texts.connection import *  # noqa: F403
from bot.texts.payment import *  # noqa: F403
from bot.texts.runtime import *  # noqa: F403
from bot.texts.user import *  # noqa: F403

# Re-export canonical keys introduced in domain modules but not yet present in the
# package-level domain __all__ lists. Ownership remains in the domain files.
from bot.texts.admin.broadcast import ADMIN_AUDIT_LOG_DETAILS_BROADCAST
from bot.texts.admin.queues import QUEUE_HEALTH_DEAD_PROBLEM, QUEUE_HEALTH_OVERDUE_PROBLEM
from bot.texts.admin.servers import ADMIN_SERVER_API_URL
from bot.texts.admin.users import (
    ADMIN_AUDIT_LOG_DETAILS_MASS_BONUS,
    ADMIN_USER_ID_NO_COLON_FORMAT,
)
from bot.texts.connection.config import (
    CONNECTION_CONFIG_DEVICE_VIEW_KEY_BLOCKQUOTE,
    CONNECTION_LIST_DEVICE_ROW_FORMAT,
)

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
