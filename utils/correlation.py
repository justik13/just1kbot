"""Correlation context management for request tracing across logs."""

from __future__ import annotations

import logging
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="system")


class CorrelationFilter(logging.Filter):
    """Logging filter that injects the current request_id into each LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get("system")
        return True


def get_current_request_id() -> str:
    """Return the active correlation request_id or 'system'."""
    return request_id_var.get("system")


def set_request_id(request_id: str) -> None:
    """Set the active correlation request_id for the current context."""
    request_id_var.set(request_id)
