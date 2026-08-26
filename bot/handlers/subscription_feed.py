"""Backward-compatibility shim for Subscription Feed handlers."""

from config.settings import get_settings
from database.connection import session_scope
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
from services.subscription import SubscriptionService
from utils.http_rate_limiter import (
    get_trusted_client_ip,
    subscription_feed_rate_limiter,
)

__all__ = [
    "subscription_feed_handler",
    "subscription_open_handler",
    "SubscriptionTokenService",
    "SubscriptionFeedService",
    "SubscriptionService",
    "MAX_SUBSCRIPTION_TOKEN_LENGTH",
    "NOT_FOUND_HTML",
    "SECURITY_HEADERS",
    "TOO_MANY_REQUESTS_HTML",
    "render_inactive_html",
    "render_open_html",
    "session_scope",
    "get_settings",
    "get_trusted_client_ip",
    "subscription_feed_rate_limiter",
]
