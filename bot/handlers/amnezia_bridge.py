"""Backward-compatibility shim for Amnezia Bridge handler."""

from database.connection import session_scope
from database.repositories.profiles_repo import get_profile_by_id
from database.repositories.users_repo import get_user_by_id
from integrations.amnezia_bridge.token_service import AmneziaBridgeTokenService
from integrations.amnezia_bridge.web_routes import amnezia_bridge_handler
from integrations.amnezia_bridge.web_templates import (
    AMNEZIA_SECURITY_HEADERS,
    render_500_html,
    render_amnezia_bridge_html,
    render_error_html,
    render_expired_html,
)
from services.subscription import SubscriptionService
from utils.http_rate_limiter import amnezia_bridge_rate_limiter, get_trusted_client_ip
from utils.vpn_helpers import build_display_vpn_uri

__all__ = [
    "amnezia_bridge_handler",
    "AmneziaBridgeTokenService",
    "SubscriptionService",
    "get_profile_by_id",
    "get_user_by_id",
    "session_scope",
    "render_500_html",
    "render_amnezia_bridge_html",
    "render_error_html",
    "render_expired_html",
    "amnezia_bridge_rate_limiter",
    "get_trusted_client_ip",
    "build_display_vpn_uri",
    "AMNEZIA_SECURITY_HEADERS",
]
