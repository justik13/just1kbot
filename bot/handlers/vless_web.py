"""HTTP subscription feed endpoint for standard access (/sub/access/{token} and /sub/vless/{token})."""

from __future__ import annotations

import base64
import html
import logging
import os
from pathlib import Path
import re

from aiohttp import web

from bot import texts
from bot.formatters import get_tariff_display_name
from config.constants import (
    WHITE_INTERNET_CHANNEL_URL,
    WHITE_INTERNET_SUPPORT_URL,
)
from database.connection import session_scope
from database.repositories import tariffs_repo, users_repo, vless_subscription_repo
from database.repositories.system_settings_repo import get_system_setting
from services.subscription import SubscriptionService
from services.vless_subscription_service import VlessSubscriptionService
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



def _extract_device_metadata(request: web.Request) -> dict[str, str]:
    headers = request.headers
    raw_os = (
        headers.get("X-Device-Os")
        or headers.get("X-Device-OS")
        or headers.get("X-OS")
        or headers.get("x-device-os")
        or ""
    )
    raw_ver = (
        headers.get("X-Ver-Os")
        or headers.get("X-Ver-OS")
        or headers.get("x-ver-os")
        or ""
    )
    raw_model = (
        headers.get("X-Device-Model")
        or headers.get("X-Device-MODEL")
        or headers.get("X-Model")
        or headers.get("x-device-model")
        or ""
    )
    ua = headers.get("User-Agent") or ""

    clean_os = re.sub(r"[^\w\s\.\-]", "", raw_os).strip()[:32]
    clean_ver = re.sub(r"[^\w\s\.\-]", "", raw_ver).strip()[:16]
    clean_model = re.sub(r"[^\w\s\.\-,]", "", raw_model).strip()[:48]
    clean_ua = re.sub(r"[\r\n]", " ", ua).strip()[:64]

    meta: dict[str, str] = {}
    if clean_os and clean_ver:
        meta["os"] = f"{clean_os} {clean_ver}"[:32]
    elif clean_os:
        meta["os"] = clean_os
    elif clean_ver:
        meta["os"] = clean_ver[:32]

    if clean_model:
        meta["model"] = clean_model
    if clean_ua:
        meta["ua"] = clean_ua
    return meta


def _get_vless_update_interval() -> str:
    val = os.getenv("VLESS_PROFILE_UPDATE_INTERVAL", "6").strip()
    return val if val.isdigit() and int(val) > 0 else "6"


def _is_browser_request(request: web.Request) -> bool:
    """Check if incoming request is from a browser navigating to the page."""
    accept = request.headers.get("Accept", "").lower()
    if "text/html" in accept:
        return True
    ua = request.headers.get("User-Agent", "").lower()
    browser_tokens = ("mozilla/", "chrome/", "safari/", "applewebkit/", "mobile/", "android", "iphone")
    api_tokens = ("curl/", "wget/", "python-requests", "aiohttp/", "xray/", "v2ray", "sing-box", "clash")
    return any(b in ua for b in browser_tokens) and not any(a in ua for a in api_tokens)


async def _resolve_bot_username(request: web.Request) -> str:
    """Resolve current Telegram bot username dynamically without manual env setup."""
    cached = request.app.get("bot_username")
    if cached and isinstance(cached, str):
        return cached

    bot = request.app.get("bot")
    if bot is not None and hasattr(bot, "get_me"):
        try:
            me = await bot.get_me()
            if me and getattr(me, "username", None):
                username = str(me.username).lstrip("@")
                request.app["bot_username"] = username
                return username
        except Exception:
            pass

    try:
        from config.settings import get_settings
        settings_supp = getattr(get_settings(), "SUPPORT_USERNAME", None)
        if settings_supp:
            fallback = str(settings_supp).lstrip("@")
            request.app["bot_username"] = fallback
            return fallback
    except Exception:
        pass

    fallback = os.getenv("BOT_USERNAME", "").lstrip("@") or "just1k13bot"
    request.app["bot_username"] = fallback
    return fallback


def _resolve_support_url(first_extra: dict | None, default_bot_url: str) -> str:
    extra = first_extra if isinstance(first_extra, dict) else {}
    if extra.get("support_url"):
        return str(extra["support_url"])
    if WHITE_INTERNET_SUPPORT_URL:
        return WHITE_INTERNET_SUPPORT_URL
    try:
        from config.settings import get_settings
        supp = getattr(get_settings(), "SUPPORT_USERNAME", None)
        if supp:
            return f"https://t.me/{str(supp).lstrip('@')}"
    except Exception:
        pass
    return default_bot_url


def _resolve_channel_url(first_extra: dict | None, default_bot_url: str) -> str:
    extra = first_extra if isinstance(first_extra, dict) else {}
    if extra.get("channel_url"):
        return str(extra["channel_url"])
    if WHITE_INTERNET_CHANNEL_URL:
        return WHITE_INTERNET_CHANNEL_URL
    return default_bot_url


