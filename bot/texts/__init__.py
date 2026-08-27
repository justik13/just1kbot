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
from bot.texts.admin.queues import (
    QUEUE_HEALTH_PROBLEM_DEAD as QUEUE_HEALTH_PROBLEM_DEAD,
    QUEUE_HEALTH_PROBLEM_OVERDUE as QUEUE_HEALTH_PROBLEM_OVERDUE,
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
from bot.texts.connection.config import (
    CONNECTION_CONFIG_DEVICE_VIEW_KEY_BLOCKQUOTE as CONNECTION_CONFIG_DEVICE_VIEW_KEY_BLOCKQUOTE,
    CONNECTION_LIST_DEVICE_ROW_FORMAT as CONNECTION_LIST_DEVICE_ROW_FORMAT,
)
from bot.texts.runtime.alerts import (
    ALERT_QUEUE_RECOVERED as ALERT_QUEUE_RECOVERED,
    ALERT_QUEUE_UNHEALTHY as ALERT_QUEUE_UNHEALTHY,
    QUEUE_HEALTH_DEAD_PROBLEM as QUEUE_HEALTH_DEAD_PROBLEM,
    QUEUE_HEALTH_OVERDUE_PROBLEM as QUEUE_HEALTH_OVERDUE_PROBLEM,
    QUEUE_HEALTH_STALE_PROBLEM as QUEUE_HEALTH_STALE_PROBLEM,
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
