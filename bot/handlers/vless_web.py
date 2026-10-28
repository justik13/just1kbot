"""HTTP subscription feed endpoint for standard access (/sub/access/{token} and /sub/vless/{token})."""

from __future__ import annotations

import base64
import html
import logging
import os
from pathlib import Path
import re
import urllib.parse

from aiohttp import web

from bot import texts
from bot.formatters import format_plural, get_tariff_display_name
from database.connection import session_scope
from database.repositories import tariffs_repo, users_repo, vless_subscription_repo
from database.repositories.system_settings_repo import get_system_setting
from services.subscription import SubscriptionService
from services.vless_subscription_service import VlessSubscriptionService
from utils.client_parser import extract_device_metadata, parse_client_from_user_agent
from utils.datetime_helpers import now_utc
from utils.http_rate_limiter import HttpRateLimiter, get_trusted_client_ip

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_LANDING_ACTIVE_HTML = (_TEMPLATES_DIR / "vless_landing_active.html").read_text(encoding="utf-8")
_LANDING_INACTIVE_HTML = (_TEMPLATES_DIR / "vless_landing_inactive.html").read_text(encoding="utf-8")

_ip_rate_limiter = HttpRateLimiter(rate_per_minute=60.0, burst=15)
_token_rate_limiter = HttpRateLimiter(rate_per_minute=30.0, burst=10)

HWID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-:]{3,128}$")


def _safe_format_vless_template(
    template: str,
    *,
    tariff: str,
    devices: int,
    active: int,
) -> str:
    """Safely format {tariff}, {tariff_name}, {devices}, {limit}, {active}, {active_devices} placeholders."""
    mapping = {
        "tariff": str(tariff),
        "tariff_name": str(tariff),
        "devices": str(devices),
        "device_limit": str(devices),
        "limit": str(devices),
        "active": str(active),
        "active_devices": str(active),
    }
    pattern = re.compile(r"\{(" + "|".join(re.escape(k) for k in mapping.keys()) + r")\}")
    return pattern.sub(lambda m: mapping.get(m.group(1), m.group(0)), template)



_extract_device_metadata = extract_device_metadata


def _get_vless_update_interval() -> str:
    val = os.getenv("VLESS_PROFILE_UPDATE_INTERVAL", "6").strip()
    return val if val.isdigit() and int(val) > 0 else "6"


_cached_bot_username: str | None = None


def _sanitize_header_url(url: str | None, fallback: str = "") -> str:
    """Sanitize URL for safe usage in HTTP headers without forbidden control chars, whitespace, or non-ASCII."""
    for candidate in (url, fallback):
        if candidate and isinstance(candidate, str):
            clean = candidate.strip()
            if clean and clean.isascii() and all(32 < ord(c) < 127 for c in clean):
                return clean
    return ""


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

    return os.getenv("BOT_USERNAME", "").lstrip("@") or "just1k13bot"


def _resolve_support_url(first_extra: dict | None, default_bot_url: str) -> str:
    extra = first_extra if isinstance(first_extra, dict) else {}
    raw = extra.get("support_url")
    if raw:
        cleaned = _sanitize_header_url(raw)
        if cleaned:
            return cleaned
    try:
        from config.settings import get_settings
        supp = getattr(get_settings(), "SUPPORT_USERNAME", None)
        if supp:
            return _sanitize_header_url(f"https://t.me/{str(supp).lstrip('@')}", fallback=default_bot_url)
    except Exception:
        pass
    return _sanitize_header_url(default_bot_url)


def _resolve_channel_url(first_extra: dict | None, default_bot_url: str) -> str:
    extra = first_extra if isinstance(first_extra, dict) else {}
    raw = extra.get("channel_url")
    if raw:
        cleaned = _sanitize_header_url(raw)
        if cleaned:
            return cleaned
    return _sanitize_header_url(default_bot_url)


