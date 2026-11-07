"""HTTP subscription feed endpoint for unified subscription access (/sub/{token})."""

from __future__ import annotations

import logging
from aiohttp import web

from database.connection import session_scope
from services.unified_subscription_service import UnifiedSubscriptionService

logger = logging.getLogger(__name__)


async def unified_subscription_feed_handler(request: web.Request) -> web.Response:
    """Serve Base64 feed or browser onboarding for unified Standard and White Internet access."""
    token = request.match_info.get("token", "").strip()
    if not token or len(token) < 16:
        return web.Response(
            status=404,
            text="Not Found",
            headers={"Cache-Control": "no-store", "CDN-Cache-Control": "no-store"},
        )

    async with session_scope() as session:
        status_code, body, headers = await UnifiedSubscriptionService.build_subscription_feed(
            session, request, token
        )
        return web.Response(status=status_code, text=body, headers=headers)


def setup_unified_web_routes(app: web.Application) -> None:
    """Register HTTP routes for unified subscription feed and backwards compatibility aliases."""
    app.router.add_get("/sub/{token}", unified_subscription_feed_handler)
    app.router.add_get("/sub/access/{token}", unified_subscription_feed_handler)
    app.router.add_get("/sub/vless/{token}", unified_subscription_feed_handler)
    app.router.add_get("/sub/wl/{token}", unified_subscription_feed_handler)
    logger.info("Unified subscription feed route registered: GET /sub/{token}")
