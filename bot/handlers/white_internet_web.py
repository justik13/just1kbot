"""HTTP subscription feed endpoint for White Internet (/sub/wl/{token})."""

from __future__ import annotations

import base64
import logging
import os
import urllib.parse

from aiohttp import web
from sqlalchemy import select

from bot import texts
from config.enums import WhiteInternetStatus
from database.connection import session_scope
from database.models import Server
from database.repositories import white_internet_repo
from services.white_internet_service import WhiteInternetService
from utils.datetime_helpers import now_utc
from utils.http_rate_limiter import HttpRateLimiter, get_trusted_client_ip

logger = logging.getLogger(__name__)

# Rate limit is deliberately scoped to both caller IP and secret token. This
# prevents one token from consuming the entire IP bucket shared by other users.
_sub_rate_limiter = HttpRateLimiter(rate_per_minute=30.0, burst=10)


async def white_internet_subscription_feed_handler(request: web.Request) -> web.Response:
    """Serve the no-store Base64 subscription feed only after runtime reconciliation."""
    token = request.match_info.get("token", "").strip()
    client_ip = get_trusted_client_ip(request)
    rate_key = f"{client_ip}:{token}"
    allowed, retry_after = _sub_rate_limiter.check(rate_key)
    if not allowed:
        return web.Response(
            status=429,
            text=texts.WL_WEB_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry_after), "Cache-Control": "no-store"},
        )

    if not token or len(token) < 16:
        return web.Response(status=404, text="Not Found", headers={"Cache-Control": "no-store"})

    now = now_utc()
    common_headers = {
        "Cache-Control": "no-store, private, no-cache, must-revalidate",
        "Pragma": "no-cache",
    }

    async with session_scope() as session:
        sub = await white_internet_repo.get_subscription_by_token(session, token)
        if sub is None:
            return web.Response(status=404, text="Not Found", headers=common_headers)

        if sub.status == WhiteInternetStatus.PENDING:
            headers = dict(common_headers)
            headers["Retry-After"] = "5"
            return web.Response(status=503, text=texts.WL_WEB_PENDING, headers=headers)

        if sub.status == WhiteInternetStatus.EXHAUSTED:
            headers = dict(common_headers)
            headers["Subscription-Userinfo"] = (
                f"upload={sub.last_uplink_snapshot}; download={sub.last_downlink_snapshot}; "
                f"total={sub.traffic_limit_bytes}; expire={int(sub.expires_at.timestamp())}"
            )
            return web.Response(status=403, text=texts.WL_WEB_EXHAUSTED, headers=headers)

        if sub.status in (WhiteInternetStatus.EXPIRED, WhiteInternetStatus.DISABLED) or sub.expires_at <= now:
            return web.Response(status=403, text=texts.WL_WEB_EXPIRED, headers=common_headers)

        available_bytes = await white_internet_repo.get_available_quota_bytes(session, sub.id, now)
        if available_bytes <= 0:
            return web.Response(status=403, text=texts.WL_WEB_EXHAUSTED, headers=common_headers)

        server = await session.scalar(select(Server).where(Server.id == sub.origin_node_id))
        if server is None or "xray_origin" not in (server.capabilities or []):
            headers = dict(common_headers)
            headers["Retry-After"] = "5"
            return web.Response(status=503, text=texts.WL_WEB_UNSYNCED, headers=headers)

        # Runtime Truth Criterion: both configuration version and the physical
        # Xray generation must match. Version equality alone is insufficient
        # after an Xray restart because in-memory users are lost.
        if (
            sub.actual_version != sub.desired_version
            or not server.xray_instance_epoch
            or sub.last_reconciled_node_epoch != server.xray_instance_epoch
        ):
            headers = dict(common_headers)
            headers["Retry-After"] = "5"
            return web.Response(status=503, text=texts.WL_WEB_UNSYNCED, headers=headers)

        cdn_domain = os.getenv("WHITE_INTERNET_CDN_DOMAIN")
        if not cdn_domain:
            parsed = urllib.parse.urlparse(server.api_url)
            cdn_domain = parsed.hostname or os.getenv("DOMAIN", "origin.example.com")

        vless_links = WhiteInternetService.generate_vless_links(sub, cdn_domain=cdn_domain)
        payload = "\n".join(vless_links)
        b64_payload = base64.b64encode(payload.encode("utf-8")).decode("utf-8")

        response_headers = dict(common_headers)
        response_headers.update(
            {
                "Content-Type": "text/plain; charset=utf-8",
                "Subscription-Userinfo": (
                    f"upload={sub.last_uplink_snapshot}; "
                    f"download={max(0, sub.traffic_used_bytes - sub.last_uplink_snapshot)}; "
                    f"total={sub.traffic_limit_bytes}; expire={int(sub.expires_at.timestamp())}"
                ),

                "Profile-Update-Interval": "6",
                "Hide-Url": "1",
                "No-Limit-Enabled": "1",
            }
        )
        return web.Response(status=200, text=b64_payload, headers=response_headers)


def setup_white_internet_web_routes(app: web.Application) -> None:
    """Register White Internet HTTP subscription feed route."""
    app.router.add_get("/sub/wl/{token}", white_internet_subscription_feed_handler)
    logger.info("White Internet subscription feed route registered: GET /sub/wl/{token}")
