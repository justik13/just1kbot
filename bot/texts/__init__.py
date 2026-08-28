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

from bot.texts.admin.broadcast import (
    ADMIN_AUDIT_LOG_DETAILS_BROADCAST as ADMIN_AUDIT_LOG_DETAILS_BROADCAST,
)
from bot.texts.admin.dashboard import (
    ADMIN_DASHBOARD_PROXY_TAB_LABEL as ADMIN_DASHBOARD_PROXY_TAB_LABEL,
    ADMIN_DASHBOARD_SERVER_ROW_FORMAT as ADMIN_DASHBOARD_SERVER_ROW_FORMAT,
)
from bot.texts.admin.finances import (
    ADMIN_PURCHASES_ROW_FORMAT as ADMIN_PURCHASES_ROW_FORMAT,
)
from bot.texts.admin.servers import (
    ADMIN_AUDIT_LOG_DETAILS_DELETE_SERVER as ADMIN_AUDIT_LOG_DETAILS_DELETE_SERVER,
    ADMIN_AUDIT_LOG_DETAILS_EDIT_SERVER as ADMIN_AUDIT_LOG_DETAILS_EDIT_SERVER,
    ADMIN_AUDIT_LOG_DETAILS_EDIT_SERVER_REDACTED as ADMIN_AUDIT_LOG_DETAILS_EDIT_SERVER_REDACTED,
    ADMIN_SERVER_API_URL as ADMIN_SERVER_API_URL,
)
from bot.texts.admin.tariffs import (
    ADMIN_AUDIT_LOG_DETAILS_TARIFF_EDIT_RUB as ADMIN_AUDIT_LOG_DETAILS_TARIFF_EDIT_RUB,
    ADMIN_AUDIT_LOG_DETAILS_TARIFF_TOGGLED as ADMIN_AUDIT_LOG_DETAILS_TARIFF_TOGGLED,
)
from bot.texts.admin.users import (
    ADMIN_AUDIT_LOG_DETAILS_MASS_BONUS as ADMIN_AUDIT_LOG_DETAILS_MASS_BONUS,
    ADMIN_USER_ID_FORMAT as ADMIN_USER_ID_FORMAT,
    ADMIN_USER_ID_NO_COLON_FORMAT as ADMIN_USER_ID_NO_COLON_FORMAT,
)
from bot.texts.common.status import MAINTENANCE_DEFAULT_MESSAGE as MAINTENANCE_DEFAULT_MESSAGE
from bot.texts.runtime.alerts import (
    ALERT_QUEUE_RECOVERED as ALERT_QUEUE_RECOVERED,
    ALERT_QUEUE_UNHEALTHY as ALERT_QUEUE_UNHEALTHY,
    QUEUE_HEALTH_DEAD_PROBLEM as QUEUE_HEALTH_DEAD_PROBLEM,
    QUEUE_HEALTH_OVERDUE_PROBLEM as QUEUE_HEALTH_OVERDUE_PROBLEM,
    QUEUE_HEALTH_STALE_PROBLEM as QUEUE_HEALTH_STALE_PROBLEM,
)

from bot.texts import admin as _admin
from bot.texts import common as _common
from bot.texts import connection as _connection
from bot.texts import payment as _payment
from bot.texts import runtime as _runtime
from bot.texts import user as _user


def _assert_no_key_collisions() -> None:
    """Fail fast if any text key is defined in two domain modules."""
    seen: dict[str, str] = {}
    for _mod, _dom in (
        (_admin, "admin"),
        (_common, "common"),
        (_connection, "connection"),
        (_payment, "payment"),
        (_runtime, "runtime"),
        (_user, "user"),
    ):
        for _name in getattr(_mod, "__all__", ()):
            if _name in seen:
                raise ImportError(
                    f"texts key collision: {_name!r} defined in both "
                    f"{seen[_name]} and {_dom}"
                )
            seen[_name] = _dom


_assert_no_key_collisions()

_TEXT_KEYS = frozenset(name for name, value in globals().items() if name.isupper() and isinstance(value, str))


def get_all_text_keys() -> set[str]:
    """Return the set of canonical text keys exposed by the facade."""
    return set(_TEXT_KEYS)


def get_text(key: str, default=None, **kwargs):
    """Return a text by key and optionally format it with keyword arguments."""
    value = globals().get(key, default)
    if value is None:
        if kwargs and default is None:
            raise ValueError(f"Missing text key {key!r} but kwargs were provided.")
        return default
        
    if not isinstance(value, str):
        return value
        
    if kwargs:
        try:
            return value.format(**kwargs)
        except (KeyError, IndexError, ValueError) as exc:
            raise ValueError(f"Invalid placeholders for text key {key!r}: {exc}.") from exc
    elif "{" in value and "}" in value:
        import string
        if any(fname is not None for _, fname, _, _ in string.Formatter().parse(value)):
            raise ValueError(f"Missing kwargs for text key {key!r} which requires formatting.")
            
    return value
