"""HTTP subscription feed endpoint for standard VLESS access (/sub/vless/{token})."""

from __future__ import annotations

import base64
import logging
import os

from aiohttp import web
from sqlalchemy import func, select

from bot import texts
from database.connection import session_scope
from database.models import VPNProfile
from database.repositories import users_repo, vless_subscription_repo
from database.repositories.profiles_repo import RESERVING_STATUSES
from services.subscription import SubscriptionService
from services.vless_subscription_service import VlessSubscriptionService
from utils.datetime_helpers import now_utc
from utils.http_rate_limiter import HttpRateLimiter, get_trusted_client_ip

logger = logging.getLogger(__name__)

_ip_rate_limiter = HttpRateLimiter(rate_per_minute=60.0, burst=15)
_token_rate_limiter = HttpRateLimiter(rate_per_minute=30.0, burst=10)


async def vless_subscription_feed_handler(request: web.Request) -> web.Response:
    """Serve Base64 subscription feed for standard VLESS access with HWID device accounting."""
    client_ip = get_trusted_client_ip(request)
    allowed_ip, retry_after_ip = _ip_rate_limiter.check(client_ip)
    if not allowed_ip:
        return web.Response(
            status=429,
            text=texts.WL_WEB_TOO_MANY_REQUESTS,
            headers={
                "Retry-After": str(retry_after_ip),
                "Cache-Control": "no-store",
            },
        )

    token = request.match_info.get("token", "").strip()
    if not token or len(token) < 16:
        return web.Response(
            status=404,
            text="Not Found",
            headers={"Cache-Control": "no-store"},
        )

    allowed_tok, retry_after_tok = _token_rate_limiter.check(token)
    if not allowed_tok:
        return web.Response(
            status=429,
            text=texts.WL_WEB_TOO_MANY_REQUESTS,
            headers={
                "Retry-After": str(retry_after_tok),
                "Cache-Control": "no-store",
            },
        )

    now = now_utc()
    common_headers = {
        "Cache-Control": "no-store, private, no-cache, must-revalidate",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
    }

    async with session_scope() as session:
        sub = await vless_subscription_repo.get_subscription_by_token(session, token)
        if sub is None or not sub.is_active:
            return web.Response(status=404, text="Not Found", headers=common_headers)

        user = await users_repo.get_user_by_id(session, sub.user_id)
        if (
            user is None
            or getattr(user, "is_banned", False) is True
            or getattr(user, "is_deleted", False) is True
        ):
            return web.Response(status=403, text="Forbidden", headers=common_headers)

        if not user.subscription_end or user.subscription_end <= now:
            return web.Response(status=403, text=texts.WL_WEB_EXPIRED, headers=common_headers)

        # Strict HWID enforcement
        hwid = (
            request.headers.get("X-Hwid")
            or request.headers.get("X-HWID")
            or request.headers.get("X-Device-Id")
            or request.headers.get("X-Device-ID")
            or ""
        ).strip()
        if not hwid:
            headers = dict(common_headers)
            headers["x-hwid-required"] = "true"
            return web.Response(status=403, text="HWID required", headers=headers)

        # Count active AWG profiles
        awg_count = (
            await session.execute(
                select(func.count(VPNProfile.id)).where(
                    VPNProfile.user_id == user.id,
                    VPNProfile.provisioning_status.in_(RESERVING_STATUSES),
                )
            )
        ).scalar_one()

        effective_limit = await SubscriptionService.get_effective_device_limit(session, user)
        effective_vless_limit = max(0, effective_limit - awg_count)

        allowed_hwid, active_hwid_count, _ = await vless_subscription_repo.register_hwid_atomic(
            session, sub.id, hwid, effective_limit=effective_vless_limit
        )

        total_active_devices = awg_count + active_hwid_count

        if not allowed_hwid:
            headers = dict(common_headers)
            headers["Device-Limit-Exceeded"] = "1"
            headers["Device-Limit"] = str(effective_limit)
            headers["Device-Active-Count"] = str(total_active_devices)
            headers["x-hwid-max-devices-reached"] = "true"
            headers["x-hwid-limit"] = str(effective_limit)
            headers["x-hwid-active"] = str(total_active_devices)
            bot_username = os.getenv("BOT_USERNAME", "just1kbot").lstrip("@")
            limit_msg = texts.WL_WEB_DEVICE_LIMIT_EXCEEDED.format(
                active=total_active_devices,
                limit=effective_limit,
                bot_username=bot_username,
            )
            return web.Response(status=403, text=limit_msg, headers=headers)

        # Trigger background sync to dual nodes
        VlessSubscriptionService.ensure_synced_background(user.id, is_active=True)

        servers = await VlessSubscriptionService.get_eligible_vless_servers(session)
        links = VlessSubscriptionService.generate_vless_links(sub, servers)

        payload = "\n".join(links)
        b64_payload = base64.b64encode(payload.encode("utf-8")).decode("utf-8")

        expire_ts = int(user.subscription_end.timestamp()) if user.subscription_end else 0
        profile_title = os.getenv("VLESS_PROFILE_TITLE", "Just1k Access")
        b64_title = base64.b64encode(profile_title.encode("utf-8")).decode("utf-8")

        response_headers = {
            **common_headers,
            "Content-Type": "text/plain; charset=utf-8",
            "Profile-Update-Interval": "1",
            "Subscription-Userinfo": f"upload=0; download=0; total=0; expire={expire_ts}",
            "Device-Limit": str(effective_limit),
            "Device-Active-Count": str(total_active_devices),
            "Profile-Title": f"base64:{b64_title}",
        }
        return web.Response(status=200, text=b64_payload, headers=response_headers)


def setup_vless_web_routes(app: web.Application) -> None:
    """Register HTTP subscription feed route for VLESS access."""
    app.router.add_get("/sub/vless/{token}", vless_subscription_feed_handler)
