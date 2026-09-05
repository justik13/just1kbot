"""HTTP subscription feed endpoint for White Internet (/sub/wl/{token})."""

from __future__ import annotations

import base64
import json
import logging
import os

from aiohttp import web

from sqlalchemy import select

from bot import texts
from config.constants import (
    DEFAULT_WHITE_INTERNET_PATH,
    WHITE_INTERNET_BASE_TRAFFIC_BYTES,
    WHITE_INTERNET_SUB_PATH_PREFIX,
    XRAY_PROTOCOL,
)
from config.enums import ServerHealthState, WhiteInternetStatus
from database.connection import session_scope
from database.models import Server
from database.repositories import users_repo, white_internet_repo
from services.white_internet_service import WhiteInternetService
from utils.datetime_helpers import now_utc
from utils.http_rate_limiter import HttpRateLimiter, get_trusted_client_ip
from utils.security import normalize_public_domain

logger = logging.getLogger(__name__)

# Scoped rate limiters: IP bucket prevents unauthenticated DoS / token brute force,
# token bucket prevents single subscription thrashing across rotating IPs.
_ip_rate_limiter = HttpRateLimiter(rate_per_minute=60.0, burst=15)
_token_rate_limiter = HttpRateLimiter(rate_per_minute=30.0, burst=10)


