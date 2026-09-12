"""HTTP subscription feed endpoint for AmneziaWG (/sub/awg/{token})."""

from __future__ import annotations

import base64
import hashlib
import logging
import os

from aiohttp import web
from sqlalchemy import func, select

from bot import texts
from config.constants import AMNEZIA_PROTOCOL
from config.enums import ServerHealthState
from database.connection import session_scope
from database.models import Server, VPNProfile
from database.repositories import users_repo
from services.awg_subscription_feed_service import AWGSubscriptionFeedService
from services.device_service import DeviceService, RESERVING_STATUSES
from services.slots_cache import capture_server_peer_snapshot
from utils.datetime_helpers import is_expired, now_utc
from utils.http_rate_limiter import HttpRateLimiter, get_trusted_client_ip
from utils.vpn_parser import build_conf_file

logger = logging.getLogger(__name__)

# Scoped rate limiters: IP bucket prevents unauthenticated DoS / token brute force,
# token bucket prevents single subscription thrashing across rotating IPs.
_ip_rate_limiter = HttpRateLimiter(rate_per_minute=60.0, burst=15)
_token_rate_limiter = HttpRateLimiter(rate_per_minute=30.0, burst=10)

DEFAULT_AWG_SUB_PATH_PREFIX = "/sub/awg"


async def awg_subscription_feed_handler(request: web.Request) -> web.Response:
    """Serve the no-store Base64 AmneziaWG multi-server subscription feed for INCY."""
    client_ip = get_trusted_client_ip(request)
    allowed_ip, retry_after_ip = _ip_rate_limiter.check(client_ip)
    if not allowed_ip:
        return web.Response(
            status=429,
            text=texts.AWG_WEB_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry_after_ip), "Cache-Control": "no-store"},
        )

    token = request.match_info.get("token", "").strip()
    if not token or len(token) < 16:
        return web.Response(status=404, text="Not Found", headers={"Cache-Control": "no-store"})

    allowed_tok, retry_after_tok = _token_rate_limiter.check(token)
    if not allowed_tok:
        return web.Response(
            status=429,
            text=texts.AWG_WEB_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry_after_tok), "Cache-Control": "no-store"},
        )

    common_headers = {
        "Cache-Control": "no-store, private, no-cache, must-revalidate",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
    }

    # HWID (Device ID) strict enforcement
    raw_hwid = (
        request.headers.get("X-Hwid")
        or request.headers.get("X-HWID")
        or request.headers.get("X-Device-Id")
        or request.headers.get("X-Device-ID")
        or ""
    ).strip()
    if not raw_hwid:
        headers = dict(common_headers)
        headers["x-hwid-required"] = "true"
        return web.Response(status=403, text=texts.AWG_WEB_HWID_REQUIRED, headers=headers)

    hwid_hash = hashlib.sha256(raw_hwid.lower().encode("utf-8")).hexdigest()
    now = now_utc()

    async with session_scope() as session:
        user = await users_repo.get_user_by_subscription_token(session, token, for_update=True)
        if user is None:
            return web.Response(status=404, text="Not Found", headers=common_headers)

        if (
            getattr(user, "is_banned", False) is True
            or getattr(user, "is_deleted", False) is True
        ):
            return web.Response(status=403, text="Forbidden", headers=common_headers)

        if not user.subscription_end or is_expired(user.subscription_end):
            return web.Response(status=403, text=texts.AWG_WEB_EXPIRED, headers=common_headers)

        # Quota check: Distinct logical sub devices + manual configurations
        active_sub_devices = dict(user.active_sub_devices or {})
        is_existing = hwid_hash in active_sub_devices

        if not is_existing:
            manual_count = (await session.execute(
                select(func.count(VPNProfile.id)).where(
                    VPNProfile.user_id == user.id,
                    VPNProfile.device_type == "manual",
                    VPNProfile.provisioning_status.in_(RESERVING_STATUSES),
                )
            )).scalar_one()

            total_active = manual_count + len(active_sub_devices)
            limit = user.device_limit or 2

            if total_active >= limit:
                headers = dict(common_headers)
                headers["Device-Limit-Exceeded"] = "1"
                headers["Device-Limit"] = str(limit)
                headers["Device-Active-Count"] = str(total_active)
                headers["x-hwid-max-devices-reached"] = "true"
                headers["x-hwid-limit"] = str(limit)
                headers["x-hwid-active"] = str(total_active)
                limit_msg = texts.AWG_WEB_DEVICE_LIMIT_EXCEEDED.format(
                    total_active=total_active,
                    limit=limit,
                )
                return web.Response(
                    status=403,
                    text=limit_msg,
                    headers=headers,
                )

            device_idx = len(active_sub_devices) + 1
            active_sub_devices[hwid_hash] = {
                "device_index": device_idx,
                "label": texts.AWG_SUB_DEVICE_LABEL_TEMPLATE.format(index=device_idx),
                "first_seen": now.isoformat(),
                "last_seen": now.isoformat(),
            }
            user.active_sub_devices = active_sub_devices
            await session.flush()
        else:
            active_sub_devices[hwid_hash]["last_seen"] = now.isoformat()
            user.active_sub_devices = active_sub_devices
            await session.flush()

        # Find active AWG servers
        servers_stmt = select(Server).where(
            Server.is_active.is_(True),
            Server.protocol == AMNEZIA_PROTOCOL,
            Server.health_state == ServerHealthState.ONLINE,
        )
        servers = (await session.execute(servers_stmt)).scalars().all()
        awg_servers = [s for s in servers if "xray_origin" not in (s.capabilities or [])]

        if not awg_servers:
            headers = dict(common_headers)
            headers["Retry-After"] = "10"
            return web.Response(status=503, text=texts.AWG_WEB_NO_SERVERS, headers=headers)

        # Check existing profiles for this sub device
        existing_profiles_stmt = select(VPNProfile).where(
            VPNProfile.user_id == user.id,
            VPNProfile.device_type == "sub",
            VPNProfile.sub_device_hash == hwid_hash,
            VPNProfile.provisioning_status.in_(RESERVING_STATUSES),
        )
        existing_profiles = (await session.execute(existing_profiles_stmt)).scalars().all()
        profiles_by_server = {p.server_id: p for p in existing_profiles}

        newly_created = False
        for srv in awg_servers:
            if srv.id not in profiles_by_server:
                try:
                    snapshot = await capture_server_peer_snapshot(srv.id)
                    dev_name = texts.AWG_SUB_PROFILE_NAME_TEMPLATE.format(server_name=srv.name or "AWG")
                    profile = await DeviceService.create_device(
                        session,
                        user_id=user.id,
                        server_id=srv.id,
                        device_name=dev_name,
                        snapshot=snapshot,
                        device_type="sub",
                        sub_device_hash=hwid_hash,
                    )
                    profiles_by_server[srv.id] = profile
                    newly_created = True
                except Exception as exc:
                    logger.warning("Failed to create sub profile for server %s: %s", srv.id, exc)

        if newly_created:
            await session.commit()

        # Check if any profile is still provisioning
        has_pending = any(
            p.provisioning_status in ("pending_create", "pending_update") or not p.raw_config
            for p in profiles_by_server.values()
        )
        if has_pending:
            title_b64 = base64.b64encode(texts.AWG_PROFILE_NAME.encode("utf-8")).decode("ascii")
            headers = dict(common_headers)
            headers["Retry-After"] = "3"
            headers["Profile-Title"] = f"base64:{title_b64}"
            return web.Response(status=503, text=texts.AWG_WEB_PREPARING, headers=headers)

        server_configs = []
        for srv in awg_servers:
            p = profiles_by_server.get(srv.id)
            if not p or not p.raw_config or p.provisioning_status != "active":
                continue
            conf = build_conf_file(p.raw_config)
            if conf:
                server_configs.append((
                    conf,
                    srv.name or "Server",
                    srv.country_flag or "",
                    getattr(srv, "ping", None),
                ))

        if not server_configs:
            headers = dict(common_headers)
            headers["Retry-After"] = "5"
            return web.Response(status=503, text=texts.AWG_WEB_NO_CONFIGS, headers=headers)

        sorted_configs = AWGSubscriptionFeedService.sort_servers(server_configs, mode="ping")
        body_b64 = AWGSubscriptionFeedService.build_subscription_body(sorted_configs)

        expire_ts = int(user.subscription_end.timestamp()) if user.subscription_end else 0
        feed_headers = AWGSubscriptionFeedService.build_subscription_headers(
            profile_title=texts.AWG_PROFILE_NAME,
            expire_ts=expire_ts,
            sort_order="ping",
            update_interval_hours=6,
            hide_url=True,
        )
        feed_headers.update(common_headers)
        return web.Response(status=200, text=body_b64, headers=feed_headers)


