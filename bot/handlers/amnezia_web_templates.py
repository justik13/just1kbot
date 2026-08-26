"""Backward-compatibility shim for Amnezia Web Templates."""

from integrations.amnezia_bridge.web_templates import (
    AMNEZIA_SECURITY_HEADERS,
    BASE_CSS,
    BRIDGE_JS,
    render_500_html,
    render_amnezia_bridge_html,
    render_error_html,
    render_expired_html,
)

__all__ = [
    "AMNEZIA_SECURITY_HEADERS",
    "BASE_CSS",
    "BRIDGE_JS",
    "render_500_html",
    "render_amnezia_bridge_html",
    "render_error_html",
    "render_expired_html",
]
