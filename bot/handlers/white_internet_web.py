"""HTTP subscription feed endpoint for White Internet (/sub/wl/{token})."""

from __future__ import annotations

import base64
import html
import json
import logging
import os
from pathlib import Path
import re
import urllib.parse

from aiohttp import web

from sqlalchemy import select

from bot import texts
from config import constants
from config.constants import (
    DEFAULT_WHITE_INTERNET_PATH,
    DEFAULT_WHITE_INTERNET_SUB_PATH_PREFIX,
    WHITE_INTERNET_BASE_TRAFFIC_BYTES,
    WHITE_INTERNET_CHANNEL_URL,
    WHITE_INTERNET_PROFILE_TITLE,
    WHITE_INTERNET_SUB_PATH_PREFIX,
    WHITE_INTERNET_SUPPORT_URL,
    XRAY_PROTOCOL,
)
from config.enums import ServerHealthState, WhiteInternetProvisioningStatus, WhiteInternetStatus
from database.connection import session_scope
from database.models import Server
from database.repositories import users_repo, white_internet_repo
from database.repositories.system_settings_repo import get_system_setting
from services.white_internet_service import WhiteInternetService
from utils.client_parser import extract_device_metadata, parse_client_from_user_agent
from utils.datetime_helpers import now_utc
from utils.http_rate_limiter import HttpRateLimiter, get_trusted_client_ip
from utils.incy_routing import generate_incy_routing_header
from utils.security import normalize_public_domain

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_LANDING_ACTIVE_HTML = (_TEMPLATES_DIR / "vless_landing_active.html").read_text(encoding="utf-8")
_LANDING_INACTIVE_HTML = (_TEMPLATES_DIR / "vless_landing_inactive.html").read_text(encoding="utf-8")
_LANDING_PENDING_HTML = (_TEMPLATES_DIR / "vless_landing_pending.html").read_text(encoding="utf-8")

# Scoped rate limiters: IP bucket prevents unauthenticated DoS / token brute force,
# token bucket prevents single subscription thrashing across rotating IPs.
_ip_rate_limiter = HttpRateLimiter(rate_per_minute=60.0, burst=15)
_token_rate_limiter = HttpRateLimiter(rate_per_minute=30.0, burst=10)

HWID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-:]{3,128}$")


_cached_bot_username: str | None = None


def _sanitize_header_url(url: str | None, fallback: str = "") -> str:
    """Sanitize URL for safe usage in HTTP headers without forbidden control chars, whitespace, or non-ASCII."""
    for candidate in (url, fallback):
        if candidate and isinstance(candidate, str):
            clean = candidate.strip()
            if clean and clean.isascii() and all(32 < ord(c) < 127 for c in clean):
                return clean
    return ""


def _safe_format_device_template(
    template: str,
    *,
    devices: int,
    active: int,
) -> str:
    """Safely format {devices}, {device_limit}, {limit}, {active}, {active_devices} placeholders."""
    mapping = {
        "devices": str(devices),
        "device_limit": str(devices),
        "limit": str(devices),
        "active": str(active),
        "active_devices": str(active),
    }
    pattern = re.compile(r"\{(" + "|".join(re.escape(k) for k in mapping.keys()) + r")\}")
    return pattern.sub(lambda m: mapping.get(m.group(1), m.group(0)), template)


def _get_white_internet_update_interval() -> str:
    val = os.getenv("WHITE_INTERNET_PROFILE_UPDATE_INTERVAL", "6").strip()
    return val if val.isdigit() and int(val) > 0 else "6"


_extract_device_metadata = extract_device_metadata


def _is_browser_request(request: web.Request) -> bool:
    """Check if incoming request is from a browser navigating to the page."""
    raw_hwid = (
        request.headers.get("X-Hwid")
        or request.headers.get("X-Device-Hwid")
        or request.headers.get("X-Device-Id")
        or ""
    ).strip().strip("{}")
    if raw_hwid:
        return False

    ua = request.headers.get("User-Agent", "").lower()
    api_tokens = (
        "curl/",
        "wget/",
        "python-requests",
        "urllib",
        "httpx",
        "xray/",
        "v2ray",
        "sing-box",
        "clash",
        "incy",
        "happ",
    )
    if any(a in ua for a in api_tokens):
        return False

    accept = request.headers.get("Accept", "").lower()
    if "text/html" in accept:
        return True

    browser_tokens = ("mozilla/", "chrome/", "safari/", "applewebkit/", "mobile/", "android", "iphone")
    return any(b in ua for b in browser_tokens)


