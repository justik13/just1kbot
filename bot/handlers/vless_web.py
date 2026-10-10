"""HTTP subscription feed endpoint for standard access (/sub/access/{token} and /sub/vless/{token})."""

from __future__ import annotations

import base64
import json
import logging
import os
import re

from aiohttp import web
from sqlalchemy import func, select

from bot import texts
from database.connection import session_scope
from database.models import VPNProfile
from database.repositories import users_repo, vless_subscription_repo
from services.device_service import RESERVING_STATUSES
from services.subscription import SubscriptionService
from services.vless_subscription_service import VlessSubscriptionService
from utils.datetime_helpers import now_utc
from utils.http_rate_limiter import HttpRateLimiter, get_trusted_client_ip

logger = logging.getLogger(__name__)

_ip_rate_limiter = HttpRateLimiter(rate_per_minute=60.0, burst=15)
_token_rate_limiter = HttpRateLimiter(rate_per_minute=30.0, burst=10)

HWID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-:]{3,128}$")


def _extract_device_metadata(request: web.Request) -> dict[str, str]:
    headers = request.headers
    raw_os = headers.get("X-Device-Os") or headers.get("X-OS") or headers.get("x-device-os") or ""
    raw_model = headers.get("X-Device-Model") or headers.get("X-Model") or headers.get("x-device-model") or ""
    ua = headers.get("User-Agent") or ""

    clean_os = re.sub(r"[^\w\s\.\-]", "", raw_os).strip()[:32]
    clean_model = re.sub(r"[^\w\s\.\-]", "", raw_model).strip()[:48]
    clean_ua = re.sub(r"[\r\n]", " ", ua).strip()[:64]

    meta: dict[str, str] = {}
    if clean_os:
        meta["os"] = clean_os
    if clean_model:
        meta["model"] = clean_model
    if clean_ua:
        meta["ua"] = clean_ua
    return meta


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

    common_headers = {
        "Cache-Control": "no-store, private, no-cache, must-revalidate",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
    }

    raw_hwid = (
        request.headers.get("X-Hwid")
        or request.headers.get("X-HWID")
        or request.headers.get("X-Device-Id")
        or request.headers.get("X-Device-ID")
        or ""
    ).strip()
    if raw_hwid and not HWID_PATTERN.match(raw_hwid):
        headers = dict(common_headers)
        headers["x-hwid-required"] = "true"
        return web.Response(status=400, text="Invalid HWID format", headers=headers)

    async with session_scope() as session:
        sub = await vless_subscription_repo.get_subscription_by_token(session, token)
        if sub is None:
            return web.Response(status=404, text="Not Found", headers=common_headers)

        user = await users_repo.get_user_by_id(session, sub.user_id)
        if user is None or getattr(user, "is_deleted", False) is True:
            return web.Response(status=404, text="Not Found", headers=common_headers)

        if getattr(user, "is_banned", False) is True:
            return web.Response(status=403, text="Forbidden", headers=common_headers)

        is_emergency = SubscriptionService.is_vless_emergency_tg(user)
        is_active_access = SubscriptionService.check_vpn_access(user)

        if (
            not sub.is_active
            or getattr(user, "financial_hold", False) is True
            or not (is_active_access or is_emergency)
        ):
            if sub.is_active:
                sub.is_active = False
                sub.version = (getattr(sub, "version", 1) or 1) + 1
                session.add(sub)
                VlessSubscriptionService.ensure_synced_background(user.id, session=session)

            bot_username = os.getenv("BOT_USERNAME", "just1kbot").lstrip("@")
            notice_text = texts.VLESS_FEED_EXPIRED_NOTICE.format(bot_username=bot_username)
            notice_link = (
                f"vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443"
                f"?encryption=none&security=none#{notice_text}"
            )
            b64_payload = base64.b64encode(notice_link.encode("utf-8")).decode("utf-8")
            b64_title = base64.b64encode(texts.VLESS_FEED_EXPIRED_TITLE.encode("utf-8")).decode("utf-8")
            expire_ts = int(user.subscription_end.timestamp()) if getattr(user, "subscription_end", None) else 0
            response_headers = {
                **common_headers,
                "Content-Type": "text/plain; charset=utf-8",
                "Profile-Update-Interval": "1",
                "Subscription-Userinfo": f"upload=0; download=0; total=0; expire={expire_ts}",
                "Profile-Title": f"base64:{b64_title}",
                "Hide-Url": "1",
                "No-Limit-Enabled": "1",
                "Support-Url": f"https://t.me/{bot_username}",
            }
            return web.Response(status=200, text=b64_payload, headers=response_headers)

        # Strict HWID validation for active/emergency subscription requests
        if not raw_hwid:
            headers = dict(common_headers)
            headers["x-hwid-required"] = "true"
            return web.Response(status=403, text="HWID required", headers=headers)

        # Atomic HWID registration and state refresh under row locks (populate_existing=True)
        device_meta = _extract_device_metadata(request)
        allowed_hwid, active_hwid_count, effective_vless_limit = (
            await vless_subscription_repo.register_hwid_atomic(
                session, sub.id, raw_hwid, device_info=device_meta
            )
        )

        awg_res = (
            await session.execute(
                select(func.count(VPNProfile.id)).where(
                    VPNProfile.user_id == user.id,
                    VPNProfile.provisioning_status.in_(RESERVING_STATUSES),
                )
            )
        ).scalar_one()
        awg_count = awg_res if isinstance(awg_res, int) else 0

        effective_limit = await SubscriptionService.get_effective_device_limit(session, user)
        effective_limit_int = effective_limit if isinstance(effective_limit, int) else 5
        total_active_devices = awg_count + active_hwid_count

        if not allowed_hwid:
            bot_username = os.getenv("BOT_USERNAME", "just1kbot").lstrip("@")
            limit_msg = texts.VLESS_FEED_DEVICE_LIMIT_NOTICE.format(
                active=total_active_devices,
                limit=effective_limit_int,
                bot_username=bot_username,
            )
            notice_link = (
                f"vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443"
                f"?encryption=none&security=none#{limit_msg}"
            )
            b64_payload = base64.b64encode(notice_link.encode("utf-8")).decode("utf-8")
            b64_title = base64.b64encode(texts.VLESS_FEED_DEVICE_LIMIT_TITLE.encode("utf-8")).decode("utf-8")
            expire_ts = int(user.subscription_end.timestamp()) if getattr(user, "subscription_end", None) else 0
            headers = dict(common_headers)
            headers.update({
                "Content-Type": "text/plain; charset=utf-8",
                "Profile-Update-Interval": "1",
                "Subscription-Userinfo": f"upload=0; download=0; total=0; expire={expire_ts}",
                "Profile-Title": f"base64:{b64_title}",
                "Device-Limit-Exceeded": "1",
                "Device-Limit": str(effective_limit_int),
                "Device-Active-Count": str(total_active_devices),
                "x-hwid-max-devices-reached": "true",
                "x-hwid-limit": str(effective_limit_int),
                "x-hwid-active": str(total_active_devices),
                "Hide-Url": "1",
                "No-Limit-Enabled": "1",
                "Support-Url": f"https://t.me/{bot_username}",
            })
            return web.Response(status=200, text=b64_payload, headers=headers)

        # Generate fresh server links strictly AFTER atomic lock & verification
        servers = await VlessSubscriptionService.get_eligible_vless_servers(session)
        links = VlessSubscriptionService.generate_vless_links(sub, servers)
        if not links:
            retry_headers = {**common_headers, "Retry-After": "60"}
            return web.Response(status=503, text="No servers available", headers=retry_headers)

        # Trigger background sync with debouncing (300s) unless version is out of sync or pending revocations exist
        now = now_utc()
        last_synced_version = getattr(sub, "last_synced_version", None)
        current_version = getattr(sub, "version", 1) or 1
        needs_sync = (
            sub.last_synced_at is None
            or (now - sub.last_synced_at).total_seconds() > 300
            or bool(getattr(sub, "pending_revoked_uuids", None))
            or (last_synced_version is not None and last_synced_version < current_version)
        )
        if needs_sync:
            VlessSubscriptionService.ensure_synced_background(user.id, session=session)

        payload = "\n".join(links)
        b64_payload = base64.b64encode(payload.encode("utf-8")).decode("utf-8")

        expire_ts = int(user.subscription_end.timestamp()) if user.subscription_end else 0
        bot_username = os.getenv("BOT_USERNAME", "just1kbot").lstrip("@")

        if is_emergency:
            title_text = texts.VLESS_FEED_EMERGENCY_TG_TITLE
            b64_title = base64.b64encode(title_text.encode("utf-8")).decode("utf-8")
            announce_text = texts.VLESS_FEED_EMERGENCY_TG_ANNOUNCE.format(bot_username=bot_username)
            b64_announce = base64.b64encode(announce_text.encode("utf-8")).decode("utf-8")
            routing_spec = json.dumps({
                "version": 1,
                "rules": [
                    {
                        "domains": ["geosite:telegram", "t.me", "telegram.org", "telesco.pe"],
                        "ips": ["geoip:telegram"],
                        "outbound": "proxy",
                    },
                    {
                        "domains": ["*"],
                        "ips": ["0.0.0.0/0", "::/0"],
                        "outbound": "direct",
                    },
                ],
            })
            b64_routing = base64.b64encode(routing_spec.encode("utf-8")).decode("utf-8")
            response_headers = {
                **common_headers,
                "Content-Type": "text/plain; charset=utf-8",
                "Profile-Update-Interval": "1",
                "Subscription-Userinfo": f"upload=0; download=0; total=0; expire={expire_ts}",
                "Device-Limit": str(effective_limit),
                "Device-Active-Count": str(total_active_devices),
                "Profile-Title": f"base64:{b64_title}",
                "Announce": f"base64:{b64_announce}",
                "Announce-Url": f"https://t.me/{bot_username}",
                "Routing": f"base64:{b64_routing}",
                "Hide-Url": "1",
                "No-Limit-Enabled": "1",
                "Support-Url": f"https://t.me/{bot_username}",
            }
        else:
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
                "Hide-Url": "1",
                "No-Limit-Enabled": "1",
                "Support-Url": f"https://t.me/{bot_username}",
            }
        return web.Response(status=200, text=b64_payload, headers=response_headers)


def setup_vless_web_routes(app: web.Application) -> None:
    """Register HTTP subscription feed route for VLESS access."""
    app.router.add_get("/sub/access/{token}", vless_subscription_feed_handler)
    app.router.add_get("/sub/vless/{token}", vless_subscription_feed_handler)
