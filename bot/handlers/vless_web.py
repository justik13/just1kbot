"""HTTP subscription feed endpoint for standard access (/sub/access/{token} and /sub/vless/{token})."""

from __future__ import annotations

import base64
import functools
import html
import logging
import os
from pathlib import Path
import re
import urllib.parse

from aiohttp import web
from sqlalchemy import select

from bot import texts
from bot.texts import get_tariff_display_name
from config import constants
from config.enums import (
    ServerHealthState,
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from database.connection import session_scope
from database.models import Server
from database.repositories import (
    tariffs_repo,
    user_devices_repo,
    users_repo,
    vless_subscription_repo,
    white_internet_repo,
)
from database.repositories.system_settings_repo import get_system_setting
from services.subscription import SubscriptionService
from services.vless_subscription_service import VlessSubscriptionService
from utils.client_parser import extract_device_metadata, parse_client_from_user_agent
from utils.datetime_helpers import is_permanent_subscription, now_utc
from utils.formatters import format_plural
from utils.http_rate_limiter import HttpRateLimiter, get_trusted_client_ip

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


@functools.lru_cache(maxsize=1)
def get_vless_landing_active_html() -> str:
    """Assemble modular landing template with inline Base64 real screenshots into memory."""
    landing_dir = _TEMPLATES_DIR / "vless_landing"
    if not landing_dir.is_dir():
        raise FileNotFoundError(f"Landing directory not found: {landing_dir}")

    base = (landing_dir / "base.html").read_text(encoding="utf-8")
    css = (landing_dir / "css" / "landing.css").read_text(encoding="utf-8")
    js = (landing_dir / "js" / "landing.js").read_text(encoding="utf-8")
    connect_view = (landing_dir / "views" / "connect_view.html").read_text(encoding="utf-8")
    guides_view = (landing_dir / "views" / "guides_view.html").read_text(encoding="utf-8")

    guide_ios = (landing_dir / "partials" / "guide_ios.html").read_text(encoding="utf-8")
    guide_android = (landing_dir / "partials" / "guide_android.html").read_text(encoding="utf-8")
    guide_desktop = (landing_dir / "partials" / "guide_desktop.html").read_text(encoding="utf-8")
    guide_faq = (landing_dir / "partials" / "guide_faq.html").read_text(encoding="utf-8")

    assets_dir = landing_dir / "assets"
    asset_placeholders = {
        "{{IMG_IOS_1}}": "ios_01_profile.jpg",
        "{{IMG_IOS_2}}": "ios_02_kazakhstan.jpg",
        "{{IMG_IOS_3}}": "ios_03_payment.jpg",
        "{{IMG_IOS_4}}": "ios_04_appstore.jpg",
        "{{IMG_TESTFLIGHT}}": "ios_testflight.jpg",
        "{{IMG_ANDROID_APK}}": "android_apk_warning.jpg",
    }
    missing_assets = [fn for fn in asset_placeholders.values() if not (assets_dir / fn).is_file()]
    if missing_assets:
        raise FileNotFoundError(f"Missing required landing asset files: {missing_assets}")

    for placeholder, filename in asset_placeholders.items():
        img_path = assets_dir / filename
        b64_data = base64.b64encode(img_path.read_bytes()).decode("ascii")
        data_uri = f"data:image/jpeg;base64,{b64_data}"
        guide_ios = guide_ios.replace(placeholder, data_uri)
        guide_android = guide_android.replace(placeholder, data_uri)

    guides_view = (
        guides_view.replace("<!-- {{GUIDE_IOS}} -->", guide_ios)
        .replace("<!-- {{GUIDE_ANDROID}} -->", guide_android)
        .replace("<!-- {{GUIDE_DESKTOP}} -->", guide_desktop)
        .replace("<!-- {{GUIDE_FAQ}} -->", guide_faq)
    )

    rendered = (
        base.replace("/* {{LANDING_CSS}} */", css)
        .replace("<!-- {{CONNECT_VIEW}} -->", connect_view)
        .replace("<!-- {{GUIDES_VIEW}} -->", guides_view)
        .replace("// {{LANDING_JS}}", js)
    )

    if "{{IMG_" in rendered:
        raise ValueError("Unreplaced image placeholders found in assembled landing HTML")
    if "<!-- {{GUIDE_" in rendered:
        raise ValueError("Unreplaced guide slot placeholders found in assembled landing HTML")

    return rendered


@functools.lru_cache(maxsize=1)
def get_vless_landing_inactive_html() -> str:
    return (_TEMPLATES_DIR / "vless_landing_inactive.html").read_text(encoding="utf-8")


_ip_rate_limiter = HttpRateLimiter(rate_per_minute=60.0, burst=15)
_token_rate_limiter = HttpRateLimiter(rate_per_minute=30.0, burst=10)
_report_ip_limiter = HttpRateLimiter(rate_per_minute=5.0, burst=3)
_report_token_limiter = HttpRateLimiter(rate_per_minute=3.0, burst=2)

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

    from utils.telegram import resolve_bot_username

    bot = request.app.get("bot") if hasattr(request, "app") else None
    username = await resolve_bot_username(bot)
    if username:
        _cached_bot_username = username
        if hasattr(request, "app") and not getattr(request.app, "frozen", False):
            try:
                request.app["bot_username"] = username
            except Exception:
                pass
        return username

    return constants.DEFAULT_FALLBACK_BOT_USERNAME


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
            and (getattr(user_tariff, "service_type", None) or "standard") == "standard"
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
    device_limit: int | None = None,
) -> str:
    """Render universal landing page for INCY 1-click import and installation guidance."""
    clean_bot = html.escape(bot_username.strip().lstrip("@")) if bot_username else constants.DEFAULT_FALLBACK_BOT_USERNAME
    bot_link = f"https://t.me/{clean_bot}"

    if not is_active:
        badge = status_badge or texts.LANDING_INACTIVE_BADGE_PAUSED
        title = status_title or texts.LANDING_INACTIVE_TITLE_DEFAULT
        desc = status_description or texts.LANDING_INACTIVE_DESC_DEFAULT
        return (
            get_vless_landing_inactive_html()
            .replace("{clean_bot}", clean_bot)
            .replace("{bot_link}", bot_link)
            .replace("{status_badge}", html.escape(badge))
            .replace("{status_title}", html.escape(title))
            .replace("{status_description}", html.escape(desc))
        )
    badge_html = f'<div class="tier-badge">{html.escape(tariff_badge)}</div>' if tariff_badge else ""
    effective_title = page_title or texts.LANDING_VLESS_PAGE_TITLE
    return (
        get_vless_landing_active_html()
        .replace("{page_title}", html.escape(effective_title))
        .replace("{tier}", html.escape(tier))
        .replace("{tariff_badge_html}", badge_html)
        .replace("{device_limit}", str(device_limit or 1))
    )




