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

from bot import texts
from bot.texts import get_tariff_display_name
from config import constants
from config.enums import (
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from database.connection import session_scope
from database.models import Server
from database.repositories import (
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
def get_shared_design_tokens_css() -> str:
    """Read shared Mini App design tokens as the single source of truth for all web surfaces."""
    tokens_path = Path(__file__).resolve().parents[2] / "frontend" / "miniapp" / "css" / "tokens.css"
    return tokens_path.read_text(encoding="utf-8")


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
        base.replace("/* {{SHARED_TOKENS_CSS}} */", get_shared_design_tokens_css())
        .replace("/* {{LANDING_CSS}} */", css)
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
    return (
        (_TEMPLATES_DIR / "vless_landing_status.html")
        .read_text(encoding="utf-8")
        .replace("/* {{SHARED_TOKENS_CSS}} */", get_shared_design_tokens_css())
        .replace("{page_title}", "Подключение к сервису")
        .replace("{head_extra}", "")
        .replace(
            "{extra_hint}",
            '<p class="hint-note">Если Telegram не открывается, напишите нам через форму поддержки ниже</p>',
        )
        .replace("{extra_script}", "")
    )


_ip_rate_limiter = HttpRateLimiter(rate_per_minute=60.0, burst=15)
_token_rate_limiter = HttpRateLimiter(rate_per_minute=30.0, burst=10)
_report_ip_limiter = HttpRateLimiter(rate_per_minute=5.0, burst=3)
_report_token_limiter = HttpRateLimiter(rate_per_minute=3.0, burst=2)
_asset_rate_limiter = HttpRateLimiter(rate_per_minute=600.0, burst=150)

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
            if (
                clean
                and clean.startswith(("http://", "https://"))
                and clean.isascii()
                and all(32 < ord(c) < 127 for c in clean)
            ):
                return clean
    return ""


def _sanitize_ascii_header(val: str | None, fallback: str = "", *, max_len: int = 256) -> str:
    """Sanitize plain header value to safe printable ASCII without CR/LF."""
    for candidate in (val, fallback):
        if candidate and isinstance(candidate, str):
            clean = candidate.strip().replace("\r", " ").replace("\n", " ")[:max_len]
            if clean and clean.isascii() and all(32 <= ord(c) < 127 for c in clean):
                return clean
    return ""


def _normalize_per_app_proxy_list(raw: str | None, *, max_len: int = 2000) -> str:
    """Normalize newline/whitespace/comma-separated app entries into a clean ASCII CSV string."""
    if not raw or not isinstance(raw, str) or max_len <= 0:
        return ""
    seen: set[str] = set()
    selected: list[str] = []
    total_len = 0
    for item in re.split(r"[\s,]+", raw.strip()):
        if not item or item in seen:
            continue
        if not (item.isascii() and all(32 < ord(c) < 127 for c in item)):
            continue
        added_len = len(item) + (1 if selected else 0)
        if total_len + added_len > max_len:
            break
        seen.add(item)
        selected.append(item)
        total_len += added_len
    return ",".join(selected)


def _as_bool_setting(val: object, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return bool(val)


async def _load_subscription_ui_settings(session) -> dict[str, object]:
    """Load global INCY & Happ subscription customization settings from SystemSetting."""
    default_interval = _get_vless_update_interval()
    interval_raw = await get_system_setting(session, "vless_profile_update_interval", default_interval)
    interval_val = str(interval_raw).strip() if interval_raw is not None else default_interval
    if not (interval_val.isdigit() and 1 <= int(interval_val) <= 720):
        interval_val = default_interval

    sort_order = str(await get_system_setting(session, "vless_sort_order", "none") or "none").strip().lower()
    if sort_order not in {"none", "ping", "name"}:
        sort_order = "none"

    no_limit_mode = str(await get_system_setting(session, "vless_no_limit_mode", "all") or "all").strip().lower()
    if no_limit_mode not in {"off", "all", "xhttp"}:
        no_limit_mode = "all"

    per_app_mode = str(await get_system_setting(session, "vless_per_app_proxy_mode", "bypass") or "bypass").strip().lower()
    if per_app_mode not in {"bypass", "proxy"}:
        per_app_mode = "bypass"

    return {
        "profile_update_interval": interval_val,
        "support_url": str(await get_system_setting(session, "vless_support_url", "") or "").strip(),
        "web_page_url": str(await get_system_setting(session, "vless_web_page_url", "") or "").strip(),
        "sort_order": sort_order,
        "hide_url": _as_bool_setting(await get_system_setting(session, "vless_hide_url", "true"), True),
        "hide_check": _as_bool_setting(await get_system_setting(session, "vless_hide_check", "false"), False),
        "fragmentation_enabled": _as_bool_setting(await get_system_setting(session, "vless_fragmentation_enabled", "true"), True),
        "fragmentation_packets": str(await get_system_setting(session, "vless_fragmentation_packets", "tlshello") or "tlshello").strip(),
        "fragmentation_length": str(await get_system_setting(session, "vless_fragmentation_length", "10-30") or "10-30").strip(),
        "fragmentation_interval": str(await get_system_setting(session, "vless_fragmentation_interval", "10-30") or "10-30").strip(),
        "no_limit_mode": no_limit_mode,
        "dns_resolve_enabled": _as_bool_setting(await get_system_setting(session, "vless_dns_resolve_enabled", "true"), True),
        "dns_resolve_domain": str(await get_system_setting(session, "vless_dns_resolve_domain", "https://common.dot.dns.yandex.net/dns-query") or "https://common.dot.dns.yandex.net/dns-query").strip(),
        "dns_resolve_ip": str(await get_system_setting(session, "vless_dns_resolve_ip", "77.88.8.8") or "77.88.8.8").strip(),
        "per_app_proxy_enabled": _as_bool_setting(await get_system_setting(session, "vless_per_app_proxy_enabled", "false"), False),
        "per_app_proxy_mode": per_app_mode,
        "per_app_proxy_list": str(await get_system_setting(session, "vless_per_app_proxy_list", "") or "").strip(),
    }


def _apply_no_limit_headers(headers: dict[str, str], no_limit_mode: str, *, is_happ: bool) -> None:
    """Apply No-Limit-Enabled / no-limit-xhttp-enabled without ever sending both simultaneously."""
    if no_limit_mode == "all":
        headers["No-Limit-Enabled"] = "1"
    elif no_limit_mode == "xhttp":
        if is_happ:
            headers["no-limit-xhttp-enabled"] = "1"
        else:
            headers["No-Limit-Enabled"] = "1"


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




def _format_gb_compact(num_bytes: int) -> str:
    gb = max(0, num_bytes) / (1024**3)
    return f"{int(round(gb))}" if abs(gb - round(gb)) < 0.05 else f"{gb:.1f}"


def _format_days_left(end_dt, cur_now) -> int | None:
    if not end_dt or not hasattr(end_dt, "timestamp"):
        return None
    diff_sec = end_dt.timestamp() - cur_now.timestamp()
    return max(0, int(diff_sec // 86400))


def _format_announce_summary(
    *,
    std_active: bool,
    std_end,
    wi_active: bool,
    wi_end,
    wi_available_bytes: int,
    wi_total_bytes: int = 0,
    show_devices: bool = False,
    std_devices_active: int = 0,
    std_devices_limit: int = 0,
    wi_devices_active: int = 0,
    wi_devices_limit: int = 0,
    now_dt=None,
) -> str:
    """Build 2-line stop-word-free Announce summary with days left, used/total GB, and per-tier devices."""
    cur_now = now_dt or now_utc()
    lines: list[str] = []
    if std_active:
        dev_part = f" • устр. {std_devices_active}/{std_devices_limit}" if show_devices and std_devices_limit > 0 else ""
        if std_end and is_permanent_subscription(std_end):
            lines.append(f"Стандарт: без срока • ∞{dev_part}")
        else:
            std_days = _format_days_left(std_end, cur_now)
            if std_days is not None:
                lines.append(f"Стандарт: {std_days} дн. • ∞{dev_part}")
            else:
                lines.append(f"Стандарт: активен • ∞{dev_part}")

    if wi_active:
        total_b = max(0, wi_total_bytes)
        used_b = max(0, total_b - max(0, wi_available_bytes)) if total_b > 0 else 0
        gb_part = f"{_format_gb_compact(used_b)}/{_format_gb_compact(total_b)} ГБ" if total_b > 0 else f"остаток {_format_gb_compact(wi_available_bytes)} ГБ"
        dev_part = f" • устр. {wi_devices_active}/{wi_devices_limit}" if show_devices and wi_devices_limit > 0 else ""
        wi_days = _format_days_left(wi_end, cur_now)
        if wi_days is not None:
            lines.append(f"Белый интернет: {wi_days} дн. • {gb_part}{dev_part}")
        else:
            lines.append(f"Белый интернет: {gb_part}{dev_part}")

    return "\n".join(lines)[:200]


def _make_inline_stub(text: str, description: str = "") -> str:
    """Build a non-routable stub VLESS URI with optional base64 serverDescription before #."""
    query = "encryption=none&security=none&type=tcp"
    clean_desc = description.strip()[:30]
    if clean_desc:
        desc_b64 = base64.b64encode(clean_desc.encode("utf-8")).decode("ascii")
        query = f"{query}&serverDescription={desc_b64}"
    return f"vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443?{query}#{urllib.parse.quote(text)}"


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
    ui_settings: dict[str, object] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> web.Response:
    action_hint = f"Откройте @{bot_username}" if bot_username else "Откройте бота"
    notice_link = _make_inline_stub(title_text, description=action_hint)
    b64_payload = base64.b64encode(notice_link.encode("utf-8")).decode("utf-8")
    b64_title = base64.b64encode(title_text.encode("utf-8")).decode("utf-8")
    b64_announce = base64.b64encode(notice_text.strip()[:200].encode("utf-8")).decode("ascii")

    cfg = ui_settings or {}
    interval_val = str(cfg.get("profile_update_interval") or _get_vless_update_interval())
    hide_url_val = "1" if cfg.get("hide_url", True) else "0"

    headers = {
        **common_headers,
        "Content-Type": "text/plain; charset=utf-8",
        "Profile-Update-Interval": interval_val,
        "Subscription-Userinfo": f"upload={upload_bytes}; download={download_bytes}; total=0; expire={expire_ts}",
        "Profile-Title": f"base64:{b64_title}",
        "Announce": f"base64:{b64_announce}",
        "Hide-Url": hide_url_val,
    }
    _apply_no_limit_headers(headers, str(cfg.get("no_limit_mode") or "all"), is_happ=is_happ)

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
        is_std_access = SubscriptionService.check_vpn_access(user, vless_sub=vless_sub)
        if vless_sub and vless_sub.is_active and (is_financial_hold or not is_std_access):
            vless_sub.is_active = False
            vless_sub.version = (getattr(vless_sub, "version", 1) or 1) + 1
            session.add(vless_sub)
            VlessSubscriptionService.ensure_synced_background(user.id, session=session)

        std_active = bool(vless_sub and vless_sub.is_active and not is_financial_hold and is_std_access)
        std_end = getattr(vless_sub, "expires_at", None) if (std_active and vless_sub) else None

        wi_total_quota = 0
        wi_available_bytes = 0
        wi_is_pending = False
        wi_is_expired = False
        wi_is_exhausted = False
        wi_is_disabled = False
        if wi_sub is not None:
            wi_total_quota, wi_available_bytes = _compute_wi_traffic_stats(wi_sub)
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
        if wi_active and not std_active:
            upload_bytes = 0
            download_bytes = max(
                0,
                int(getattr(wi_sub, "traffic_used_bytes", 0) or 0)
                - int(getattr(wi_sub, "traffic_overage_bytes", 0) or 0),
            )
        elif std_active and not wi_active:
            upload_bytes = std_up
            download_bytes = std_down
        else:
            upload_bytes = std_up + wi_up
            download_bytes = std_down + wi_down

        std_ts = int(std_end.timestamp()) if std_end and hasattr(std_end, "timestamp") else 0
        wi_ts = int(wi_end.timestamp()) if wi_end and hasattr(wi_end, "timestamp") else 0
        active_ts = [ts for ts in (std_ts, wi_ts) if ts > 0]
        expire_ts = min(active_ts) if active_ts else 0

        bot_username = await _resolve_bot_username(request)
        default_bot_url = f"https://t.me/{bot_username}"
        ui_settings = await _load_subscription_ui_settings(session)
        support_url = _sanitize_header_url(
            str(ui_settings.get("support_url") or ""),
            fallback=_resolve_support_url({}, default_bot_url),
        )
        channel_url = _sanitize_header_url(
            str(ui_settings.get("web_page_url") or ""),
            fallback=_resolve_channel_url({}, default_bot_url),
        )

        # 1. Neither option is active -> return pending or expired/inactive stub
        if not std_active and not wi_active:
            if wi_is_pending:
                if not raw_hwid and _is_browser_request(request):
                    from bot.handlers.white_internet_web import _render_white_internet_pending_landing_html
                    html_body = _render_white_internet_pending_landing_html(bot_username=bot_username)
                    landing_headers = {
                        **common_headers,
                        "Content-Type": "text/html; charset=utf-8",
                        "Content-Security-Policy": "default-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' data:;",
                    }
                    return web.Response(status=200, text=html_body, headers=landing_headers)
                return _build_unified_notice_response(
                    title_text="⏳ Идёт настройка подключения",
                    notice_text=texts.WL_WEB_PENDING,
                    common_headers=common_headers,
                    bot_username=bot_username,
                    support_url=support_url,
                    channel_url=channel_url,
                    announce_url=channel_url,
                    upload_bytes=upload_bytes,
                    download_bytes=download_bytes,
                    expire_ts=expire_ts,
                    is_happ=is_happ,
                    ui_settings=ui_settings,
                )

            if not raw_hwid and _is_browser_request(request):
                if is_financial_hold:
                    badge = texts.LANDING_INACTIVE_BADGE_PAUSED
                    title = texts.LANDING_INACTIVE_TITLE_DEFAULT
                    desc = texts.LANDING_INACTIVE_DESC_DEFAULT
                elif wi_sub is not None and wi_is_exhausted and not wi_is_expired:
                    badge = texts.LANDING_INACTIVE_BADGE_EXHAUSTED
                    title = texts.LANDING_INACTIVE_TITLE_EXHAUSTED
                    desc = texts.LANDING_INACTIVE_DESC_EXHAUSTED
                elif wi_sub is not None and wi_is_disabled and not std_active:
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
                    "Content-Security-Policy": "default-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' data:;",
                }
                return web.Response(status=200, text=html_body, headers=landing_headers)

            fallback_exp_ts = 0
            if vless_sub and getattr(vless_sub, "expires_at", None):
                fallback_exp_ts = max(fallback_exp_ts, int(vless_sub.expires_at.timestamp()))
            if wi_sub and getattr(wi_sub, "expires_at", None):
                fallback_exp_ts = max(fallback_exp_ts, int(wi_sub.expires_at.timestamp()))

            if wi_sub is not None and wi_is_exhausted and not wi_is_expired and not std_active:
                stub_title = texts.WL_FEED_EXHAUSTED_TITLE
                stub_notice = texts.WL_FEED_EXHAUSTED_NOTICE.format(bot_username=bot_username)
            elif wi_sub is not None and wi_is_disabled and not std_active:
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
                ui_settings=ui_settings,
            )

        # 2. Active subscription(s), but no HWID provided -> landing page for browser or 403 for app
        wi_has_period = bool(
            wi_sub is not None
            and not is_financial_hold
            and wi_sub.status
            in (
                WhiteInternetStatus.ACTIVE,
                WhiteInternetStatus.PENDING,
                WhiteInternetStatus.EXHAUSTED,
            )
            and wi_sub.expires_at
            and wi_sub.expires_at > now
        )
        std_limit = await SubscriptionService.get_effective_device_limit(session, user) if std_active else 0
        wi_limit = max(1, getattr(wi_sub, "device_limit", 1) or 1) if wi_active else 0
        wi_slot_limit = max(1, getattr(wi_sub, "device_limit", 1) or 1) if wi_has_period else 0
        max_devices = max(std_limit + wi_slot_limit, 1)

        if not raw_hwid:
            if _is_browser_request(request):
                landing_std_limit = max(1, std_limit) if std_active else max_devices
                tariff_clean = await _resolve_clean_tariff_name(session, user, landing_std_limit) if std_active else ""
                if std_active:
                    if landing_std_limit > 5:
                        tier = "pro"
                    elif landing_std_limit > 2:
                        tier = "family"
                    else:
                        tier = "basic"
                    prefix = tariff_clean.upper() if tariff_clean else tier.upper()
                    device_str = format_plural(landing_std_limit, texts.LANDING_DEVICE_FORMS_LABELS)
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
                    device_limit=landing_std_limit,
                )
                landing_headers = {
                    **common_headers,
                    "Content-Type": "text/html; charset=utf-8",
                    "Content-Security-Policy": "default-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' data:;",
                }
                return web.Response(status=200, text=html_body, headers=landing_headers)

            headers = dict(common_headers)
            headers["x-hwid-required"] = "true"
            return web.Response(status=403, text="HWID required", headers=headers)

        # 3. Unified HWID registration + priority-based BI slot assignment
        device_meta = extract_device_metadata(request.headers)
        allowed_hwid, is_new_device, active_hwid_count, effective_limit, block_reason = await user_devices_repo.register_user_device(
            session, user.id, raw_hwid, max_devices, device_info=device_meta
        )
        if not allowed_hwid:
            if block_reason == "churn_limit":
                churn_cap = user_devices_repo.calculate_daily_churn_limit(effective_limit)
                limit_msg = texts.VLESS_FEED_CHURN_LIMIT_NOTICE.format(
                    limit=churn_cap,
                    bot_username=bot_username,
                )
            else:
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
                ui_settings=ui_settings,
                extra_headers={
                    "Device-Limit-Exceeded": "1",
                    "Device-Limit": str(effective_limit),
                    "Device-Active-Count": str(active_hwid_count),
                    "x-hwid-max-devices-reached": "true",
                    "x-hwid-limit": "true",
                    "x-hwid-active": str(active_hwid_count),
                },
            )

        devices_list = await user_devices_repo.list_user_devices(
            session,
            user.id,
            wi_device_limit=wi_limit if wi_active else 0,
            std_device_limit=std_limit if std_active else 0,
        )

        current_dev_info = next((d for d in devices_list if d.get("hwid") == raw_hwid), None)
        is_std_assigned = bool(std_active and current_dev_info and current_dev_info.get("is_std_assigned"))
        is_bi_assigned = bool(wi_active and current_dev_info and current_dev_info.get("is_bi_assigned"))
        is_bi_explicitly_disabled = bool(
            current_dev_info is not None and int(current_dev_info.get("bi_priority") or 0) < 0
        )

        # 4. Generate links for active options (with independent per-option stubs)
        all_links: list[str] = []
        std_servers: list[Server] = []
        exit_nodes: list[Server] = []
        current_dev = await user_devices_repo.get_user_device_by_hwid(session, user.id, raw_hwid)
        device_client_uuid = getattr(current_dev, "device_uuid", None) if current_dev else None

        if (std_active and is_std_assigned and vless_sub is not None) or (wi_active and is_bi_assigned and wi_sub is not None):
            exit_nodes = await VlessSubscriptionService.get_eligible_exit_nodes(session)

        if std_active and vless_sub is not None:
            if is_std_assigned:
                std_servers = await VlessSubscriptionService.get_eligible_vless_servers(session)
                std_links = VlessSubscriptionService.generate_vless_links(
                    vless_sub,
                    std_servers,
                    exit_nodes=exit_nodes,
                    client_uuid=device_client_uuid,
                )
                all_links.extend(std_links)

                if is_new_device:
                    vless_sub.version = (vless_sub.version or 1) + 1
                    await session.flush()

                last_synced_version = getattr(vless_sub, "last_synced_version", None)
                current_version = getattr(vless_sub, "version", 1) or 1
                needs_sync = (
                    is_new_device
                    or vless_sub.last_synced_at is None
                    or (now - vless_sub.last_synced_at).total_seconds() > 300
                    or bool(getattr(vless_sub, "pending_revoked_uuids", None))
                    or (last_synced_version is not None and last_synced_version < current_version)
                )
                if needs_sync:
                    VlessSubscriptionService.ensure_synced_background(user.id, session=session)
            elif not (wi_active and is_bi_assigned):
                all_links.append(
                    _make_inline_stub(
                        texts.FEED_STUB_STD_DEVICE_LIMIT.format(limit=std_limit),
                        "Управление в кабинете",
                    )
                )
        elif vless_sub and getattr(vless_sub, "expires_at", None) is not None and wi_active:
            all_links.append(_make_inline_stub(texts.FEED_STUB_STD_EXPIRED, "Управление в кабинете"))

        if wi_active and wi_sub is not None:
            if is_bi_assigned:
                from database.repositories import servers_repo

                wi_hubs = await servers_repo.get_wi_capable_ru_hubs(session)
                ready_hubs = []
                for hub in wi_hubs:
                    if constants.WHITE_INTERNET_CAPABILITY not in (hub.capabilities or []):
                        continue
                    sync_entry = (
                        wi_sub.node_sync_state.get(str(hub.id))
                        if isinstance(getattr(wi_sub, "node_sync_state", None), dict)
                        else None
                    )
                    if isinstance(sync_entry, dict):
                        epoch_ok = (
                            not hub.xray_instance_epoch
                            or sync_entry.get("epoch") == hub.xray_instance_epoch
                        )
                        if (
                            (
                                sync_entry.get("version") == wi_sub.desired_version
                                or (
                                    wi_sub.status == WhiteInternetStatus.ACTIVE
                                    and (sync_entry.get("version") or 0) > 0
                                )
                            )
                            and sync_entry.get("active", True) is True
                            and epoch_ok
                        ):
                            ready_hubs.append(hub)
                            continue
                    if (
                        not getattr(wi_sub, "node_sync_state", None)
                        and getattr(wi_sub, "provisioning_status", None) == WhiteInternetProvisioningStatus.ACTIVE
                        and (getattr(wi_sub, "actual_version", 0) or 0) > 0
                    ):
                        ready_hubs.append(hub)
                if ready_hubs:
                    ready_hubs.sort(
                        key=lambda s: (
                            servers_repo.has_server_ingress_problem(s) or servers_repo.has_server_cdn_problem(s),
                            servers_repo.get_server_load_score(s),
                            int(s.id or 0),
                        )
                    )
                    cdn_label_mode = str(await get_system_setting(session, "bi_cdn_label_mode", "name") or "name")
                    cdn_yandex_label = str(await get_system_setting(session, "bi_cdn_yandex_label", "Yandex") or "Yandex")
                    cdn_turboflare_label = str(await get_system_setting(session, "bi_cdn_turboflare_label", "Turboflare") or "Turboflare")
                    seen_bi_fragments: set[str] = set()
                    for wi_server in ready_hubs:
                        wi_links = VlessSubscriptionService.generate_bi_xhttp_links(
                            wi_sub,
                            wi_server,
                            exit_nodes=exit_nodes,
                            cdn_label_mode=cdn_label_mode,
                            cdn_yandex_label=cdn_yandex_label,
                            cdn_turboflare_label=cdn_turboflare_label,
                            client_uuid=device_client_uuid,
                        )
                        for lk in wi_links:
                            frag = lk.split("#", 1)[-1] if "#" in lk else lk
                            if frag not in seen_bi_fragments:
                                seen_bi_fragments.add(frag)
                                all_links.append(lk)
                else:
                    all_links.append(_make_inline_stub(texts.FEED_STUB_WI_PENDING, "Нажмите ⟳ через 1 минуту"))
                if is_new_device and device_client_uuid and is_bi_assigned:
                    wi_sub.desired_version = (wi_sub.desired_version or 1) + 1
                    wi_sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
                    await session.flush()
                    if wi_hubs:
                        from services.xray_node_client import XrayNodeClient

                        targets_snapshot = [
                            (h.api_url, h.api_key, h.xray_instance_epoch)
                            for h in wi_hubs
                            if h.api_url and h.api_key
                        ]
                        ver_snap = int(wi_sub.desired_version or 1)

                        async def _provision_new_wi_device(
                            targets: list[tuple[str, str, str | None]],
                            dev_uuid: str,
                            ver: int,
                        ) -> None:
                            async with XrayNodeClient(timeout=5.0) as client:
                                for srv_url, srv_key, epoch in targets:
                                    try:
                                        await client.sync_client(
                                            srv_url,
                                            srv_key,
                                            client_uuid=dev_uuid,
                                            is_active=True,
                                            version=ver,
                                            expected_node_epoch=epoch,
                                            service="white_internet",
                                        )
                                    except Exception as exc:
                                        logger.warning("Background WI device provision failed for %s: %s", dev_uuid, exc)

                        if targets_snapshot and device_client_uuid:
                            from database.connection import queue_post_commit_task

                            queue_post_commit_task(
                                session,
                                lambda: _provision_new_wi_device(targets_snapshot, device_client_uuid, ver_snap),
                            )
            elif not (is_bi_explicitly_disabled and std_active and is_std_assigned):
                all_links.append(
                    _make_inline_stub(
                        texts.FEED_STUB_WI_DEVICE_LIMIT.format(limit=wi_limit),
                        "Управление в кабинете",
                    )
                )
        elif wi_sub is not None and std_active:
            if wi_is_pending:
                all_links.append(_make_inline_stub(texts.FEED_STUB_WI_PENDING, "Нажмите ⟳ через 1 минуту"))
            elif wi_is_exhausted and not wi_is_expired:
                all_links.append(_make_inline_stub(texts.FEED_STUB_WI_EXHAUSTED, "Докупите ГБ в кабинете"))
            elif wi_is_disabled:
                all_links.append(_make_inline_stub(texts.FEED_STUB_WI_DISABLED, "Поддержка в кабинете"))
            else:
                all_links.append(_make_inline_stub(texts.FEED_STUB_WI_EXPIRED, "Продлите в кабинете"))

        no_servers_mode = False
        if not all_links:
            no_servers_mode = True
            placeholder_label = (
                texts.FEED_STUB_PENDING_SETUP
                if (wi_active and not std_active)
                else texts.FEED_STUB_NO_LOCATIONS
            )
            placeholder_desc = "Нажмите ⟳ через 1 минуту" if (wi_active and not std_active) else "Идут работы, нажмите ⟳ позже"
            all_links.append(_make_inline_stub(placeholder_label, placeholder_desc))

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

        show_devices_setting = await get_system_setting(session, "vless_profile_show_devices", False)
        show_devices = _as_bool_setting(show_devices_setting, False)

        profile_title_base = str(profile_title).strip().replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
        full_title = profile_title_base[:25]

        userinfo_total = wi_total_quota if (not std_active and wi_active and wi_total_quota > 0) else 0
        b64_title = base64.b64encode(full_title.encode("utf-8")).decode("ascii")
        sort_order = str(ui_settings.get("sort_order") or "none")
        happ_sort_map = {"none": "without", "ping": "ping", "name": "alphabet"}

        response_headers = {
            **common_headers,
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Disposition": 'inline; filename="Just1k.txt"; filename*=UTF-8\'\'Just1k.txt',
            "Profile-Update-Interval": str(ui_settings.get("profile_update_interval") or _get_vless_update_interval()),
            "Subscription-Userinfo": f"upload={upload_bytes}; download={download_bytes}; total={userinfo_total}; expire={expire_ts}",
            "Device-Limit": str(effective_limit),
            "Device-Active-Count": str(active_hwid_count),
            "Profile-Title": f"base64:{b64_title}",
            "Sort-Order": sort_order,
            "x-hwid-active": "true",
            "Hide-Url": "1" if ui_settings.get("hide_url", True) else "0",
        }
        if ui_settings.get("hide_check"):
            response_headers["Hide-Check"] = "1"
        _apply_no_limit_headers(response_headers, str(ui_settings.get("no_limit_mode") or "all"), is_happ=is_happ)

        if ui_settings.get("fragmentation_enabled", True):
            response_headers["fragmentation-enable"] = "1"
            response_headers["fragmentation-packets"] = _sanitize_ascii_header(
                str(ui_settings.get("fragmentation_packets") or ""), "tlshello", max_len=64
            )
            response_headers["fragmentation-length"] = _sanitize_ascii_header(
                str(ui_settings.get("fragmentation_length") or ""), "10-30", max_len=32
            )
            response_headers["fragmentation-interval"] = _sanitize_ascii_header(
                str(ui_settings.get("fragmentation_interval") or ""), "10-30", max_len=32
            )

        if ui_settings.get("dns_resolve_enabled"):
            dns_dom = _sanitize_ascii_header(str(ui_settings.get("dns_resolve_domain") or ""), max_len=253)
            dns_ip = _sanitize_ascii_header(str(ui_settings.get("dns_resolve_ip") or ""), max_len=64)
            if dns_dom and dns_ip:
                response_headers["Server-Address-Resolve-Enable"] = "1"
                response_headers["Server-Address-Resolve-Dns-Domain"] = dns_dom
                response_headers["Server-Address-Resolve-Dns-Ip"] = dns_ip

        if ui_settings.get("per_app_proxy_enabled"):
            response_headers["Per-App-Proxy-Enable"] = "1"
            response_headers["Per-App-Proxy-Mode"] = str(ui_settings.get("per_app_proxy_mode") or "bypass")
            normalized_app_list = _normalize_per_app_proxy_list(
                str(ui_settings.get("per_app_proxy_list") or "")
            )
            app_list = _sanitize_ascii_header(normalized_app_list, max_len=2000)
            if app_list:
                response_headers["Per-App-Proxy-List"] = app_list

        response_headers["Routing"] = "off"
        if is_happ:
            response_headers["Routing-Enable"] = "0"
            response_headers["subscriptions-sort-type"] = happ_sort_map.get(sort_order, "without")

        sanitized_support = _sanitize_header_url(str(ui_settings.get("support_url") or ""), fallback=support_url)
        sanitized_channel = _sanitize_header_url(channel_url)
        sanitized_web_page = _sanitize_header_url(str(ui_settings.get("web_page_url") or ""), fallback=sanitized_channel)
        sanitized_announce = _sanitize_header_url(clean_announce_url, fallback=sanitized_channel)

        if sanitized_support:
            response_headers["Support-Url"] = sanitized_support
        if sanitized_web_page:
            response_headers["Profile-Web-Page-Url"] = sanitized_web_page
        if sanitized_channel:
            response_headers["Channel-Url"] = sanitized_channel

        raw_announce = await get_system_setting(session, "vless_announce", None)
        if no_servers_mode:
            announce_summary = (
                "Идёт настройка подключения — обновите подписку через минуту."
                if (wi_active and not std_active)
                else "Локации временно недоступны — ведутся работы. Попробуйте обновить позже или напишите в поддержку."
            )
        elif isinstance(raw_announce, str) and raw_announce.strip():
            announce_summary = raw_announce.strip()
        else:
            std_devices_active = sum(1 for d in devices_list if d.get("is_std_assigned")) if std_active else 0
            wi_devices_active = sum(1 for d in devices_list if d.get("is_bi_assigned")) if wi_active else 0
            announce_summary = _format_announce_summary(
                std_active=std_active,
                std_end=std_end,
                wi_active=wi_active,
                wi_end=wi_end,
                wi_available_bytes=wi_available_bytes,
                wi_total_bytes=wi_total_quota,
                show_devices=show_devices,
                std_devices_active=std_devices_active,
                std_devices_limit=std_limit,
                wi_devices_active=wi_devices_active,
                wi_devices_limit=wi_limit,
                now_dt=now,
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
            from database.repositories import white_internet_repo

            vless_sub = await vless_subscription_repo.get_subscription_by_token(session, token)
            wi_sub = None
            if not vless_sub:
                wi_sub = await white_internet_repo.get_subscription_by_token(session, token)
            target_sub = vless_sub or wi_sub
            if not target_sub:
                return web.json_response(
                    {"ok": False, "error": texts.LANDING_REPORT_INVALID_LINK},
                    status=404,
                    headers={"Cache-Control": "no-store"},
                )
            user = await users_repo.get_user_by_id(session, target_sub.user_id)
            if not user or getattr(user, "is_deleted", False):
                return web.json_response(
                    {"ok": False, "error": texts.LANDING_REPORT_INVALID_LINK},
                    status=404,
                    headers={"Cache-Control": "no-store"},
                )
            target_user_id = user.id

            user_tag = f"@{html.escape(user.username)}" if user.username else "—"
            user_info = f"ID: <code>{user.telegram_id}</code> ({user_tag})"
            dev_limit = await SubscriptionService.get_effective_device_limit(session, user)
            tariff_name = get_tariff_display_name(dev_limit)
            slot_usage = await SubscriptionService.get_device_slot_usage(
                session, user, vless_sub=vless_sub, wi_sub=wi_sub
            )
            total_active = int(slot_usage["total_devices"])
            total_limit = int(slot_usage["total_limit"])
            if slot_usage["std_active"] and slot_usage["wi_has_period"]:
                devices_info = (
                    f"{total_active} / {total_limit} "
                    f"(Стандарт: {slot_usage['std_assigned_count']}/{slot_usage['std_limit']}, "
                    f"БИ: {slot_usage['bi_assigned_count']}/{slot_usage['wi_slot_limit']})"
                )
            else:
                eff_limit = total_limit if total_limit > 0 else dev_limit
                devices_info = f"{total_active} / {eff_limit if eff_limit > 0 else '—'}"
            is_access_active = SubscriptionService.check_vpn_access(user, vless_sub=vless_sub)
            if getattr(user, "financial_hold", False):
                sub_status = "HOLD"
            elif wi_sub is not None and not is_access_active:
                sub_status = str(getattr(wi_sub, "status", "EXPIRED") or "EXPIRED").upper()
            elif (vless_sub and not getattr(vless_sub, "is_active", True)) or not is_access_active:
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
        from bot.keyboards.notifications import get_admin_user_keyboard
        from config.settings import get_settings
        from utils.telegram import safe_send_message

        admin_ids = get_settings().ADMIN_IDS
        reply_markup = get_admin_user_keyboard(target_user_id)
        for aid in admin_ids:
            try:
                msg_id = await safe_send_message(
                    bot,
                    aid,
                    admin_text,
                    reply_markup=reply_markup,
                    parse_mode="HTML",
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


async def vless_guide_video_handler(request: web.Request) -> web.Response:
    """Serve the iOS region-change video guide with HTTP Range support and long cache."""
    client_ip = get_trusted_client_ip(request)
    allowed, retry_after = _asset_rate_limiter.check(client_ip)
    if not allowed:
        return web.Response(
            status=429,
            text="Too Many Requests",
            headers={"Retry-After": str(retry_after)},
        )
    video_path = (_TEMPLATES_DIR / "vless_landing" / "assets" / "happ_guide.mp4").resolve()
    if not video_path.is_file():
        return web.Response(status=404, text="Not Found")
    return web.FileResponse(
        video_path,
        headers={
            "Content-Type": "video/mp4",
            "Cache-Control": "public, max-age=604800",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )


def setup_vless_web_routes(app: web.Application) -> None:
    """Register HTTP subscription feed route for unified and legacy VLESS access."""
    app.router.add_get("/sub/assets/happ_guide.mp4", vless_guide_video_handler)
    app.router.add_get("/sub/access/{token}", vless_subscription_feed_handler)
    app.router.add_get("/sub/vless/{token}", vless_subscription_feed_handler)
    app.router.add_get("/sub/{token}", vless_subscription_feed_handler)
    app.router.add_post("/sub/access/{token}/report", vless_report_handler)
    app.router.add_post("/sub/vless/{token}/report", vless_report_handler)
    app.router.add_post("/sub/{token}/report", vless_report_handler)