async def _resolve_clean_tariff_name(
    session,
    user,
    total_device_limit: int | None = None,
) -> str:
    """Resolve human-readable tariff name for user, sanitized for headers and UI display."""
    tariff_name = None
    if getattr(user, "current_tariff_id", None):
        user_tariff = await tariffs_repo.get_tariff_by_id(session, user.current_tariff_id)
        if (
            user_tariff
            and getattr(user_tariff, "name", None)
            and (getattr(user_tariff, "service_type", None) or "standard") in ("standard", "awg")
        ):
            tariff_name = user_tariff.name
    if not tariff_name:
        if total_device_limit is None:
            total_device_limit = await SubscriptionService.get_effective_device_limit(session, user)
        tariff_name = get_tariff_display_name(total_device_limit)
    return re.sub(r"^[^\w\u0400-\u04ffa-zA-Z0-9]+", "", str(tariff_name)).strip()[:40]


def _render_vless_landing_html(
    bot_username: str = "",
    is_active: bool = True,
    status_badge: str | None = None,
    status_title: str | None = None,
    status_description: str | None = None,
    page_title: str | None = None,
    tier: str = "basic",
    tariff_badge: str | None = None,
) -> str:
    """Render universal landing page for INCY 1-click import and installation guidance."""
    clean_bot = html.escape(bot_username.strip().lstrip("@")) if bot_username else "just1k13bot"
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
    badge_html = f'<div class="tier-badge">{html.escape(tariff_badge)}</div>' if tariff_badge else ""
    effective_title = page_title or texts.LANDING_VLESS_PAGE_TITLE
    return (
        _LANDING_ACTIVE_HTML.replace("{page_title}", html.escape(effective_title))
        .replace("{tier}", html.escape(tier))
        .replace("{tariff_badge_html}", badge_html)
    )




