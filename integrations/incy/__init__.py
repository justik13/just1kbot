"""INCY Application & Subscription Feed Integration module."""

import logging
from typing import ClassVar

from aiogram import Router
from aiohttp import web

from integrations.base import BaseIntegration
from integrations.incy.bot_routes import (
    rotate_incy_subscription,
    show_incy_subscription,
)
from integrations.incy.bot_routes import (
    router as incy_router,
)
from integrations.incy.feed_service import SubscriptionFeedService
from integrations.incy.token_service import (
    MAX_SUBSCRIPTION_TOKEN_LENGTH,
    SubscriptionTokenService,
)
from integrations.incy.web_routes import (
    subscription_feed_handler,
    subscription_open_handler,
)
from integrations.incy.web_templates import (
    NOT_FOUND_HTML,
    SECURITY_HEADERS,
    TOO_MANY_REQUESTS_HTML,
    render_inactive_html,
    render_open_html,
)

logger = logging.getLogger(__name__)


class IncyIntegration(BaseIntegration):
    """Encapsulated INCY application & subscription feed integration."""

    name: ClassVar[str] = "incy"

    @classmethod
    def is_enabled(cls) -> bool:
        """Check if INCY subscription feature is enabled and domain is available."""
        return SubscriptionTokenService.is_enabled()

    @classmethod
    def register_web_routes(cls, app: web.Application) -> None:
        """Register INCY subscription feed endpoints only if enabled."""
        if not cls.is_enabled():
            return
        app.router.add_get("/sub/open/{token}", subscription_open_handler)
        app.router.add_get("/subscription/open/{token}", subscription_open_handler)
        app.router.add_get("/sub/{token}", subscription_feed_handler)
        app.router.add_get("/subscription/{token}", subscription_feed_handler)
        logger.info("Subscription feed endpoint registered: GET /sub/{token} & GET /subscription/{token}")
        logger.info("Subscription open endpoint registered: GET /sub/open/{token} & GET /subscription/open/{token}")

    @classmethod
    def get_bot_router(cls) -> Router | None:
        """Return aiogram Router for INCY."""
        return incy_router


__all__ = [
    "MAX_SUBSCRIPTION_TOKEN_LENGTH",
    "NOT_FOUND_HTML",
    "SECURITY_HEADERS",
    "TOO_MANY_REQUESTS_HTML",
    "IncyIntegration",
    "SubscriptionFeedService",
    "SubscriptionTokenService",
    "incy_router",
    "render_inactive_html",
    "render_open_html",
    "rotate_incy_subscription",
    "show_incy_subscription",
    "subscription_feed_handler",
    "subscription_open_handler",
]