PING_HEADERS = {
    "Cache-Control": "no-store,no-cache,must-revalidate",
    "Pragma": "no-cache",
    "Content-Type": "text/plain;charset=utf-8",
}


async def awg_ping_handler(_request: web.Request) -> web.Response:
    """Lightweight synthetic healthcheck endpoint for AWG subscription proxy verification."""
    return web.Response(status=200, text="pong", headers=PING_HEADERS)


def setup_awg_subscription_web_routes(app: web.Application) -> None:
    """Register AmneziaWG HTTP subscription feed routes."""
    sub_prefix = (
        os.getenv("AWG_SUB_PATH_PREFIX") or DEFAULT_AWG_SUB_PATH_PREFIX
    ).strip().rstrip("/")
    if not sub_prefix.startswith("/"):
        sub_prefix = f"/{sub_prefix}"

    app.router.add_get(f"{sub_prefix}/ping", awg_ping_handler)
    app.router.add_get(f"{sub_prefix}/{{token}}", awg_subscription_feed_handler)
    if sub_prefix != DEFAULT_AWG_SUB_PATH_PREFIX:
        app.router.add_get(f"{DEFAULT_AWG_SUB_PATH_PREFIX}/ping", awg_ping_handler)
        app.router.add_get(f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{{token}}", awg_subscription_feed_handler)
    logger.info("AWG subscription feed routes registered: %s/{token} and %s/ping", sub_prefix, sub_prefix)