def _render_white_internet_landing_html(
    bot_username: str = "",
    is_active: bool = True,
    status_badge: str | None = None,
    status_title: str | None = None,
    status_description: str | None = None,
    page_title: str | None = None,
    tier: str = "white_internet",
    tariff_badge: str | None = None,
) -> str:
    """Render universal landing page for INCY 1-click import."""
    clean_bot = (
        html.escape(bot_username.strip().lstrip("@"))
        if bot_username
        else constants.DEFAULT_FALLBACK_BOT_USERNAME
    )
    bot_link = f"https://t.me/{clean_bot}"

    if not is_active:
        badge = status_badge or texts.LANDING_INACTIVE_BADGE_PAUSED
        title = status_title or texts.LANDING_INACTIVE_TITLE_DEFAULT
        desc = status_description or texts.LANDING_INACTIVE_DESC_DEFAULT
        return (
            _LANDING_INACTIVE_HTML.replace("{clean_bot}", clean_bot)
            .replace("{bot_link}", bot_link)
            .replace("{status_badge}", html.escape(badge))
            .replace("{status_title}", html.escape(title))
            .replace("{status_description}", html.escape(desc))
        )
    effective_badge = tariff_badge or texts.LANDING_SPECIAL_ACCESS_BADGE
    effective_title = page_title or texts.LANDING_WHITE_INTERNET_PAGE_TITLE
    badge_html = f'<div class="tier-badge">{html.escape(effective_badge)}</div>' if effective_badge else ""
    return (
        _LANDING_ACTIVE_HTML.replace("{page_title}", html.escape(effective_title))
        .replace("{tier}", html.escape(tier))
        .replace("{tariff_badge_html}", badge_html)
    )


def _render_white_internet_pending_landing_html(bot_username: str = "") -> str:
    """Render landing page for PENDING state with auto-refresh."""
    clean_bot = (
        html.escape(bot_username.strip().lstrip("@"))
        if bot_username
        else constants.DEFAULT_FALLBACK_BOT_USERNAME
    )
    bot_link = f"https://t.me/{clean_bot}"
    return _LANDING_PENDING_HTML.replace("{clean_bot}", clean_bot).replace("{bot_link}", bot_link)


async def _resolve_bot_username(request: web.Request) -> str:
    """Resolve current Telegram bot username dynamically without manual env setup."""
    global _cached_bot_username
    cached = request.app.get("bot_username") if hasattr(request, "app") else None
    if cached and isinstance(cached, str):
        return cached
    if _cached_bot_username:
        return _cached_bot_username

    bot = request.app.get("bot") if hasattr(request, "app") else None
    if bot is not None and hasattr(bot, "get_me"):
        try:
            me = await bot.get_me()
            if me and getattr(me, "username", None):
                username = str(me.username).lstrip("@")
                _cached_bot_username = username
                if hasattr(request, "app") and not getattr(request.app, "frozen", False):
                    try:
                        request.app["bot_username"] = username
                    except Exception:
                        pass
                return username
        except Exception:
            pass

    return os.getenv("BOT_USERNAME", "").lstrip("@") or constants.DEFAULT_FALLBACK_BOT_USERNAME