def _format_announce_summary(
    *,
    std_active: bool,
    std_end,
    wi_active: bool,
    wi_end,
    wi_available_bytes: int,
) -> str:
    """Build concise stop-word-free Announce summary of active options, dates, and remaining GB."""
    parts: list[str] = []
    if std_active:
        if std_end and is_permanent_subscription(std_end):
            parts.append("Стандарт (без срока)")
        elif std_end and hasattr(std_end, "strftime"):
            parts.append(f"Стандарт до {std_end.strftime('%d.%m.%Y')}")
        else:
            parts.append("Стандарт (активен)")

    if wi_active:
        rem_gb = max(0, wi_available_bytes) / (1024**3)
        gb_str = f"{int(round(rem_gb))}" if abs(rem_gb - round(rem_gb)) < 0.05 else f"{rem_gb:.1f}"
        if wi_end and hasattr(wi_end, "strftime"):
            parts.append(f"Белый интернет до {wi_end.strftime('%d.%m.%Y')} (остаток {gb_str} ГБ)")
        else:
            parts.append(f"Белый интернет (остаток {gb_str} ГБ)")

    return " | ".join(parts)[:200]


def _build_unified_notice_response(
    *,
    title_text: str,
    notice_text: str,
    common_headers: dict[str, str],
    bot_username: str,
    support_url: str,
    channel_url: str,
    announce_url: str,
    upload_bytes: int,
    download_bytes: int,
    expire_ts: int,
    is_happ: bool,
    extra_headers: dict[str, str] | None = None,
) -> web.Response:
    quoted_notice = urllib.parse.quote(notice_text)
    notice_link = (
        f"vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443"
        f"?encryption=none&security=none#{quoted_notice}"
    )
    b64_payload = base64.b64encode(notice_link.encode("utf-8")).decode("utf-8")
    b64_title = base64.b64encode(title_text.encode("utf-8")).decode("utf-8")

    headers = {
        **common_headers,
        "Content-Type": "text/plain; charset=utf-8",
        "Profile-Update-Interval": _get_vless_update_interval(),
        "Subscription-Userinfo": f"upload={upload_bytes}; download={download_bytes}; total=0; expire={expire_ts}",
        "Profile-Title": f"base64:{b64_title}",
        "Hide-Url": "1",
        "No-Limit-Enabled": "1",
    }
    if is_happ:
        clean_bot = str(bot_username).strip().lstrip("@") or constants.DEFAULT_FALLBACK_BOT_USERNAME
        if expire_ts > 0:
            headers["sub-expire"] = "1"
        headers["sub-expire-button-link"] = f"https://t.me/{clean_bot}"

    sanitized_support = _sanitize_header_url(support_url)
    sanitized_channel = _sanitize_header_url(channel_url)
    sanitized_announce = _sanitize_header_url(announce_url, fallback=sanitized_channel)
    if sanitized_support:
        headers["Support-Url"] = sanitized_support
    if sanitized_channel:
        headers["Profile-Web-Page-Url"] = sanitized_channel
        headers["Channel-Url"] = sanitized_channel
    if sanitized_announce:
        headers["Announce-Url"] = sanitized_announce
    if extra_headers:
        headers.update(extra_headers)
    return web.Response(status=200, text=b64_payload, headers=headers)