def _render_vless_landing_html(bot_username: str = "", is_active: bool = True) -> str:
    """Render universal landing page for INCY 1-click import and installation guidance."""
    clean_bot = html.escape(bot_username.strip().lstrip("@")) if bot_username else "just1k13bot"
    bot_link = f"https://t.me/{clean_bot}"

    if not is_active:
        return _LANDING_INACTIVE_HTML.replace("{clean_bot}", clean_bot).replace("{bot_link}", bot_link)
    return _LANDING_ACTIVE_HTML




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
        or request.headers.get("X-HWID")
        or request.headers.get("X-Device-Hwid")
        or request.headers.get("X-Device-HWID")
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
            if _is_browser_request(request):
                html_body = _render_vless_landing_html(bot_username=bot_username, is_active=False)
                landing_headers = {
                    **common_headers,
                    "Content-Type": "text/html; charset=utf-8",
                    "Content-Security-Policy": "default-src 'self' 'unsafe-inline' https:; img-src 'self' data: https:;",
                }
                return web.Response(status=200, text=html_body, headers=landing_headers)

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
                "Profile-Update-Interval": _get_vless_update_interval(),
                "Subscription-Userinfo": f"upload=0; download=0; total=0; expire={expire_ts}",
                "Profile-Title": f"base64:{b64_title}",
                "Hide-Url": "1",
                "No-Limit-Enabled": "1",
                "Support-Url": support_url,
                "Profile-Web-Page-Url": channel_url,
                "Channel-Url": channel_url,
            }
            return web.Response(status=200, text=b64_payload, headers=response_headers)

        # Strict HWID validation for active/emergency subscription requests
        if not raw_hwid:
            bot_username = await _resolve_bot_username(request)
            if _is_browser_request(request):
                html_body = _render_vless_landing_html(bot_username=bot_username, is_active=True)
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
        device_meta = _extract_device_metadata(request)
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
            limit_msg = texts.VLESS_FEED_DEVICE_LIMIT_NOTICE.format(
                active=active_hwid_count,
                limit=effective_vless_limit,
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
                "Profile-Update-Interval": _get_vless_update_interval(),
                "Subscription-Userinfo": f"upload=0; download=0; total=0; expire={expire_ts}",
                "Profile-Title": f"base64:{b64_title}",
                "Device-Limit-Exceeded": "1",
                "Device-Limit": str(effective_vless_limit),
                "Device-Active-Count": str(active_hwid_count),
                "x-hwid-max-devices-reached": "true",
                "x-hwid-limit": "true",
                "x-hwid-active": str(active_hwid_count),
                "Hide-Url": "1",
                "No-Limit-Enabled": "1",
                "Support-Url": support_url,
                "Profile-Web-Page-Url": channel_url,
                "Channel-Url": channel_url,
            })
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
            tariff_name = get_tariff_display_name(total_device_limit)
        # Strip decorative emoji prefixes and truncate cleanly to preserve following quota text
        tariff_clean = re.sub(r"^[^\w\u0400-\u04ffa-zA-Z0-9]+", "", str(tariff_name)).strip()[:40]

        profile_desc = await get_system_setting(session, "vless_profile_description", None)
        if not isinstance(profile_desc, str) or not profile_desc.strip():
            profile_desc = await get_system_setting(session, "vless_profile_desc", None)
        if not isinstance(profile_desc, str) or not profile_desc.strip():
            profile_desc = texts.DEFAULT_VLESS_PROFILE_DESCRIPTION

        announce = await get_system_setting(session, "vless_announce", None)
        if not isinstance(announce, str) or not announce.strip():
            announce = None

        announce_url = await get_system_setting(session, "vless_announce_url", None)
        if not isinstance(announce_url, str) or not announce_url.strip():
            announce_url = None
        elif any(c in announce_url for c in (" ", "\n", "\r", "\t")):
            announce_url = None

        first_extra = {}
        if servers:
            first_srv = servers[0]
            if isinstance(first_srv.extra_data, dict):
                first_extra = first_srv.extra_data

        support_url = _resolve_support_url(first_extra, default_bot_url)
        channel_url = _resolve_channel_url(first_extra, default_bot_url)

        profile_title_clean = str(profile_title).strip().replace("\r\n", " ").replace("\n", " ")[:25]
        if profile_desc:
            formatted_desc = _safe_format_vless_template(
                str(profile_desc).strip(),
                tariff=tariff_clean,
                devices=total_device_limit,
                active=total_active_devices,
            )[:100]
            full_title = f"{profile_title_clean}\n{formatted_desc}" if formatted_desc else profile_title_clean
        else:
            full_title = profile_title_clean

        b64_title = base64.b64encode(full_title.encode("utf-8")).decode("ascii")
        response_headers = {
            **common_headers,
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Disposition": 'inline; filename="Just1k.txt"; filename*=UTF-8\'\'Just1k.txt',
            "Profile-Update-Interval": _get_vless_update_interval(),
            "Subscription-Userinfo": f"upload=0; download=0; total=0; expire={expire_ts}",
            "Device-Limit": str(effective_vless_limit),
            "Device-Active-Count": str(active_hwid_count),
            "Profile-Title": f"base64:{b64_title}",
            "Sort-Order": "none",
            "x-hwid-active": "true",
            "Hide-Url": "1",
            "No-Limit-Enabled": "1",
            "Support-Url": support_url,
            "Profile-Web-Page-Url": channel_url,
            "Channel-Url": channel_url,
        }
        if announce:
            formatted_ann = _safe_format_vless_template(
                str(announce).strip(),
                tariff=tariff_clean,
                devices=total_device_limit,
                active=total_active_devices,
            )[:200]
            b64_ann = base64.b64encode(formatted_ann.encode("utf-8")).decode("ascii")
            response_headers["Announce"] = f"base64:{b64_ann}"
        if announce_url:
            response_headers["Announce-Url"] = announce_url
        return web.Response(status=200, text=b64_payload, headers=response_headers)


def setup_vless_web_routes(app: web.Application) -> None:
    """Register HTTP subscription feed route for VLESS access."""
    app.router.add_get("/sub/access/{token}", vless_subscription_feed_handler)
    app.router.add_get("/sub/vless/{token}", vless_subscription_feed_handler)