async def _resolve_urls_and_bot(request: web.Request, extra: dict | None) -> tuple[str, str, str, str]:
    extra_dict = extra if isinstance(extra, dict) else {}
    bot_user = await _resolve_bot_username(request)
    default_bot_url = f"https://t.me/{bot_user}"
    try:
        from config.settings import get_settings
        support_user = (
            getattr(get_settings(), "SUPPORT_USERNAME", None)
            or os.getenv("SUPPORT_BOT_USERNAME")
            or ""
        ).lstrip("@")
    except Exception:
        support_user = (os.getenv("SUPPORT_USERNAME") or os.getenv("SUPPORT_BOT_USERNAME") or "").lstrip("@")
    default_support_url = f"https://t.me/{support_user}" if support_user else default_bot_url

    raw_channel_url = (
        extra_dict.get("channel_url")
        or WHITE_INTERNET_CHANNEL_URL
        or default_bot_url
    )
    raw_support_url = (
        extra_dict.get("support_url")
        or WHITE_INTERNET_SUPPORT_URL
        or default_support_url
    )
    raw_announce_url = extra_dict.get("announce_url")

    channel_url = _sanitize_header_url(raw_channel_url, fallback=default_bot_url)
    support_url = _sanitize_header_url(raw_support_url, fallback=channel_url)
    effective_announce_url = _sanitize_header_url(raw_announce_url, fallback=channel_url)

    return channel_url, support_url, effective_announce_url, bot_user


def _build_service_notice_response(
    *,
    title_text: str,
    notice_text: str,
    common_headers: dict[str, str],
    channel_url: str,
    support_url: str,
    effective_announce_url: str,
    upload_bytes: int = 0,
    download_bytes: int = 0,
    total_quota: int = 0,
    expire_ts: int = 0,
    extra_headers: dict[str, str] | None = None,
) -> web.Response:
    quoted_notice = urllib.parse.quote(notice_text)
    notice_link = (
        f"vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443"
        f"?encryption=none&security=none#{quoted_notice}"
    )
    b64_payload = base64.b64encode(notice_link.encode("utf-8")).decode("utf-8")
    b64_title = base64.b64encode(title_text.encode("utf-8")).decode("utf-8")

    sanitized_support = _sanitize_header_url(support_url)
    sanitized_channel = _sanitize_header_url(channel_url)
    sanitized_announce = _sanitize_header_url(effective_announce_url, fallback=sanitized_channel)

    headers = dict(common_headers)
    headers.update(
        {
            "Content-Type": "text/plain; charset=utf-8",
            "Profile-Update-Interval": _get_white_internet_update_interval(),
            "Subscription-Userinfo": texts.WL_USERINFO_HEADER_TEMPLATE.format(
                upload=upload_bytes,
                download=download_bytes,
                total=total_quota,
                expire=expire_ts,
            ),
            "Profile-Title": f"base64:{b64_title}",
            "Hide-Url": "1",
            "No-Limit-Enabled": "1",
        }
    )
    if sanitized_support:
        headers["Support-Url"] = sanitized_support
    if sanitized_announce:
        headers["Announce-Url"] = sanitized_announce
    if sanitized_channel:
        headers["Profile-Web-Page-Url"] = sanitized_channel
        headers["Channel-Url"] = sanitized_channel
    if extra_headers:
        headers.update(extra_headers)
    return web.Response(status=200, text=b64_payload, headers=headers)


