"""Amnezia 1-Click Web Bridge Integration module."""

import logging
from typing import ClassVar

from aiohttp import web

from integrations.amnezia_bridge.constants import (
    BRIDGE_TOKEN_MAX_FUTURE_SKEW_SECONDS,
    BRIDGE_TOKEN_TTL_SECONDS,
    BRIDGE_TOKEN_VERSION,
    MAX_BRIDGE_REQUEST_TARGET_BYTES,
    MAX_RAW_CONFIG_BYTES,
    RATE_LIMIT_BURST,
    RATE_LIMIT_REQUESTS_PER_MINUTE,
)
from integrations.amnezia_bridge.token_service import AmneziaBridgeTokenService
from integrations.amnezia_bridge.web_routes import amnezia_bridge_handler
from integrations.amnezia_bridge.web_templates import (
    AMNEZIA_SECURITY_HEADERS,
    render_500_html,
    render_amnezia_bridge_html,
    render_error_html,
    render_expired_html,
)
from integrations.base import BaseIntegration

logger = logging.getLogger(__name__)


class AmneziaBridgeIntegration(BaseIntegration):
    """Encapsulated 1-Click Amnezia Web Bridge integration."""

    name: ClassVar[str] = "amnezia_bridge"

    @classmethod
    def is_enabled(cls) -> bool:
        """Check if Amnezia bridge is enabled via HMAC secret and domain."""
        return AmneziaBridgeTokenService.is_enabled()

    @classmethod
    def register_web_routes(cls, app: web.Application) -> None:
        """Register Amnezia Bridge web endpoints only if enabled."""
        if not cls.is_enabled():
            return
        app.router.add_get("/amnezia/open/{profile_id}", amnezia_bridge_handler)
        logger.info("Amnezia bridge endpoint registered: GET /amnezia/open/{profile_id}")

    @classmethod
    def build_bridge_url(
        cls,
        domain: str,
        profile_id: int,
        user_id: int,
        ttl_seconds: int = BRIDGE_TOKEN_TTL_SECONDS,
        secret: str | None = None,
    ) -> str:
        return AmneziaBridgeTokenService.build_bridge_url(
            domain=domain,
            profile_id=profile_id,
            user_id=user_id,
            ttl_seconds=ttl_seconds,
            secret=secret,
        )


__all__ = [
    "AMNEZIA_SECURITY_HEADERS",
    "BRIDGE_TOKEN_MAX_FUTURE_SKEW_SECONDS",
    "BRIDGE_TOKEN_TTL_SECONDS",
    "BRIDGE_TOKEN_VERSION",
    "MAX_BRIDGE_REQUEST_TARGET_BYTES",
    "MAX_RAW_CONFIG_BYTES",
    "RATE_LIMIT_BURST",
    "RATE_LIMIT_REQUESTS_PER_MINUTE",
    "AmneziaBridgeIntegration",
    "AmneziaBridgeTokenService",
    "amnezia_bridge_handler",
    "render_500_html",
    "render_amnezia_bridge_html",
    "render_error_html",
    "render_expired_html",
]
