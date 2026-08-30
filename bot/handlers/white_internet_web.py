"""HTTP subscription feed endpoint for White Internet (/sub/wl/{token})."""

from __future__ import annotations

import base64
import logging
import os
import urllib.parse
from datetime import datetime

from aiohttp import web
from sqlalchemy import select

from bot import texts
from config.enums import WhiteInternetStatus
from database.connection import session_scope
from database.models import Server, WhiteInternetSubscription
from database.repositories import white_internet_repo
from services.white_internet_service import WhiteInternetService
from utils.datetime_helpers import now_utc
from utils.http_rate_limiter import HttpRateLimiter, get_trusted_client_ip

logger = logging.getLogger(__name__)

# Dedicated rate limiter: 30 requests per minute per IP
_sub_rate_limiter = HttpRateLimiter(rate_per_minute=30.0, burst=10)


async def white_internet_subscription_feed_handler(request: web.Request) -> web.Response:
    """
    HTTP handler serving client configuration feed:
    - Path: /sub/wl/{token}
    - Methods: GET, HEAD
    - Status codes:
        200: Subscription ACTIVE and runtime-synced (Base64 VLESS config list)
        503: Provisioning in progress / sync pending (Retry-After: 5)
        403: EXHAUSTED, EXPIRED, or DISABLED
        404: Unknown token
    """
    client_ip = get_trusted_client_ip(request)
    allowed, retry_after = _sub_rate_limiter.check(client_ip)
    if not allowed:
        return web.Response(
            status=429,
            text=texts.WL_WEB_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry_after), "Cache-Control": "no-store"},
        )

    token = request.match_info.get("token", "").strip()
    if not token or len(token) < 16:
        return web.Response(status=404, text="Not Found", headers={"Cache-Control": "no-store"})

    now = now_utc()

    async with session_scope() as session:
        sub = await white_internet_repo.get_subscription_by_token(session, token)
        if sub is None:
            return web.Response(status=404, text="Not Found", headers={"Cache-Control": "no-store"})

        # Common cache-control headers (never cache subscription feeds)
        common_headers = {
            "Cache-Control": "no-store, private, no-cache, must-revalidate",
            "Pragma": "no-cache",
        }

        # Check pending state
        if sub.status == WhiteInternetStatus.PENDING:
            headers = dict(common_headers)
            headers["Retry-After"] = "5"
            return web.Response(
                status=503,
                text=texts.WL_WEB_PENDING,
                headers=headers,
            )

        # Check exhausted state
        if sub.status == WhiteInternetStatus.EXHAUSTED:
            headers = dict(common_headers)
            headers["Subscription-Userinfo"] = (
                f"upload={sub.last_uplink_snapshot}; download={sub.last_downlink_snapshot}; "
                f"total={sub.traffic_limit_bytes}; expire={int(sub.expires_at.timestamp())}"
            )
            return web.Response(
                status=403,
                text=texts.WL_WEB_EXHAUSTED,
                headers=headers,
            )

        # Check expired or disabled
        if sub.status in (WhiteInternetStatus.EXPIRED, WhiteInternetStatus.DISABLED) or sub.expires_at <= now:
            return web.Response(
                status=403,
                text=texts.WL_WEB_EXPIRED,
                headers=common_headers,
            )

        # Check active status: verify quota invariant and runtime sync
        available_bytes = await white_internet_repo.get_available_quota_bytes(session, sub.id, now)
        if available_bytes <= 0:
            return web.Response(
                status=403,
                text=texts.WL_WEB_EXHAUSTED,
                headers=common_headers,
            )

        # Runtime Truth Criterion: config must be reconciled before serving
        if sub.actual_version != sub.desired_version:
            headers = dict(common_headers)
            headers["Retry-After"] = "5"
            return web.Response(
                status=503,
                text=texts.WL_WEB_UNSYNCED,
                headers=headers,
            )

        # Determine CDN domain
        cdn_domain = os.getenv("WHITE_INTERNET_CDN_DOMAIN")
        if not cdn_domain:
            # Fallback to origin server's hostname or app domain
            stmt_server = select(Server).where(Server.id == sub.origin_node_id)
            res_server = await session.execute(stmt_server)
            server = res_server.scalar_one_or_none()
            if server and server.api_url:
                parsed = urllib.parse.urlparse(server.api_url)
                cdn_domain = parsed.hostname or os.getenv("DOMAIN", "origin.example.com")
            else:
                cdn_domain = os.getenv("DOMAIN", "origin.example.com")

        # Generate VLESS links
        vless_links = WhiteInternetService.generate_vless_links(sub, cdn_domain=cdn_domain)
        payload = "\n".join(vless_links)
        b64_payload = base64.b64encode(payload.encode("utf-8")).decode("utf-8")

        response_headers = dict(common_headers)
        response_headers.update({
            "Content-Type": "text/plain; charset=utf-8",
            "Subscription-Userinfo": (
                f"upload={sub.last_uplink_snapshot}; download={sub.last_downlink_snapshot}; "
                f"total={sub.traffic_limit_bytes}; expire={int(sub.expires_at.timestamp())}"
            ),
            "Profile-Update-Interval": "6",
            "Hide-Url": "1",
            "No-Limit-Enabled": "1",
        })

        return web.Response(status=200, text=b64_payload, headers=response_headers)


def setup_white_internet_web_routes(app: web.Application) -> None:
    """Register White Internet HTTP subscription feed routes."""
    app.router.add_get("/sub/wl/{token}", white_internet_subscription_feed_handler)
    logger.info("White Internet subscription feed route registered: GET/HEAD /sub/wl/{token}")