async def white_internet_subscription_feed_handler(request: web.Request) -> web.Response:
    """Serve the no-store Base64 subscription feed only after runtime reconciliation."""
    client_ip = get_trusted_client_ip(request)
    allowed_ip, retry_after_ip = _ip_rate_limiter.check(client_ip)
    if not allowed_ip:
        return web.Response(
            status=429,
            text=texts.WL_WEB_TOO_MANY_REQUESTS,
            headers={
                "Retry-After": str(retry_after_ip),
                "Cache-Control": "no-store",
                "CDN-Cache-Control": "no-store",
            },
        )

    token = request.match_info.get("token", "").strip()
    if not token or len(token) < 16:
        return web.Response(
            status=404,
            text="Not Found",
            headers={"Cache-Control": "no-store", "CDN-Cache-Control": "no-store"},
        )

    allowed_tok, retry_after_tok = _token_rate_limiter.check(token)
    if not allowed_tok:
        return web.Response(
            status=429,
            text=texts.WL_WEB_TOO_MANY_REQUESTS,
            headers={
                "Retry-After": str(retry_after_tok),
                "Cache-Control": "no-store",
                "CDN-Cache-Control": "no-store",
            },
        )

    now = now_utc()
    common_headers = {
        "Cache-Control": "no-store, private, no-cache, must-revalidate",
        "CDN-Cache-Control": "no-store",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
    }

    raw_hwid = (
        request.headers.get("X-Hwid")
        or request.headers.get("X-Device-Hwid")
        or request.headers.get("X-Device-Id")
        or ""
    ).strip().strip("{}")
    if raw_hwid and not HWID_PATTERN.match(raw_hwid):
        headers = dict(common_headers)
        headers["x-hwid-required"] = "true"
        return web.Response(status=400, text="Invalid HWID format", headers=headers)

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

        server: Server | None = None
        if getattr(sub, "origin_node_id", None):
            server = await session.scalar(select(Server).where(Server.id == sub.origin_node_id))
        extra = server.extra_data if server and isinstance(server.extra_data, dict) else {}
        channel_url, support_url, effective_announce_url, bot_user = await _resolve_urls_and_bot(request, extra)

        landing_headers = {
            **common_headers,
            "Content-Type": "text/html; charset=utf-8",
            "Content-Security-Policy": "default-src 'self' 'unsafe-inline' https:; img-src 'self' data: https:;",
        }

        if sub.status == WhiteInternetStatus.PENDING:
            if not raw_hwid and _is_browser_request(request):
                html_body = _render_white_internet_pending_landing_html(bot_username=bot_user)
                return web.Response(status=200, text=html_body, headers=landing_headers)
            headers = dict(common_headers)
            headers["Retry-After"] = "5"
            return web.Response(status=503, text=texts.WL_WEB_PENDING, headers=headers)

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

        used_val = max(
            0,
            (
                int(sub.traffic_used_bytes)
                if isinstance(getattr(sub, "traffic_used_bytes", None), (int, float))
                else 0
            )
            - (
                int(sub.traffic_overage_bytes)
                if isinstance(getattr(sub, "traffic_overage_bytes", None), (int, float))
                else 0
            ),
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

        user_agent = request.headers.get("User-Agent", "")
        client_name = parse_client_from_user_agent(user_agent)
        is_happ = bool((client_name and "happ" in client_name.lower()) or ("happ" in user_agent.lower()))
        clean_bot = str(bot_user).strip().lstrip("@") or constants.DEFAULT_FALLBACK_BOT_USERNAME
        notice_extra_headers: dict[str, str] = {}
        if is_happ:
            notice_extra_headers = {
                "sub-expire-button-link": f"https://t.me/{clean_bot}",
            }
            if expire_ts > 0:
                notice_extra_headers["sub-expire"] = "1"

        if sub.status == WhiteInternetStatus.DISABLED:
            if not raw_hwid and _is_browser_request(request):
                html_body = _render_white_internet_landing_html(
                    bot_username=bot_user,
                    is_active=False,
                    status_badge=texts.LANDING_INACTIVE_BADGE_DISABLED,
                    status_title=texts.LANDING_INACTIVE_TITLE_DISABLED,
                    status_description=texts.LANDING_INACTIVE_DESC_DISABLED,
                )
                return web.Response(status=200, text=html_body, headers=landing_headers)
            return _build_service_notice_response(
                title_text=texts.WL_FEED_DISABLED_TITLE,
                notice_text=texts.WL_FEED_DISABLED_NOTICE.format(bot_username=bot_user),
                common_headers=common_headers,
                channel_url=channel_url,
                support_url=support_url,
                effective_announce_url=effective_announce_url,
                upload_bytes=upload_bytes,
                download_bytes=download_bytes,
                total_quota=total_quota,
                expire_ts=expire_ts,
                extra_headers=notice_extra_headers,
            )

        if (
            sub.status == WhiteInternetStatus.EXPIRED
            or (sub.expires_at and sub.expires_at <= now)
        ):
            if not raw_hwid and _is_browser_request(request):
                html_body = _render_white_internet_landing_html(
                    bot_username=bot_user,
                    is_active=False,
                    status_badge=texts.LANDING_INACTIVE_BADGE_EXPIRED,
                    status_title=texts.LANDING_INACTIVE_TITLE_EXPIRED,
                    status_description=texts.LANDING_INACTIVE_DESC_EXPIRED,
                )
                return web.Response(status=200, text=html_body, headers=landing_headers)
            return _build_service_notice_response(
                title_text=texts.VLESS_FEED_EXPIRED_TITLE,
                notice_text=texts.VLESS_FEED_EXPIRED_NOTICE.format(bot_username=bot_user),
                common_headers=common_headers,
                channel_url=channel_url,
                support_url=support_url,
                effective_announce_url=effective_announce_url,
                upload_bytes=upload_bytes,
                download_bytes=download_bytes,
                total_quota=total_quota,
                expire_ts=expire_ts,
                extra_headers=notice_extra_headers,
            )

        if sub.status == WhiteInternetStatus.EXHAUSTED or available_bytes <= 0:
            if not raw_hwid and _is_browser_request(request):
                html_body = _render_white_internet_landing_html(
                    bot_username=bot_user,
                    is_active=False,
                    status_badge=texts.LANDING_INACTIVE_BADGE_EXHAUSTED,
                    status_title=texts.LANDING_INACTIVE_TITLE_EXHAUSTED,
                    status_description=texts.LANDING_INACTIVE_DESC_EXHAUSTED,
                )
                return web.Response(status=200, text=html_body, headers=landing_headers)
            is_trial = bool(getattr(sub, "is_trial", False))
            notice_text_tmpl = texts.WL_FEED_EXHAUSTED_TRIAL_NOTICE if is_trial else texts.WL_FEED_EXHAUSTED_NOTICE
            return _build_service_notice_response(
                title_text=texts.WL_FEED_EXHAUSTED_TITLE,
                notice_text=notice_text_tmpl.format(bot_username=bot_user),
                common_headers=common_headers,
                channel_url=channel_url,
                support_url=support_url,
                effective_announce_url=effective_announce_url,
                upload_bytes=upload_bytes,
                download_bytes=download_bytes,
                total_quota=total_quota,
                expire_ts=expire_ts,
                extra_headers=notice_extra_headers,
            )

        # HWID (Device ID) enforcement: browser gets 1-click landing, app must provide HWID
        if not raw_hwid:
            if _is_browser_request(request):
                html_body = _render_white_internet_landing_html(
                    bot_username=bot_user,
                    is_active=True,
                    page_title=texts.LANDING_WHITE_INTERNET_PAGE_TITLE,
                    tier="white_internet",
                    tariff_badge=texts.LANDING_SPECIAL_ACCESS_BADGE,
                )
                return web.Response(status=200, text=html_body, headers=landing_headers)
            headers = dict(common_headers)
            headers["x-hwid-required"] = "true"
            return web.Response(status=403, text=texts.WL_WEB_HWID_REQUIRED, headers=headers)

        server_proto = getattr(server, "protocol", None)
        if not server_proto and server:
            caps = getattr(server, "capabilities", None) or []
            server_proto = XRAY_PROTOCOL if "xray_origin" in caps else None

        if server is None or "xray_origin" not in (server.capabilities or []):
            headers = dict(common_headers)
            headers["Retry-After"] = "5"
            return web.Response(status=503, text=texts.WL_WEB_UNSYNCED, headers=headers)

        # Invariants of protocol and credential convergence MUST hold unconditionally
        if (
            server_proto != XRAY_PROTOCOL
            or sub.actual_version != sub.desired_version
            or sub.last_reconciled_node_epoch != server.xray_instance_epoch
        ):
            headers = dict(common_headers)
            headers["Retry-After"] = "5"
            return web.Response(status=503, text=texts.WL_WEB_UNSYNCED, headers=headers)

        is_sub_already_active = (
            getattr(sub, "provisioning_status", None) == WhiteInternetProvisioningStatus.ACTIVE
            and (getattr(sub, "actual_version", 0) or 0) > 0
        )

        is_server_available = bool(server.is_active and server.health_state == ServerHealthState.ONLINE)
        if not is_server_available and not is_sub_already_active:
            headers = dict(common_headers)
            headers["Retry-After"] = "5"
            return web.Response(status=503, text=texts.WL_WEB_UNSYNCED, headers=headers)

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
                relays = None
        if not isinstance(relays, list):
            relays = None

        raw_dev_limit = getattr(sub, "device_limit", None)
        effective_limit = max(1, raw_dev_limit) if isinstance(raw_dev_limit, int) else 1
        device_meta = extract_device_metadata(request.headers)
        allowed_hwid, active_count, max_devs = await white_internet_repo.register_hwid_atomic(
            session, sub.id, raw_hwid, max_devices=effective_limit, device_info=device_meta
        )
        if not allowed_hwid:
            limit_msg = texts.WL_FEED_DEVICE_LIMIT_NOTICE.format(
                active=active_count,
                limit=max_devs,
                bot_username=bot_user,
            )
            extra_headers = {
                "Device-Limit-Exceeded": "1",
                "Device-Limit": str(max_devs),
                "Device-Active-Count": str(active_count),
                "x-hwid-max-devices-reached": "true",
                "x-hwid-limit": "true",
                "x-hwid-active": str(active_count),
            }
            if is_happ:
                if expire_ts > 0:
                    extra_headers["sub-expire"] = "1"
                extra_headers["sub-expire-button-link"] = f"https://t.me/{clean_bot}"
            return _build_service_notice_response(
                title_text=texts.VLESS_FEED_DEVICE_LIMIT_TITLE,
                notice_text=limit_msg,
                common_headers=common_headers,
                channel_url=channel_url,
                support_url=support_url,
                effective_announce_url=effective_announce_url,
                upload_bytes=upload_bytes,
                download_bytes=download_bytes,
                total_quota=total_quota,
                expire_ts=expire_ts,
                extra_headers=extra_headers,
            )

        origin_tag = extra.get("origin_tag")
        origin_badge = extra.get("origin_badge")
        default_relay_badge = extra.get("relay_badge")
        relay_names = extra.get("relay_names") if isinstance(extra.get("relay_names"), dict) else None
        relay_badges = extra.get("relay_badges") if isinstance(extra.get("relay_badges"), dict) else None

        origin_hidden = bool(extra.get("origin_hidden", False))
        relay_hidden_raw = extra.get("relay_hidden")
        if isinstance(relay_hidden_raw, (list, tuple, set)):
            relay_hidden = {str(item) for item in relay_hidden_raw}
        elif isinstance(relay_hidden_raw, str) and relay_hidden_raw.strip():
            relay_hidden = {item.strip() for item in relay_hidden_raw.split(",") if item.strip()}
        else:
            relay_hidden = set()

        active_relays = (
            [
                r
                for r in relays
                if isinstance(r, dict) and (r.get("code") or r.get("name")) not in relay_hidden
            ]
            if relays
            else None
        )

        vless_links = WhiteInternetService.generate_vless_links(
            sub,
            cdn_domain=cdn_domain,
            path=base_path,
            relays=active_relays,
            include_origin=not origin_hidden,
            origin_tag=origin_tag,
            origin_badge=origin_badge,
            default_relay_badge=default_relay_badge,
            relay_names=relay_names,
            relay_badges=relay_badges,
        )
        payload = "\n".join(vless_links)
        b64_payload = base64.b64encode(payload.encode("utf-8")).decode("utf-8")

        profile_title = (
            extra.get("profile_title")
            or WHITE_INTERNET_PROFILE_TITLE
            or texts.WL_PROFILE_NAME
        )
        show_devices_setting = await get_system_setting(session, "vless_profile_show_devices", False)
        if isinstance(show_devices_setting, str):
            show_devices = show_devices_setting.lower() in ("1", "true", "yes", "on")
        else:
            show_devices = bool(show_devices_setting)

        profile_title_base = (
            str(profile_title).strip().replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
        )
        if show_devices:
            suffix = f" [{active_count}/{max_devs}]"
            max_base_len = max(0, 25 - len(suffix))
            clean_base = profile_title_base[:max_base_len].rstrip()
            profile_title_clean = f"{clean_base}{suffix}"[:25]
        else:
            profile_title_clean = profile_title_base[:25]
        profile_title_b64 = base64.b64encode(profile_title_clean.encode("utf-8")).decode("ascii")

        response_headers = dict(common_headers)
        response_headers.update(
            {
                "Content-Type": "text/plain; charset=utf-8",
                "Content-Disposition": 'inline; filename="Just1k.txt"; filename*=UTF-8\'\'Just1k.txt',
                "Subscription-Userinfo": texts.WL_USERINFO_HEADER_TEMPLATE.format(
                    upload=upload_bytes,
                    download=download_bytes,
                    total=total_quota,
                    expire=expire_ts,
                ),
                "Device-Limit": str(max_devs),
                "Device-Active-Count": str(active_count),
                "Profile-Title": f"base64:{profile_title_b64}",
                "Profile-Update-Interval": _get_white_internet_update_interval(),
                "Hide-Url": "1",
                "Hide-Check": "1",
                "Sort-Order": "none",
                "No-Limit-Enabled": "1",
                "x-hwid-active": "true",
            }
        )
        routing_setting = await get_system_setting(session, "white_internet_routing_enabled", True)
        if isinstance(routing_setting, str):
            routing_enabled = routing_setting.lower() in ("1", "true", "yes", "on")
        else:
            routing_enabled = bool(routing_setting)

        routing_scheme = "happ" if is_happ else "incy"
        if routing_enabled:
            response_headers["Routing"] = generate_incy_routing_header(
                profile_name=profile_title,
                direct_ru=False,
                scheme=routing_scheme,
            )
            response_headers["routing-geo-update-interval"] = "168"
            if is_happ:
                response_headers["Routing-Enable"] = "true"
        else:
            response_headers["Routing"] = "off"
            if is_happ:
                response_headers["Routing-Enable"] = "0"

        if is_happ:
            if expire_ts > 0:
                response_headers["sub-expire"] = "1"
            response_headers["sub-expire-button-link"] = f"https://t.me/{clean_bot}"

        sanitized_support = _sanitize_header_url(support_url)
        sanitized_channel = _sanitize_header_url(channel_url)
        sanitized_announce = _sanitize_header_url(effective_announce_url, fallback=sanitized_channel)

        if sanitized_support:
            response_headers["Support-Url"] = sanitized_support
        if sanitized_channel:
            response_headers["Profile-Web-Page-Url"] = sanitized_channel
            response_headers["Channel-Url"] = sanitized_channel

        announce = extra.get("announce")
        if not announce and not is_server_available:
            announce = texts.WL_ANNOUNCE_MAINTENANCE

        if announce and str(announce).strip():
            clean_announce = str(announce).strip()
            clean_announce = _safe_format_device_template(
                clean_announce, devices=max_devs, active=active_count
            )[:200]
            announce_b64 = base64.b64encode(clean_announce.encode("utf-8")).decode("ascii")
            response_headers["Announce"] = f"base64:{announce_b64}"

        if sanitized_announce:
            response_headers["Announce-Url"] = sanitized_announce

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
    sub_prefix = (
        os.getenv("WHITE_INTERNET_SUB_PATH_PREFIX") or WHITE_INTERNET_SUB_PATH_PREFIX
    ).strip().rstrip("/")
    if not sub_prefix.startswith("/"):
        sub_prefix = f"/{sub_prefix}"
    app.router.add_get(f"{sub_prefix}/ping", white_internet_ping_handler)
    app.router.add_get(f"{sub_prefix}/{{token}}", white_internet_subscription_feed_handler)
    if sub_prefix != DEFAULT_WHITE_INTERNET_SUB_PATH_PREFIX:
        app.router.add_get(f"{DEFAULT_WHITE_INTERNET_SUB_PATH_PREFIX}/ping", white_internet_ping_handler)
        app.router.add_get(f"{DEFAULT_WHITE_INTERNET_SUB_PATH_PREFIX}/{{token}}", white_internet_subscription_feed_handler)
    logger.info("White Internet subscription feed routes registered: %s/{token} and %s/ping", sub_prefix, sub_prefix)
