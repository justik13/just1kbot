import logging

from aiohttp import web

from bot.handlers.incy_web_templates import (
    NOT_FOUND_HTML,
    SECURITY_HEADERS,
    TOO_MANY_REQUESTS_HTML,
    render_inactive_html,
    render_open_html,
)
from config.settings import get_settings
from database.connection import session_scope
from services.subscription import SubscriptionService
from services.subscription_feed_service import SubscriptionFeedService
from services.subscription_token_service import (
    MAX_SUBSCRIPTION_TOKEN_LENGTH,
    SubscriptionTokenService,
)
from utils.http_rate_limiter import (
    get_trusted_client_ip,
    subscription_feed_rate_limiter,
)

logger = logging.getLogger(__name__)


async def subscription_feed_handler(request: web.Request) -> web.Response:
    client_ip = get_trusted_client_ip(request)
    is_allowed, retry_after = subscription_feed_rate_limiter.check(client_ip)
    if not is_allowed:
        return web.Response(
            status=429,
            text="Too Many Requests",
            headers={
                "Retry-After": str(retry_after),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    token = request.match_info.get("token", "").strip()
    if not token or len(token) > MAX_SUBSCRIPTION_TOKEN_LENGTH:
        return web.Response(status=404, text="Not Found")

    async with session_scope() as session:
        user = await SubscriptionTokenService.get_user_by_token(session, token)
        if not user:
            return web.Response(status=404, text="Not Found")

        # Security: never log full secret token, log user_id only
        logger.info("Subscription feed requested for user_id=%s", user.id)

        status, headers, body = await SubscriptionFeedService.build_feed(session, user)
        return web.Response(
            status=status,
            text=body,
            headers=headers,
        )


async def subscription_open_handler(request: web.Request) -> web.Response:
    client_ip = get_trusted_client_ip(request)
    is_allowed, retry_after = subscription_feed_rate_limiter.check(client_ip)
    if not is_allowed:
        return web.Response(
            status=429,
            text=TOO_MANY_REQUESTS_HTML,
            headers={
                **SECURITY_HEADERS,
                "Retry-After": str(retry_after),
            },
        )

    token = request.match_info.get("token", "").strip()
    if not token or len(token) > MAX_SUBSCRIPTION_TOKEN_LENGTH:
        return web.Response(
            status=404,
            text=NOT_FOUND_HTML,
            headers=SECURITY_HEADERS,
        )

    async with session_scope() as session:
        user = await SubscriptionTokenService.get_user_by_token(session, token)
        if not user:
            return web.Response(
                status=404,
                text=NOT_FOUND_HTML,
                headers=SECURITY_HEADERS,
            )

        # Security: never log full secret token, log user_id only
        logger.info("Subscription open bridge requested for user_id=%s", user.id)

        settings = get_settings()
        sub_url = f"https://{settings.DOMAIN}/sub/{token}"
        deep_link = f"incy://import/{sub_url}"

        has_access = SubscriptionService.check_vpn_access(user)
        if not has_access:
            support_username = getattr(settings, "SUPPORT_USERNAME", "")
            html_content = render_inactive_html(sub_url, support_username)
        else:
            html_content = render_open_html(sub_url, deep_link)

        return web.Response(
            status=200,
            text=html_content,
            headers=SECURITY_HEADERS,
        )