async def white_internet_subscription_feed_handler(request: web.Request) -> web.Response:
    """Serve the no-store Base64 subscription feed only after runtime reconciliation."""
    client_ip = get_trusted_client_ip(request)
    allowed_ip, retry_after_ip = _ip_rate_limiter.check(client_ip)
    if not allowed_ip:
        return web.Response(
            status=429,
            text=texts.WL_WEB_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry_after_ip), "Cache-Control": "no-store"},
        )

    token = request.match_info.get("token", "").strip()
    if not token or len(token) < 16:
        return web.Response(status=404, text="Not Found", headers={"Cache-Control": "no-store"})

    allowed_tok, retry_after_tok = _token_rate_limiter.check(token)
    if not allowed_tok:
        return web.Response(
            status=429,
            text=texts.WL_WEB_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry_after_tok), "Cache-Control": "no-store"},
        )

    now = now_utc()
    common_headers = {
        "Cache-Control": "no-store, private, no-cache, must-revalidate",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
    }

    async with session_scope() as session:
        sub = await white_internet_repo.get_subscription_by_token(session, token)
        if sub is None:
            return web.Response(status=404, text="Not Found", headers=common_headers)

        user = await users_repo.get_user_by_id(session, sub.user_id)
        if (
            user is None
            or getattr(user, "is_banned", False) is True
            or getattr(user, "is_deleted", False) is True
        ):
            return web.Response(status=403, text="Forbidden", headers=common_headers)

        if sub.status == WhiteInternetStatus.PENDING:
            headers = dict(common_headers)
            headers["Retry-After"] = "5"
            return web.Response(status=503, text=texts.WL_WEB_PENDING, headers=headers)

        if (
            sub.status in (WhiteInternetStatus.EXPIRED, WhiteInternetStatus.DISABLED)
            or sub.expires_at <= now
        ):
            return web.Response(status=403, text=texts.WL_WEB_EXPIRED, headers=common_headers)

        traffic_limit = getattr(sub, "traffic_limit_bytes", None)
        base_bytes = getattr(sub, "base_traffic_bytes", None)
        extra_bytes = getattr(sub, "extra_traffic_bytes", None)
        if isinstance(traffic_limit, (int, float)):
            total_quota = int(traffic_limit)
        elif isinstance(base_bytes, (int, float)) or isinstance(extra_bytes, (int, float)):
            total_quota = (int(base_bytes) if isinstance(base_bytes, (int, float)) else 0) + (
                int(extra_bytes) if isinstance(extra_bytes, (int, float)) else 0
            )
        else:
            total_quota = WHITE_INTERNET_BASE_TRAFFIC_BYTES

        used_val = (
            int(sub.traffic_used_bytes)
            if isinstance(getattr(sub, "traffic_used_bytes", None), (int, float))
            else 0
        )
        available_bytes = max(0, total_quota - used_val)
        expire_ts = (
            int(sub.expires_at.timestamp())
            if hasattr(getattr(sub, "expires_at", None), "timestamp")
            else 0
        )
        upload_bytes = (
            int(sub.traffic_uplink_bytes)
            if isinstance(getattr(sub, "traffic_uplink_bytes", None), (int, float))
            else 0
        )
        download_bytes = (
            int(sub.traffic_downlink_bytes)
            if isinstance(getattr(sub, "traffic_downlink_bytes", None), (int, float))
            else 0
        )

        if sub.status == WhiteInternetStatus.EXHAUSTED or available_bytes <= 0:
            headers = dict(common_headers)
            headers["Subscription-Userinfo"] = texts.WL_USERINFO_HEADER_TEMPLATE.format(
                upload=upload_bytes,
                download=download_bytes,
                total=total_quota,
                expire=expire_ts,
            )
            return web.Response(status=403, text=texts.WL_WEB_EXHAUSTED, headers=headers)

        server = await session.scalar(select(Server).where(Server.id == sub.origin_node_id))
        if (
            server is None
            or not server.is_active
            or getattr(server, "protocol", None) != XRAY_PROTOCOL
            or server.health_state != ServerHealthState.ONLINE
            or "xray_origin" not in (server.capabilities or [])
        ):
            headers = dict(common_headers)
            headers["Retry-After"] = "5"
            return web.Response(status=503, text=texts.WL_WEB_UNSYNCED, headers=headers)

        if (
            sub.actual_version != sub.desired_version
            or sub.last_reconciled_node_epoch != server.xray_instance_epoch
        ):
            headers = dict(common_headers)
            headers["Retry-After"] = "5"
            return web.Response(status=503, text=texts.WL_WEB_UNSYNCED, headers=headers)

        extra = server.extra_data if isinstance(getattr(server, "extra_data", None), dict) else {}
        cdn_domain = normalize_public_domain(extra.get("cdn_domain") or os.getenv("WHITE_INTERNET_CDN_DOMAIN"))
        if not cdn_domain:
            logger.error(
                "Configuration error: CDN domain is missing from server extra_data and WHITE_INTERNET_CDN_DOMAIN environment variable"
            )
            headers = dict(common_headers)
            headers["Retry-After"] = "60"
            return web.Response(
                status=503,
                text=texts.WL_WEB_CDN_UNCONFIGURED,
                headers=headers,
            )

        # Determine base path from server extra_data, environment or default
        base_path = (
            extra.get("secret_base_path")
            or os.getenv("WHITE_INTERNET_PATH")
            or DEFAULT_WHITE_INTERNET_PATH
        )

        # Generate multi-relay configs if available
        relays = extra.get("relays")
        if relays is None and os.getenv("WHITE_INTERNET_RELAYS"):
            try:
                relays = json.loads(os.environ["WHITE_INTERNET_RELAYS"])
            except Exception:
                pass

        vless_links = WhiteInternetService.generate_vless_links(
            sub,
            cdn_domain=cdn_domain,
            path=base_path,
            relays=relays,
        )
        payload = "\n".join(vless_links)
        b64_payload = base64.b64encode(payload.encode("utf-8")).decode("utf-8")

        profile_title_b64 = base64.b64encode(texts.WL_PROFILE_NAME.encode("utf-8")).decode("ascii")
        response_headers = dict(common_headers)
        response_headers.update(
            {
                "Content-Type": "text/plain; charset=utf-8",
                "Subscription-Userinfo": texts.WL_USERINFO_HEADER_TEMPLATE.format(
                    upload=upload_bytes,
                    download=download_bytes,
                    total=total_quota,
                    expire=expire_ts,
                ),
                "Profile-Title": f"base64:{profile_title_b64}",
                "Profile-Update-Interval": "6",
                "hide-url": "1",
                "no-limit-enabled": "1",
            }
        )

        return web.Response(status=200, text=b64_payload, headers=response_headers)


PING_HEADERS = {
    "Cache-Control": "no-store,no-cache,must-revalidate",
    "Pragma": "no-cache",
    "Content-Type": "text/plain;charset=utf-8",
}


async def white_internet_ping_handler(_request: web.Request) -> web.Response:
    """Lightweight synthetic healthcheck endpoint for subscription proxy verification."""
    return web.Response(
        status=200,
        text="pong",
        headers=PING_HEADERS,
    )


def setup_white_internet_web_routes(app: web.Application) -> None:
    """Register White Internet HTTP subscription feed routes with support for custom prefixes."""
    sub_prefix = WHITE_INTERNET_SUB_PATH_PREFIX
    app.router.add_get(f"{sub_prefix}/ping", white_internet_ping_handler)
    app.router.add_get(f"{sub_prefix}/{{token}}", white_internet_subscription_feed_handler)
    logger.info("White Internet subscription feed routes registered: %s/{token} and %s/ping", sub_prefix, sub_prefix)
