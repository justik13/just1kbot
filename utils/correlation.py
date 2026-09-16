"""Correlation context management for request tracing across logs."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator

__all__ = [
    "CorrelationFilter",
    "correlation_scope",
    "get_current_request_id",
    "request_id_var",
    "reset_request_id",
    "set_request_id",
]

request_id_var: ContextVar[str] = ContextVar("request_id", default="system")


class CorrelationFilter(logging.Filter):
    """Logging filter that injects the current request_id into each LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get("system")
        return True


def get_current_request_id() -> str:
    """Return the active correlation request_id or 'system'."""
    return request_id_var.get("system")


def set_request_id(request_id: str) -> Token[str]:
    """Set the active correlation request_id for the current context and return the token."""
    return request_id_var.set(request_id)


def reset_request_id(token: Token[str]) -> None:
    """Reset the correlation request_id to the state before set_request_id."""
    request_id_var.reset(token)


@contextmanager
def correlation_scope(request_id: str) -> Iterator[str]:
    """Context manager for setting and safely restoring a correlation request_id."""
    token = request_id_var.set(request_id)
    try:
        yield request_id
    finally:
        request_id_var.reset(token)