def _compute_wi_traffic_stats(wi_sub) -> tuple[int, int]:
    """Return (total_quota_bytes, available_bytes) for a WhiteInternetSubscription."""
    traffic_limit = getattr(wi_sub, "traffic_limit_bytes", None)
    base_bytes = getattr(wi_sub, "base_traffic_bytes", None)
    extra_bytes = getattr(wi_sub, "extra_traffic_bytes", None)
    if isinstance(traffic_limit, (int, float)):
        total_quota = int(traffic_limit)
    elif isinstance(base_bytes, (int, float)) or isinstance(extra_bytes, (int, float)):
        total_quota = (int(base_bytes) if isinstance(base_bytes, (int, float)) else 0) + (
            int(extra_bytes) if isinstance(extra_bytes, (int, float)) else 0
        )
    else:
        total_quota = constants.WHITE_INTERNET_BASE_TRAFFIC_BYTES

    raw_used = getattr(wi_sub, "traffic_used_bytes", None)
    raw_over = getattr(wi_sub, "traffic_overage_bytes", None)
    used_val = max(
        0,
        (int(raw_used) if isinstance(raw_used, (int, float)) else 0)
        - (int(raw_over) if isinstance(raw_over, (int, float)) else 0),
    )
    return total_quota, max(0, total_quota - used_val)