async def vless_subscription_feed_handler(request: web.Request) -> web.Response:
    """Serve Base64 subscription feed for standard VLESS access with HWID device accounting."""
    client_ip = get_trusted_client_ip(request)
    allowed_ip, retry_after_ip = _ip_rate_limiter.check(client_ip)
    if not allowed_ip:
        return web.Response(
            status=429,
            text=texts.VLESS_WEB_TOO_MANY_REQUESTS,
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
            text=texts.VLESS_WEB_TOO_MANY_REQUESTS,
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
        or request.headers.get("X-Device-Hwid")
        or request.headers.get("X-Device-Id")
        or ""
    ).strip().strip("{}")
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

        is_active_access = SubscriptionService.check_vpn_access(user)

        if (
            not sub.is_active
            or getattr(user, "financial_hold", False) is True
            or not is_active_access
        ):
            if sub.is_active:
                sub.is_active = False
                sub.version = (getattr(sub, "version", 1) or 1) + 1
                session.add(sub)
                VlessSubscriptionService.ensure_synced_background(user.id, session=session)

            bot_username = await _resolve_bot_username(request)
            default_bot_url = f"https://t.me/{bot_username}"
            support_url = _resolve_support_url({}, default_bot_url)
            channel_url = _resolve_channel_url({}, default_bot_url)
            if not raw_hwid and _is_browser_request(request):
                sub_end = getattr(user, "subscription_end", None)
                now = now_utc()
                is_time_expired = sub_end is None or sub_end <= now

                if getattr(user, "financial_hold", False) or not is_time_expired:
                    badge = texts.LANDING_INACTIVE_BADGE_PAUSED
                    title = texts.LANDING_INACTIVE_TITLE_DEFAULT
                    desc = texts.LANDING_INACTIVE_DESC_DEFAULT
                else:
                    badge = texts.LANDING_INACTIVE_BADGE_EXPIRED
                    title = texts.LANDING_INACTIVE_TITLE_EXPIRED
                    desc = texts.LANDING_INACTIVE_DESC_EXPIRED

                html_body = _render_vless_landing_html(
                    bot_username=bot_username,
                    is_active=False,
                    status_badge=badge,
                    status_title=title,
                    status_description=desc,
                )
                landing_headers = {
                    **common_headers,
                    "Content-Type": "text/html; charset=utf-8",
                    "Content-Security-Policy": "default-src 'self' 'unsafe-inline' https:; img-src 'self' data: https:;",
                }
                return web.Response(status=200, text=html_body, headers=landing_headers)

            notice_text = texts.VLESS_FEED_EXPIRED_NOTICE.format(bot_username=bot_username)
            quoted_notice = urllib.parse.quote(notice_text)
            notice_link = (
                f"vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443"
                f"?encryption=none&security=none#{quoted_notice}"
            )
            b64_payload = base64.b64encode(notice_link.encode("utf-8")).decode("utf-8")
            b64_title = base64.b64encode(texts.VLESS_FEED_EXPIRED_TITLE.encode("utf-8")).decode("utf-8")
            expire_ts = int(user.subscription_end.timestamp()) if getattr(user, "subscription_end", None) else 0
            sub_up = max(0, int(getattr(sub, "traffic_up", 0) or 0))
            sub_down = max(0, int(getattr(sub, "traffic_down", 0) or 0))
            sanitized_support = _sanitize_header_url(support_url)
            sanitized_channel = _sanitize_header_url(channel_url)

            user_agent = request.headers.get("User-Agent", "")
            client_name = parse_client_from_user_agent(user_agent)
            is_happ = bool((client_name and "happ" in client_name.lower()) or ("happ" in user_agent.lower()))

            response_headers = {
                **common_headers,
                "Content-Type": "text/plain; charset=utf-8",
                "Profile-Update-Interval": _get_vless_update_interval(),
                "Subscription-Userinfo": f"upload={sub_up}; download={sub_down}; total=0; expire={expire_ts}",
                "Profile-Title": f"base64:{b64_title}",
                "Hide-Url": "1",
                "No-Limit-Enabled": "1",
            }
            if is_happ:
                clean_bot = str(bot_username).strip().lstrip("@") or "just1k13bot"
                if expire_ts > 0:
                    response_headers["sub-expire"] = "1"
                response_headers["sub-expire-button-link"] = f"https://t.me/{clean_bot}"
            if sanitized_support:
                response_headers["Support-Url"] = sanitized_support
            if sanitized_channel:
                response_headers["Announce-Url"] = sanitized_channel
                response_headers["Profile-Web-Page-Url"] = sanitized_channel
                response_headers["Channel-Url"] = sanitized_channel
            return web.Response(status=200, text=b64_payload, headers=response_headers)

        # Strict HWID validation for active/emergency subscription requests
        if not raw_hwid:
            bot_username = await _resolve_bot_username(request)
            if _is_browser_request(request):
                total_limit = await SubscriptionService.get_effective_device_limit(session, user)
                tariff_clean = await _resolve_clean_tariff_name(session, user, total_limit)
                page_title = texts.LANDING_VLESS_PAGE_TITLE
                if total_limit > 5:
                    tier = "pro"
                elif total_limit > 2:
                    tier = "family"
                else:
                    tier = "basic"

                prefix = tariff_clean.upper() if tariff_clean else tier.upper()
                device_str = format_plural(total_limit, texts.LANDING_DEVICE_FORMS_LABELS)
                tariff_badge = f"{prefix} • {device_str}"

                html_body = _render_vless_landing_html(
                    bot_username=bot_username,
                    is_active=True,
                    page_title=page_title,
                    tier=tier,
                    tariff_badge=tariff_badge,
                )
                landing_headers = {
                    **common_headers,
                    "Content-Type": "text/html; charset=utf-8",
                    "Content-Security-Policy": "default-src 'self' 'unsafe-inline' https:; img-src 'self' data: https:;",
                }
                return web.Response(status=200, text=html_body, headers=landing_headers)

            headers = dict(common_headers)
            headers["x-hwid-required"] = "true"
            return web.Response(status=403, text="HWID required", headers=headers)

        # Pre-flight check: ensure eligible VLESS servers exist and valid links can be generated
        servers = await VlessSubscriptionService.get_eligible_vless_servers(session)
        if not servers:
            retry_headers = {**common_headers, "Retry-After": "60"}
            return web.Response(status=503, text="No servers available", headers=retry_headers)

        links = VlessSubscriptionService.generate_vless_links(sub, servers)
        if not links:
            retry_headers = {**common_headers, "Retry-After": "60"}
            return web.Response(status=503, text="No servers available", headers=retry_headers)

        # Atomic HWID registration and state refresh under row locks (populate_existing=True)
        device_meta = extract_device_metadata(request.headers)
        allowed_hwid, active_hwid_count, effective_vless_limit = (
            await vless_subscription_repo.register_hwid_atomic(
                session, sub.id, raw_hwid, device_info=device_meta
            )
        )

        if not allowed_hwid:
            bot_username = await _resolve_bot_username(request)
            default_bot_url = f"https://t.me/{bot_username}"
            support_url = _resolve_support_url({}, default_bot_url)
            channel_url = _resolve_channel_url({}, default_bot_url)
            total_device_limit = await SubscriptionService.get_effective_device_limit(session, user)
            total_active_devices, _awg_active, _vless_active = (
                await VlessSubscriptionService.get_combined_quota(session, user)
            )
            limit_msg = texts.VLESS_FEED_DEVICE_LIMIT_NOTICE.format(
                active=total_active_devices,
                limit=total_device_limit,
                bot_username=bot_username,
            )
            quoted_limit = urllib.parse.quote(limit_msg)
            notice_link = (
                f"vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443"
                f"?encryption=none&security=none#{quoted_limit}"
            )
            b64_payload = base64.b64encode(notice_link.encode("utf-8")).decode("utf-8")
            b64_title = base64.b64encode(texts.VLESS_FEED_DEVICE_LIMIT_TITLE.encode("utf-8")).decode("utf-8")
            expire_ts = int(user.subscription_end.timestamp()) if getattr(user, "subscription_end", None) else 0
            sub_up = max(0, int(getattr(sub, "traffic_up", 0) or 0))
            sub_down = max(0, int(getattr(sub, "traffic_down", 0) or 0))
            headers = dict(common_headers)
            headers.update({
                "Content-Type": "text/plain; charset=utf-8",
                "Profile-Update-Interval": _get_vless_update_interval(),
                "Subscription-Userinfo": f"upload={sub_up}; download={sub_down}; total=0; expire={expire_ts}",
                "Profile-Title": f"base64:{b64_title}",
                "Device-Limit-Exceeded": "1",
                "Device-Limit": str(total_device_limit),
                "Device-Active-Count": str(total_active_devices),
                "x-hwid-max-devices-reached": "true",
                "x-hwid-limit": "true",
                "x-hwid-active": str(total_active_devices),
                "Hide-Url": "1",
                "No-Limit-Enabled": "1",
            })
            user_agent = request.headers.get("User-Agent", "")
            client_name = parse_client_from_user_agent(user_agent)
            is_happ = bool((client_name and "happ" in client_name.lower()) or ("happ" in user_agent.lower()))
            if is_happ:
                clean_bot = str(bot_username).strip().lstrip("@") or "just1k13bot"
                if expire_ts > 0:
                    headers["sub-expire"] = "1"
                headers["sub-expire-button-link"] = f"https://t.me/{clean_bot}"
            sanitized_support = _sanitize_header_url(support_url)
            sanitized_channel = _sanitize_header_url(channel_url)
            if sanitized_support:
                headers["Support-Url"] = sanitized_support
            if sanitized_channel:
                headers["Announce-Url"] = sanitized_channel
                headers["Profile-Web-Page-Url"] = sanitized_channel
                headers["Channel-Url"] = sanitized_channel
            return web.Response(status=200, text=b64_payload, headers=headers)

        await session.refresh(sub)
        links = VlessSubscriptionService.generate_vless_links(sub, servers)

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
        bot_username = await _resolve_bot_username(request)
        default_bot_url = f"https://t.me/{bot_username}"

        profile_title = await get_system_setting(session, "vless_profile_title", os.getenv("VLESS_PROFILE_TITLE", "✦ Just1k"))
        if not isinstance(profile_title, str) or not profile_title.strip():
            profile_title = os.getenv("VLESS_PROFILE_TITLE", "✦ Just1k") or "✦ Just1k"

        total_device_limit = await SubscriptionService.get_effective_device_limit(session, user)
        total_active_devices, _awg_active, _vless_active = (
            await VlessSubscriptionService.get_combined_quota(session, user)
        )

        tariff_clean = await _resolve_clean_tariff_name(session, user, total_device_limit)

        announce = await get_system_setting(session, "vless_announce", None)
        if not isinstance(announce, str) or not announce.strip():
            announce = None

        raw_announce_url = await get_system_setting(session, "vless_announce_url", None)
        clean_announce_url = _sanitize_header_url(raw_announce_url)

        first_extra = {}
        if servers:
            first_srv = servers[0]
            if isinstance(first_srv.extra_data, dict):
                first_extra = first_srv.extra_data

        support_url = _resolve_support_url(first_extra, default_bot_url)
        channel_url = _resolve_channel_url(first_extra, default_bot_url)

        show_devices_setting = await get_system_setting(session, "vless_profile_show_devices", False)
        if isinstance(show_devices_setting, str):
            show_devices = show_devices_setting.lower() in ("1", "true", "yes", "on")
        else:
            show_devices = bool(show_devices_setting)

        routing_setting = await get_system_setting(session, "vless_routing_enabled", True)
        if isinstance(routing_setting, str):
            routing_enabled = routing_setting.lower() in ("1", "true", "yes", "on")
        else:
            routing_enabled = bool(routing_setting)

        profile_title_base = str(profile_title).strip().replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
        if show_devices:
            suffix = f" [{total_active_devices}/{total_device_limit}]"
            max_base_len = max(0, 25 - len(suffix))
            clean_base = profile_title_base[:max_base_len].rstrip()
            full_title = f"{clean_base}{suffix}"[:25]
        else:
            full_title = profile_title_base[:25]

        upload_bytes = max(0, int(getattr(sub, "traffic_up", 0) or 0))
        download_bytes = max(0, int(getattr(sub, "traffic_down", 0) or 0))
        b64_title = base64.b64encode(full_title.encode("utf-8")).decode("ascii")
        response_headers = {
            **common_headers,
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Disposition": 'inline; filename="Just1k.txt"; filename*=UTF-8\'\'Just1k.txt',
            "Profile-Update-Interval": _get_vless_update_interval(),
            "Subscription-Userinfo": f"upload={upload_bytes}; download={download_bytes}; total=0; expire={expire_ts}",
            "Device-Limit": str(effective_vless_limit),
            "Device-Active-Count": str(active_hwid_count),
            "Profile-Title": f"base64:{b64_title}",
            "Sort-Order": "none",
            "x-hwid-active": "true",
            "Hide-Url": "1",
            "No-Limit-Enabled": "1",
        }
        user_agent = request.headers.get("User-Agent", "")
        client_name = parse_client_from_user_agent(user_agent)
        is_happ = bool((client_name and "happ" in client_name.lower()) or ("happ" in user_agent.lower()))
        routing_scheme = "happ" if is_happ else "incy"

        if routing_enabled:
            response_headers["Routing"] = VlessSubscriptionService.generate_incy_routing_header(
                profile_name="✦ Just1k",
                direct_ru=True,
                scheme=routing_scheme,
            )
            response_headers["routing-geo-update-interval"] = "168"
        else:
            response_headers["Routing"] = "off"
            if is_happ:
                response_headers["routing-enable"] = "0"

        if is_happ:
            clean_bot = str(bot_username).strip().lstrip("@") or "just1k13bot"
            if expire_ts > 0:
                response_headers["sub-expire"] = "1"
            response_headers["sub-expire-button-link"] = f"https://t.me/{clean_bot}"

        sanitized_support = _sanitize_header_url(support_url)
        sanitized_channel = _sanitize_header_url(channel_url)
        sanitized_announce = _sanitize_header_url(clean_announce_url, fallback=sanitized_channel)

        if sanitized_support:
            response_headers["Support-Url"] = sanitized_support
        if sanitized_channel:
            response_headers["Profile-Web-Page-Url"] = sanitized_channel
            response_headers["Channel-Url"] = sanitized_channel

        if announce:
            formatted_ann = _safe_format_vless_template(
                str(announce).strip(),
                tariff=tariff_clean,
                devices=total_device_limit,
                active=total_active_devices,
            )[:200]
            b64_ann = base64.b64encode(formatted_ann.encode("utf-8")).decode("ascii")
            response_headers["Announce"] = f"base64:{b64_ann}"

        if sanitized_announce:
            response_headers["Announce-Url"] = sanitized_announce
        return web.Response(status=200, text=b64_payload, headers=response_headers)


def setup_vless_web_routes(app: web.Application) -> None:
    """Register HTTP subscription feed route for VLESS access."""
    app.router.add_get("/sub/access/{token}", vless_subscription_feed_handler)
    app.router.add_get("/sub/vless/{token}", vless_subscription_feed_handler)
