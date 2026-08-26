"""Backward-compatibility shim for INCY Web Templates."""

from integrations.incy.web_templates import (
    NOT_FOUND_HTML,
    SECURITY_HEADERS,
    TOO_MANY_REQUESTS_HTML,
    render_inactive_html,
    render_open_html,
)

__all__ = [
    "NOT_FOUND_HTML",
    "SECURITY_HEADERS",
    "TOO_MANY_REQUESTS_HTML",
    "render_inactive_html",
    "render_open_html",
]