async def serve_unified_subscription_feed(
    request: web.Request,
    *,
    token_source: str = "vless",
) -> web.Response:
    """Serve unified Base64 subscription feed (Standard + White Internet) with HWID accounting."""
    client_ip = get_trusted_client_ip(request)
    allowed_ip, retry_after_ip = _ip_rate_limiter.check(client_ip)
    if not allowed_ip:
        return web.Response(
            status=429,
            text=texts.VLESS_WEB_TOO_MANY_REQUESTS,
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
            text=texts.VLESS_WEB_TOO_MANY_REQUESTS,
            headers={
                "Retry-After": str(retry_after_tok),
                "Cache-Control": "no-store",
                "CDN-Cache-Control": "no-store",
            },
        )

    common_headers = {
        "Cache-Control": "no-store, private, no-cache, must-revalidate",
        "CDN-Cache-Control": "no-store",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
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

    now = now_utc()
    user_agent = request.headers.get("User-Agent", "")
    client_name = parse_client_from_user_agent(user_agent)
    is_happ = bool((client_name and "happ" in client_name.lower()) or ("happ" in user_agent.lower()))

    async with session_scope() as session:
        vless_sub = None
        wi_sub = None
        if token_source == "white_internet":
            wi_sub = await white_internet_repo.get_subscription_by_token(session, token)
            if wi_sub is None:
                vless_sub = await vless_subscription_repo.get_subscription_by_token(session, token)
        else:
            vless_sub = await vless_subscription_repo.get_subscription_by_token(session, token)
            if vless_sub is None:
                wi_sub = await white_internet_repo.get_subscription_by_token(session, token)

        matched_sub = vless_sub or wi_sub
        if matched_sub is None:
            return web.Response(status=404, text="Not Found", headers=common_headers)

        user = await users_repo.get_user_by_id(session, matched_sub.user_id)
        if user is None or getattr(user, "is_deleted", False) is True:
            return web.Response(status=404, text="Not Found", headers=common_headers)

        if vless_sub is None:
            vless_sub = await vless_subscription_repo.get_subscription_by_user_id(session, user.id)
        if wi_sub is None:
            wi_sub = await white_internet_repo.get_subscription_by_user_id(session, user.id)

        is_financial_hold = bool(getattr(user, "financial_hold", False))
        is_std_access = SubscriptionService.check_vpn_access(user)
        if vless_sub and vless_sub.is_active and (is_financial_hold or not is_std_access):
            vless_sub.is_active = False
            vless_sub.version = (getattr(vless_sub, "version", 1) or 1) + 1
            session.add(vless_sub)
            VlessSubscriptionService.ensure_synced_background(user.id, session=session)

        std_active = bool(vless_sub and vless_sub.is_active and not is_financial_hold and is_std_access)
        std_end = getattr(user, "subscription_end", None) if std_active else None

        wi_available_bytes = 0
        wi_is_pending = False
        wi_is_expired = False
        wi_is_exhausted = False
        wi_is_disabled = False
        if wi_sub is not None:
            _, wi_available_bytes = _compute_wi_traffic_stats(wi_sub)
            wi_is_pending = wi_sub.status == WhiteInternetStatus.PENDING and not is_financial_hold
            wi_is_disabled = wi_sub.status == WhiteInternetStatus.DISABLED
            wi_is_expired = bool(
                wi_sub.status == WhiteInternetStatus.EXPIRED
                or (wi_sub.expires_at and wi_sub.expires_at <= now)
            )
            wi_is_exhausted = bool(
                wi_sub.status == WhiteInternetStatus.EXHAUSTED or wi_available_bytes <= 0
            )

        wi_active = bool(
            wi_sub is not None
            and not is_financial_hold
            and wi_sub.status == WhiteInternetStatus.ACTIVE
            and not wi_is_expired
            and not wi_is_exhausted
        )
        wi_end = getattr(wi_sub, "expires_at", None) if wi_active else None

        std_up = max(0, int(getattr(vless_sub, "traffic_up", 0) or 0)) if vless_sub else 0
        std_down = max(0, int(getattr(vless_sub, "traffic_down", 0) or 0)) if vless_sub else 0
        wi_up = max(0, int(getattr(wi_sub, "traffic_uplink_bytes", 0) or 0)) if wi_sub else 0
        wi_down = max(0, int(getattr(wi_sub, "traffic_downlink_bytes", 0) or 0)) if wi_sub else 0
        upload_bytes = std_up + wi_up
        download_bytes = std_down + wi_down

        std_ts = int(std_end.timestamp()) if std_end and hasattr(std_end, "timestamp") else 0
        wi_ts = int(wi_end.timestamp()) if wi_end and hasattr(wi_end, "timestamp") else 0
        expire_ts = max(std_ts, wi_ts)

        bot_username = await _resolve_bot_username(request)
        default_bot_url = f"https://t.me/{bot_username}"
        support_url = _resolve_support_url({}, default_bot_url)
        channel_url = _resolve_channel_url({}, default_bot_url)

        # 1. Neither option is active -> return pending or expired/inactive stub
        if not std_active and not wi_active:
            if wi_is_pending:
                if not raw_hwid and _is_browser_request(request):
                    from bot.handlers.white_internet_web import _render_white_internet_pending_landing_html
                    html_body = _render_white_internet_pending_landing_html(bot_username=bot_username)
                    landing_headers = {
                        **common_headers,
                        "Content-Type": "text/html; charset=utf-8",
                        "Content-Security-Policy": "default-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self';",
                    }
                    return web.Response(status=200, text=html_body, headers=landing_headers)
                return web.Response(
                    status=503,
                    text=texts.WL_WEB_PENDING,
                    headers={**common_headers, "Retry-After": "5"},
                )

            if not raw_hwid and _is_browser_request(request):
                if is_financial_hold:
                    badge = texts.LANDING_INACTIVE_BADGE_PAUSED
                    title = texts.LANDING_INACTIVE_TITLE_DEFAULT
                    desc = texts.LANDING_INACTIVE_DESC_DEFAULT
                elif wi_sub is not None and wi_is_exhausted and not wi_is_expired and vless_sub is None:
                    badge = texts.LANDING_INACTIVE_BADGE_EXHAUSTED
                    title = texts.LANDING_INACTIVE_TITLE_EXHAUSTED
                    desc = texts.LANDING_INACTIVE_DESC_EXHAUSTED
                elif wi_sub is not None and wi_is_disabled and vless_sub is None:
                    badge = texts.LANDING_INACTIVE_BADGE_DISABLED
                    title = texts.LANDING_INACTIVE_TITLE_DISABLED
                    desc = texts.LANDING_INACTIVE_DESC_DISABLED
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
                    "Content-Security-Policy": "default-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self';",
                }
                return web.Response(status=200, text=html_body, headers=landing_headers)

            fallback_exp_ts = 0
            if getattr(user, "subscription_end", None):
                fallback_exp_ts = max(fallback_exp_ts, int(user.subscription_end.timestamp()))
            if wi_sub and getattr(wi_sub, "expires_at", None):
                fallback_exp_ts = max(fallback_exp_ts, int(wi_sub.expires_at.timestamp()))

            if wi_sub is not None and wi_is_exhausted and not wi_is_expired and not std_active and vless_sub is None:
                stub_title = texts.WL_FEED_EXHAUSTED_TITLE
                stub_notice = texts.WL_FEED_EXHAUSTED_NOTICE.format(bot_username=bot_username)
            elif wi_sub is not None and wi_is_disabled and not std_active and vless_sub is None:
                stub_title = texts.WL_FEED_DISABLED_TITLE
                stub_notice = texts.WL_FEED_DISABLED_NOTICE.format(bot_username=bot_username)
            else:
                stub_title = texts.VLESS_FEED_EXPIRED_TITLE
                stub_notice = texts.VLESS_FEED_EXPIRED_NOTICE.format(bot_username=bot_username)

            return _build_unified_notice_response(
                title_text=stub_title,
                notice_text=stub_notice,
                common_headers=common_headers,
                bot_username=bot_username,
                support_url=support_url,
                channel_url=channel_url,
                announce_url=channel_url,
                upload_bytes=upload_bytes,
                download_bytes=download_bytes,
                expire_ts=fallback_exp_ts,
                is_happ=is_happ,
            )

        # 2. Active subscription(s), but no HWID provided -> landing page for browser or 403 for app
        std_limit = await SubscriptionService.get_effective_device_limit(session, user) if std_active else 0
        wi_limit = max(1, getattr(wi_sub, "device_limit", 1) or 1) if wi_active else 0
        max_devices = max(std_limit, wi_limit, 1)

        if not raw_hwid:
            if _is_browser_request(request):
                tariff_clean = await _resolve_clean_tariff_name(session, user, max_devices) if std_active else ""
                if std_active:
                    if max_devices > 5:
                        tier = "pro"
                    elif max_devices > 2:
                        tier = "family"
                    else:
                        tier = "basic"
                    prefix = tariff_clean.upper() if tariff_clean else tier.upper()
                    device_str = format_plural(max_devices, texts.LANDING_DEVICE_FORMS_LABELS)
                    tariff_badge = f"{prefix} • {device_str}"
                    page_title = texts.LANDING_VLESS_PAGE_TITLE
                else:
                    tier = "white_internet"
                    tariff_badge = texts.LANDING_SPECIAL_ACCESS_BADGE
                    page_title = texts.LANDING_WHITE_INTERNET_PAGE_TITLE

                html_body = _render_vless_landing_html(
                    bot_username=bot_username,
                    is_active=True,
                    page_title=page_title,
                    tier=tier,
                    tariff_badge=tariff_badge,
                    device_limit=max_devices,
                )
                landing_headers = {
                    **common_headers,
                    "Content-Type": "text/html; charset=utf-8",
                    "Content-Security-Policy": "default-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self';",
                }
                return web.Response(status=200, text=html_body, headers=landing_headers)

            headers = dict(common_headers)
            headers["x-hwid-required"] = "true"
            return web.Response(status=403, text="HWID required", headers=headers)

        # 3. Unified HWID registration + priority-based BI slot assignment
        device_meta = extract_device_metadata(request.headers)
        allowed_hwid, active_hwid_count, effective_limit = await user_devices_repo.register_user_device(
            session, user.id, raw_hwid, max_devices, device_info=device_meta
        )
        if not allowed_hwid:
            limit_msg = texts.VLESS_FEED_DEVICE_LIMIT_NOTICE.format(
                active=active_hwid_count,
                limit=effective_limit,
                bot_username=bot_username,
            )
            return _build_unified_notice_response(
                title_text=texts.VLESS_FEED_DEVICE_LIMIT_TITLE,
                notice_text=limit_msg,
                common_headers=common_headers,
                bot_username=bot_username,
                support_url=support_url,
                channel_url=channel_url,
                announce_url=channel_url,
                upload_bytes=upload_bytes,
                download_bytes=download_bytes,
                expire_ts=expire_ts,
                is_happ=is_happ,
                extra_headers={
                    "Device-Limit-Exceeded": "1",
                    "Device-Limit": str(effective_limit),
                    "Device-Active-Count": str(active_hwid_count),
                    "x-hwid-max-devices-reached": "true",
                    "x-hwid-limit": "true",
                    "x-hwid-active": str(active_hwid_count),
                },
            )

        is_bi_assigned = False
        if wi_active:
            devices_list = await user_devices_repo.list_user_devices(
                session, user.id, wi_device_limit=wi_limit
            )
            is_bi_assigned = any(
                d.get("hwid") == raw_hwid and bool(d.get("is_bi_assigned"))
                for d in devices_list
            )
            if not std_active and not is_bi_assigned:
                limit_msg = texts.WL_FEED_DEVICE_LIMIT_NOTICE.format(
                    active=active_hwid_count,
                    limit=wi_limit,
                    bot_username=bot_username,
                )
                return _build_unified_notice_response(
                    title_text=texts.VLESS_FEED_DEVICE_LIMIT_TITLE,
                    notice_text=limit_msg,
                    common_headers=common_headers,
                    bot_username=bot_username,
                    support_url=support_url,
                    channel_url=channel_url,
                    announce_url=channel_url,
                    upload_bytes=upload_bytes,
                    download_bytes=download_bytes,
                    expire_ts=expire_ts,
                    is_happ=is_happ,
                    extra_headers={
                        "Device-Limit-Exceeded": "1",
                        "Device-Limit": str(wi_limit),
                        "Device-Active-Count": str(active_hwid_count),
                        "x-hwid-max-devices-reached": "true",
                        "x-hwid-limit": "true",
                        "x-hwid-active": str(active_hwid_count),
                    },
                )

        # 4. Generate links for active options
        all_links: list[str] = []
        std_servers: list[Server] = []
        if std_active and vless_sub is not None:
            std_servers = await VlessSubscriptionService.get_eligible_vless_servers(session)
            std_links = VlessSubscriptionService.generate_vless_links(vless_sub, std_servers)
            all_links.extend(std_links)

            last_synced_version = getattr(vless_sub, "last_synced_version", None)
            current_version = getattr(vless_sub, "version", 1) or 1
            needs_sync = (
                vless_sub.last_synced_at is None
                or (now - vless_sub.last_synced_at).total_seconds() > 300
                or bool(getattr(vless_sub, "pending_revoked_uuids", None))
                or (last_synced_version is not None and last_synced_version < current_version)
            )
            if needs_sync:
                VlessSubscriptionService.ensure_synced_background(user.id, session=session)

        wi_server: Server | None = None
        if wi_active and is_bi_assigned and wi_sub is not None:
            if getattr(wi_sub, "origin_node_id", None):
                wi_server = await session.scalar(select(Server).where(Server.id == wi_sub.origin_node_id))
            is_wi_node_ready = bool(
                wi_server is not None
                and "xray_origin" in (wi_server.capabilities or [])
                and wi_sub.actual_version == wi_sub.desired_version
                and wi_sub.last_reconciled_node_epoch == wi_server.xray_instance_epoch
                and (
                    (wi_server.is_active and wi_server.health_state == ServerHealthState.ONLINE)
                    or (
                        getattr(wi_sub, "provisioning_status", None) == WhiteInternetProvisioningStatus.ACTIVE
                        and (getattr(wi_sub, "actual_version", 0) or 0) > 0
                    )
                )
            )
            if is_wi_node_ready and wi_server is not None:
                wi_links = VlessSubscriptionService.generate_bi_xhttp_links(wi_sub, wi_server)
                all_links.extend(wi_links)
            elif not std_active:
                return web.Response(
                    status=503,
                    text=texts.WL_WEB_UNSYNCED,
                    headers={**common_headers, "Retry-After": "5"},
                )

        if not all_links:
            retry_after = "5" if (wi_active and not std_active) else "60"
            return web.Response(
                status=503,
                text="No servers available",
                headers={**common_headers, "Retry-After": retry_after},
            )

        payload = "\n".join(all_links)
        b64_payload = base64.b64encode(payload.encode("utf-8")).decode("utf-8")

        profile_title = await get_system_setting(
            session,
            "vless_profile_title",
            os.getenv("VLESS_PROFILE_TITLE", constants.DEFAULT_VLESS_PROFILE_TITLE)
            or constants.DEFAULT_VLESS_PROFILE_TITLE,
        )
        if not isinstance(profile_title, str) or not profile_title.strip():
            profile_title = (
                os.getenv("VLESS_PROFILE_TITLE", constants.DEFAULT_VLESS_PROFILE_TITLE)
                or constants.DEFAULT_VLESS_PROFILE_TITLE
            )

        raw_announce_url = await get_system_setting(session, "vless_announce_url", None)
        clean_announce_url = _sanitize_header_url(raw_announce_url)

        first_extra = {}
        if std_servers and isinstance(std_servers[0].extra_data, dict):
            first_extra = std_servers[0].extra_data
        elif wi_server and isinstance(wi_server.extra_data, dict):
            first_extra = wi_server.extra_data

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

        fragmentation_setting = await get_system_setting(session, "vless_fragmentation_enabled", True)
        if isinstance(fragmentation_setting, str):
            fragmentation_enabled = fragmentation_setting.lower() in ("1", "true", "yes", "on")
        else:
            fragmentation_enabled = bool(fragmentation_setting)

        profile_title_base = str(profile_title).strip().replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
        if show_devices:
            suffix = f" [{active_hwid_count}/{effective_limit}]"
            max_base_len = max(0, 25 - len(suffix))
            clean_base = profile_title_base[:max_base_len].rstrip()
            full_title = f"{clean_base}{suffix}"[:25]
        else:
            full_title = profile_title_base[:25]

        b64_title = base64.b64encode(full_title.encode("utf-8")).decode("ascii")
        response_headers = {
            **common_headers,
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Disposition": 'inline; filename="Just1k.txt"; filename*=UTF-8\'\'Just1k.txt',
            "Profile-Update-Interval": _get_vless_update_interval(),
            "Subscription-Userinfo": f"upload={upload_bytes}; download={download_bytes}; total=0; expire={expire_ts}",
            "Device-Limit": str(effective_limit),
            "Device-Active-Count": str(active_hwid_count),
            "Profile-Title": f"base64:{b64_title}",
            "Sort-Order": "none",
            "x-hwid-active": "true",
            "Hide-Url": "1",
            "No-Limit-Enabled": "1",
        }
        routing_scheme = "happ" if is_happ else "incy"

        if fragmentation_enabled:
            response_headers["fragmentation-enable"] = "1"
            response_headers["fragmentation-packets"] = "tlshello"
            response_headers["fragmentation-length"] = "10-30"
            response_headers["fragmentation-interval"] = "10-30"

        if routing_enabled:
            response_headers["Routing"] = VlessSubscriptionService.generate_incy_routing_header(
                profile_name=constants.DEFAULT_VLESS_PROFILE_TITLE,
                direct_ru=bool(std_active),
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
            clean_bot = str(bot_username).strip().lstrip("@") or constants.DEFAULT_FALLBACK_BOT_USERNAME
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

        announce_summary = _format_announce_summary(
            std_active=std_active,
            std_end=std_end,
            wi_active=wi_active,
            wi_end=wi_end,
            wi_available_bytes=wi_available_bytes,
        )
        if announce_summary:
            b64_ann = base64.b64encode(announce_summary.encode("utf-8")).decode("ascii")
            response_headers["Announce"] = f"base64:{b64_ann}"

        if sanitized_announce:
            response_headers["Announce-Url"] = sanitized_announce
        return web.Response(status=200, text=b64_payload, headers=response_headers)


async def vless_subscription_feed_handler(request: web.Request) -> web.Response:
    """Serve unified Base64 subscription feed for /sub/{token}, /sub/access/{token}, /sub/vless/{token}."""
    return await serve_unified_subscription_feed(request, token_source="vless")


async def vless_report_handler(request: web.Request) -> web.Response:
    """Handle emergency contact reports from standard VLESS landing pages."""
    client_ip = get_trusted_client_ip(request)
    allowed_ip, retry_after_ip = _report_ip_limiter.check(client_ip)
    if not allowed_ip:
        return web.json_response(
            {"ok": False, "error": texts.LANDING_REPORT_TOO_MANY_REQUESTS},
            status=429,
            headers={"Retry-After": str(retry_after_ip), "Cache-Control": "no-store"},
        )

    token = request.match_info.get("token", "").strip()
    if not token or len(token) < 16:
        return web.json_response(
            {"ok": False, "error": texts.LANDING_REPORT_INVALID_LINK},
            status=400,
            headers={"Cache-Control": "no-store"},
        )

    allowed_tok, retry_after_tok = _report_token_limiter.check(token)
    if not allowed_tok:
        return web.json_response(
            {"ok": False, "error": texts.LANDING_REPORT_TOO_MANY_REQUESTS},
            status=429,
            headers={"Retry-After": str(retry_after_tok), "Cache-Control": "no-store"},
        )

    try:
        data = await request.json()
    except Exception:
        return web.json_response(
            {"ok": False, "error": texts.LANDING_REPORT_INVALID_REQUEST},
            status=400,
            headers={"Cache-Control": "no-store"},
        )

    if not isinstance(data, dict):
        return web.json_response(
            {"ok": False, "error": texts.LANDING_REPORT_INVALID_FORMAT},
            status=400,
            headers={"Cache-Control": "no-store"},
        )

    contact_raw = data.get("contact")
    message_raw = data.get("message")
    if not isinstance(contact_raw, str) or not isinstance(message_raw, str):
        return web.json_response(
            {"ok": False, "error": texts.LANDING_REPORT_INVALID_FORMAT},
            status=400,
            headers={"Cache-Control": "no-store"},
        )

    contact = contact_raw.strip()
    message = message_raw.strip()

    if len(contact) < 3 or len(contact) > 120:
        return web.json_response(
            {"ok": False, "error": texts.LANDING_REPORT_CONTACT_LENGTH_ERROR},
            status=400,
            headers={"Cache-Control": "no-store"},
        )

    if len(message) < 5 or len(message) > 1000:
        return web.json_response(
            {"ok": False, "error": texts.LANDING_REPORT_MESSAGE_LENGTH_ERROR},
            status=400,
            headers={"Cache-Control": "no-store"},
        )

    contact_escaped = html.escape(contact)
    message_escaped = html.escape(message)

    try:
        async with session_scope() as session:
            vless_sub = await vless_subscription_repo.get_subscription_by_token(session, token)
            if not vless_sub:
                return web.json_response(
                    {"ok": False, "error": texts.LANDING_REPORT_INVALID_LINK},
                    status=404,
                    headers={"Cache-Control": "no-store"},
                )
            user = await users_repo.get_user_by_id(session, vless_sub.user_id)
            if not user or getattr(user, "is_deleted", False):
                return web.json_response(
                    {"ok": False, "error": texts.LANDING_REPORT_INVALID_LINK},
                    status=404,
                    headers={"Cache-Control": "no-store"},
                )

            user_tag = f"@{html.escape(user.username)}" if user.username else "—"
            user_info = f"ID: <code>{user.telegram_id}</code> ({user_tag})"
            dev_limit = await SubscriptionService.get_effective_device_limit(session, user)
            tariff_name = get_tariff_display_name(dev_limit)
            total_active = await vless_subscription_repo.get_active_hwid_count(session, user.id)
            devices_info = f"{total_active} / {dev_limit if dev_limit > 0 else '—'}"
            is_access_active = SubscriptionService.check_vpn_access(user)
            if getattr(user, "financial_hold", False):
                sub_status = "HOLD"
            elif not getattr(vless_sub, "is_active", True) or not is_access_active:
                sub_status = "EXPIRED"
            else:
                sub_status = "ACTIVE"
    except Exception as exc:
        logger.error("Failed to query subscription info for emergency report: %s", exc)
        return web.json_response(
            {"ok": False, "error": texts.LANDING_REPORT_DELIVERY_FAILED},
            status=500,
            headers={"Cache-Control": "no-store"},
        )

    category_name = f"{texts.LANDING_VLESS_PAGE_TITLE} ({tariff_name})" if tariff_name != "—" else texts.LANDING_VLESS_PAGE_TITLE
    admin_text = texts.ADMIN_ALERT_EMERGENCY_SUPPORT.format(
        user_info=user_info,
        category=category_name,
        sub_status=html.escape(sub_status),
        devices_info=devices_info,
        client_ip=html.escape(client_ip),
        contact=contact_escaped,
        message=message_escaped,
    )

    bot = request.app.get("bot")
    delivered = False
    if bot:
        from bot.keyboards.notifications import get_dismiss_alert_keyboard
        from config.settings import get_settings
        from utils.telegram import safe_send_message

        admin_ids = get_settings().ADMIN_IDS
        dismiss_kb = get_dismiss_alert_keyboard("general")
        for aid in admin_ids:
            try:
                msg_id = await safe_send_message(
                    bot, aid, admin_text, reply_markup=dismiss_kb, parse_mode="HTML"
                )
                if msg_id is not None:
                    delivered = True
            except Exception as exc:
                logger.error("Failed to send emergency support alert to admin %s: %s", aid, exc)
    else:
        logger.warning("Emergency support report received, but bot instance not set on app")

    if not delivered:
        return web.json_response(
            {"ok": False, "error": texts.LANDING_REPORT_DELIVERY_FAILED},
            status=502,
            headers={"Cache-Control": "no-store"},
        )

    return web.json_response({"ok": True}, headers={"Cache-Control": "no-store"})


def setup_vless_web_routes(app: web.Application) -> None:
    """Register HTTP subscription feed route for unified and legacy VLESS access."""
    app.router.add_get("/sub/access/{token}", vless_subscription_feed_handler)
    app.router.add_get("/sub/vless/{token}", vless_subscription_feed_handler)
    app.router.add_get("/sub/{token}", vless_subscription_feed_handler)
    app.router.add_post("/sub/access/{token}/report", vless_report_handler)
    app.router.add_post("/sub/vless/{token}/report", vless_report_handler)
    app.router.add_post("/sub/{token}/report", vless_report_handler)
