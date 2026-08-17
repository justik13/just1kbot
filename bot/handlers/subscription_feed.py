import logging
from aiohttp import web

from database.connection import session_scope
from services.subscription_feed_service import SubscriptionFeedService
from services.subscription_token_service import (
    MAX_SUBSCRIPTION_TOKEN_LENGTH,
    SubscriptionTokenService,
)

logger = logging.getLogger(__name__)


async def subscription_feed_handler(request: web.Request) -> web.Response:
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
