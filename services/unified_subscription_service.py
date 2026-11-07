"""Unified Subscription Service for Just1k v2.

Integrates Standard access (Direct VLESS TLS) and White Internet (VLESS XHTTP via CDN)
into a single subscription URL (/sub/{token}) with:
- Dynamic dual-protocol profile generation.
- Independent countdown timers per service.
- Priority device slot queue (Section 7.1 of just1kbotv2.md).
- Notice nodes on BI exhaustion (0 GB) or BI device limit without interrupting Standard access.
- Strict in-country RU egress via Russian Hub origin.
- All titles, banners, intervals, and routing rules dynamically loaded from SystemSetting.
"""

from __future__ import annotations

import base64
import html
import json
import logging
import os
import re
import urllib.parse

from aiohttp import web
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from config.constants import (
    DEFAULT_FALLBACK_BOT_USERNAME,
    DEFAULT_WHITE_INTERNET_PATH,
    WHITE_INTERNET_BASE_TRAFFIC_BYTES,
)
from config.enums import (
    ServerHealthState,
    WhiteInternetStatus,
)
from database.models import Server, User, VlessSubscription, WhiteInternetSubscription
from database.repositories import (
    users_repo,
    vless_subscription_repo,
    white_internet_repo,
)
from database.repositories.system_settings_repo import get_system_setting
from services.subscription import SubscriptionService
from services.vless_subscription_service import VlessSubscriptionService
from services.white_internet_service import WhiteInternetService
from utils.client_parser import extract_device_metadata, parse_client_from_user_agent
from utils.datetime_helpers import now_utc
from utils.http_rate_limiter import HttpRateLimiter, get_trusted_client_ip
from utils.incy_routing import generate_incy_routing_header
from utils.security import normalize_public_domain

logger = logging.getLogger(__name__)

HWID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-:]{3,128}$")

_ip_rate_limiter = HttpRateLimiter(rate_per_minute=60.0, burst=15)
_token_rate_limiter = HttpRateLimiter(rate_per_minute=30.0, burst=10)

_CACHED_LANDING_HTML: str | None = None
_CACHED_LANDING_INACTIVE_HTML: str | None = None


def _get_landing_templates() -> tuple[str, str]:
    global _CACHED_LANDING_HTML, _CACHED_LANDING_INACTIVE_HTML
    if _CACHED_LANDING_HTML is not None and _CACHED_LANDING_INACTIVE_HTML is not None:
        return _CACHED_LANDING_HTML, _CACHED_LANDING_INACTIVE_HTML

    from bot.handlers.vless_web import load_vless_landing_active_html

    _CACHED_LANDING_HTML = load_vless_landing_active_html()
    templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bot", "templates")
    inactive_path = os.path.join(templates_dir, "vless_landing_inactive.html")
    with open(inactive_path, "r", encoding="utf-8") as f:
        _CACHED_LANDING_INACTIVE_HTML = f.read()

    return _CACHED_LANDING_HTML, _CACHED_LANDING_INACTIVE_HTML


def _sanitize_header_url(url: str | None, fallback: str = "") -> str:
    """Sanitize URL for safe usage in HTTP headers without forbidden control chars."""
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
        "happ",
        "incy",
        "streisand",
        "v2box",
        "shadowrocket",
        "loon",
        "surge",
        "quantumult",
        "flclash",
        "husi",
        "karing",
        "nekobox",
        "matsurirunner",
        "sagernet",
    )
    if any(tok in ua for tok in api_tokens):
        return False

    sec_fetch_mode = request.headers.get("Sec-Fetch-Mode", "").lower()
    if sec_fetch_mode in ("navigate", "nested-navigate"):
        return True

    accept = request.headers.get("Accept", "").lower()
    if "text/html" in accept or "application/xhtml+xml" in accept:
        return True

    browser_markers = ("mozilla/", "chrome/", "safari/", "applewebkit/", "edge/", "firefox/")
    return any(marker in ua for marker in browser_markers)


def build_service_notice_link(notice_text: str) -> str:
    """Generate a zero-endpoint VLESS notice node to display clean status messages in client UI."""
    quoted = urllib.parse.quote(notice_text.strip())
    return (
        f"vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443"
        f"?encryption=none&security=none#{quoted}"
    )


class UnifiedSubscriptionService:
    """Central engine for generating unified v2 multi-protocol subscription profiles."""

    @staticmethod
    async def resolve_bot_username(request: web.Request) -> str:
        """Resolve current bot username dynamically from request app or environment."""
        bot = request.app.get("bot") if hasattr(request, "app") else None
        if bot is not None and hasattr(bot, "get_me"):
            try:
                me = await bot.get_me()
                if me and getattr(me, "username", None):
                    return str(me.username).lstrip("@")
            except Exception:
                pass
        return os.getenv("BOT_USERNAME", "").lstrip("@") or DEFAULT_FALLBACK_BOT_USERNAME

    @staticmethod
    async def get_user_and_subscriptions(
        session: AsyncSession, token: str
    ) -> tuple[User | None, VlessSubscription | None, WhiteInternetSubscription | None]:
        """Lookup user and their Standard / White Internet subscription rows by any valid token."""
        user: User | None = None
        vless_sub = await vless_subscription_repo.get_subscription_by_token(session, token)
        if vless_sub:
            user = await users_repo.get_user_by_id(session, vless_sub.user_id)

        wi_sub: WhiteInternetSubscription | None = None
        if not user:
            wi_sub = await white_internet_repo.get_subscription_by_token(session, token)
            if wi_sub:
                user = await users_repo.get_user_by_id(session, wi_sub.user_id)

        if not user:
            return None, None, None

        if vless_sub is None:
            vless_sub = await vless_subscription_repo.get_subscription_by_user_id(session, user.id)
        if wi_sub is None:
            wi_sub = await white_internet_repo.get_subscription_by_user_id(session, user.id)

        return user, vless_sub, wi_sub

    @staticmethod
    def render_landing_html(
        *,
        bot_username: str,
        is_active: bool,
        page_title: str | None = None,
        tier: str = "basic",
        tariff_badge: str | None = None,
        device_limit: int = 1,
        status_badge: str | None = None,
        status_title: str | None = None,
        status_description: str | None = None,
    ) -> str:
        active_tmpl, inactive_tmpl = _get_landing_templates()
        clean_bot = html.escape(bot_username.strip().lstrip("@")) if bot_username else DEFAULT_FALLBACK_BOT_USERNAME
        bot_link = f"https://t.me/{clean_bot}"

        if not is_active:
            badge = status_badge or texts.LANDING_INACTIVE_BADGE_PAUSED
            title = status_title or texts.LANDING_INACTIVE_TITLE_DEFAULT
            desc = status_description or texts.LANDING_INACTIVE_DESC_DEFAULT
            return (
                inactive_tmpl.replace("{clean_bot}", clean_bot)
                .replace("{bot_link}", bot_link)
                .replace("{status_badge}", html.escape(badge))
                .replace("{status_title}", html.escape(title))
                .replace("{status_description}", html.escape(desc))
            )

        badge_html = f'<div class="tier-badge">{html.escape(tariff_badge)}</div>' if tariff_badge else ""
        eff_title = page_title or texts.LANDING_VLESS_PAGE_TITLE
        return (
            active_tmpl.replace("{page_title}", html.escape(eff_title))
            .replace("{tier}", html.escape(tier))
            .replace("{tariff_badge_html}", badge_html)
            .replace("{device_limit}", str(max(1, device_limit)))
        )

    @classmethod
    async def build_subscription_feed(
        cls,
        session: AsyncSession,
        request: web.Request,
        token: str,
    ) -> tuple[int, str, dict[str, str]]:
        """Core subscription generator: handles browser landing, HWID queue, and dual profiles."""
        client_ip = get_trusted_client_ip(request)
        allowed_ip, retry_after_ip = _ip_rate_limiter.check(client_ip)
        if not allowed_ip:
            return 429, texts.VLESS_WEB_TOO_MANY_REQUESTS, {
                "Retry-After": str(retry_after_ip),
                "Cache-Control": "no-store",
            }

        allowed_tok, retry_after_tok = _token_rate_limiter.check(token)
        if not allowed_tok:
            return 429, texts.VLESS_WEB_TOO_MANY_REQUESTS, {
                "Retry-After": str(retry_after_tok),
                "Cache-Control": "no-store",
            }

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
            return 400, "Invalid HWID format", headers

        user, vless_sub, wi_sub = await cls.get_user_and_subscriptions(session, token)
        if user is None or getattr(user, "is_deleted", False) is True:
            return 404, "Not Found", common_headers

        if getattr(user, "is_banned", False) is True:
            return 403, "Forbidden", common_headers

        now = now_utc()
        bot_username = await cls.resolve_bot_username(request)
        clean_bot = bot_username.strip().lstrip("@") or DEFAULT_FALLBACK_BOT_USERNAME
        default_bot_url = f"https://t.me/{clean_bot}"

        is_std_active_access = SubscriptionService.check_vpn_access(user)
        std_device_limit = await SubscriptionService.get_effective_device_limit(session, user)

        wi_time_active = bool(
            wi_sub
            and wi_sub.status in (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.EXHAUSTED)
            and wi_sub.expires_at
            and wi_sub.expires_at > now
        )

        wi_traffic_limit = getattr(wi_sub, "traffic_limit_bytes", WHITE_INTERNET_BASE_TRAFFIC_BYTES)
        wi_used_bytes = max(
            0,
            (getattr(wi_sub, "traffic_used_bytes", 0) or 0)
            - (getattr(wi_sub, "traffic_overage_bytes", 0) or 0),
        )
        wi_available_bytes = max(0, wi_traffic_limit - wi_used_bytes) if wi_sub else 0
        wi_is_exhausted = bool(
            wi_sub
            and (wi_sub.status == WhiteInternetStatus.EXHAUSTED or wi_available_bytes <= 0)
        )
        wi_device_limit = getattr(wi_sub, "device_limit", 1) if wi_sub else 0

        is_browser = _is_browser_request(request)
        if not raw_hwid and is_browser:
            any_active = is_std_active_access or (wi_time_active and not wi_is_exhausted)
            max_devs = max(std_device_limit, wi_device_limit, 1)
            landing_html = cls.render_landing_html(
                bot_username=bot_username,
                is_active=any_active,
                device_limit=max_devs,
                page_title="✦ Just1k",
            )
            landing_headers = {
                **common_headers,
                "Content-Type": "text/html; charset=utf-8",
                "Content-Security-Policy": "default-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self';",
            }
            return 200, landing_html, landing_headers

        device_meta = extract_device_metadata(request.headers)
        allowed_std = False
        active_std_count = 0
        max_std = max(1, std_device_limit)

        if raw_hwid and vless_sub and is_std_active_access:
            allowed_std, active_std_count, max_std = (
                await vless_subscription_repo.register_hwid_atomic(
                    session,
                    vless_sub.id,
                    raw_hwid,
                    effective_limit=max_std,
                    device_info=device_meta,
                )
            )
        elif vless_sub:
            active_hwids = vless_sub.active_hwids if isinstance(vless_sub.active_hwids, dict) else {}
            active_std_count = len(active_hwids)
            allowed_std = (raw_hwid in active_hwids) or (active_std_count < max_std)

        allowed_wi = False
        active_wi_count = 0
        max_wi = max(1, wi_device_limit)

        if raw_hwid and wi_sub and wi_time_active:
            wi_hwids = wi_sub.active_hwids if isinstance(wi_sub.active_hwids, dict) else {}
            if raw_hwid in wi_hwids:
                allowed_wi = True
                active_wi_count = len(wi_hwids)
            elif len(wi_hwids) < max_wi:
                allowed_wi, active_wi_count, max_wi = (
                    await white_internet_repo.register_hwid_atomic(
                        session,
                        wi_sub.id,
                        raw_hwid,
                        max_devices=max_wi,
                        device_info=device_meta,
                    )
                )
            else:
                allowed_wi = False
                active_wi_count = len(wi_hwids)
        elif wi_sub:
            wi_hwids = wi_sub.active_hwids if isinstance(wi_sub.active_hwids, dict) else {}
            active_wi_count = len(wi_hwids)
            allowed_wi = (raw_hwid in wi_hwids) or (active_wi_count < max_wi)

        std_nodes: list[str] = []
        if is_std_active_access and allowed_std and vless_sub:
            std_servers = await VlessSubscriptionService.get_configured_vless_servers(
                session, user_id=user.id
            )
            std_nodes = VlessSubscriptionService.generate_vless_links(vless_sub, std_servers)

        wi_nodes: list[str] = []
        if wi_time_active and wi_sub:
            if not allowed_wi:
                wi_nodes.append(
                    build_service_notice_link(
                        f"✦ Исчерпан лимит устройств Белого Интернета ({active_wi_count}/{max_wi}) — докупите слот в @{clean_bot}"
                    )
                )
            elif wi_is_exhausted:
                wi_nodes.append(
                    build_service_notice_link(
                        f"✦ Закончился трафик Белого Интернета (0 ГБ) — пополните пакет в @{clean_bot}"
                    )
                )
            else:
                wi_server = None
                if getattr(wi_sub, "origin_node_id", None):
                    wi_server = await session.scalar(
                        select(Server).where(Server.id == wi_sub.origin_node_id)
                    )
                if wi_server and wi_server.is_active and wi_server.health_state == ServerHealthState.ONLINE:
                    extra = wi_server.extra_data if isinstance(wi_server.extra_data, dict) else {}
                    cdn_domain = normalize_public_domain(
                        extra.get("cdn_domain") or os.getenv("WHITE_INTERNET_CDN_DOMAIN")
                    )
                    base_path = (
                        extra.get("secret_base_path")
                        or os.getenv("WHITE_INTERNET_PATH")
                        or DEFAULT_WHITE_INTERNET_PATH
                    )
                    relays = extra.get("relays")
                    if relays is None and os.getenv("WHITE_INTERNET_RELAYS"):
                        try:
                            relays = json.loads(os.environ["WHITE_INTERNET_RELAYS"])
                        except Exception:
                            relays = None

                    origin_tag = extra.get("origin_tag")
                    origin_badge = extra.get("origin_badge")
                    default_relay_badge = extra.get("relay_badge")
                    relay_names = extra.get("relay_names") if isinstance(extra.get("relay_names"), dict) else None
                    relay_badges = extra.get("relay_badges") if isinstance(extra.get("relay_badges"), dict) else None
                    origin_hidden = bool(extra.get("origin_hidden", False))

                    if cdn_domain:
                        yandex_links = WhiteInternetService.generate_vless_links(
                            wi_sub,
                            cdn_domain=cdn_domain,
                            path=base_path,
                            relays=relays,
                            include_origin=not origin_hidden,
                            origin_tag=origin_tag,
                            origin_badge=origin_badge,
                            default_relay_badge=default_relay_badge,
                            relay_names=relay_names,
                            relay_badges=relay_badges,
                        )
                        wi_nodes.extend(yandex_links)

                    turboflare_domain = normalize_public_domain(
                        extra.get("turboflare_domain") or os.getenv("WHITE_INTERNET_TURBOFLARE_DOMAIN")
                    )
                    if turboflare_domain and turboflare_domain != cdn_domain:
                        turbo_origin_tag = f"{origin_tag or texts.WL_ORIGIN_VLESS_TAG} (Turbo)"
                        turbo_links = WhiteInternetService.generate_vless_links(
                            wi_sub,
                            cdn_domain=turboflare_domain,
                            path=base_path,
                            relays=relays,
                            include_origin=not origin_hidden,
                            origin_tag=turbo_origin_tag,
                            origin_badge=origin_badge,
                            default_relay_badge=default_relay_badge,
                            relay_names=relay_names,
                            relay_badges=relay_badges,
                        )
                        wi_nodes.extend(turbo_links)

        nodes_order = await get_system_setting(session, "sub_nodes_order", "standard_first")
        if nodes_order == "wi_first":
            combined_nodes = wi_nodes + std_nodes
        else:
            combined_nodes = std_nodes + wi_nodes

        user_agent = request.headers.get("User-Agent", "")
        client_name = parse_client_from_user_agent(user_agent)
        is_happ = bool(
            (client_name and "happ" in client_name.lower()) or ("happ" in user_agent.lower())
        )

        std_expire_ts = (
            int(user.subscription_end.timestamp())
            if (user.subscription_end and is_std_active_access)
            else 0
        )
        wi_expire_ts = (
            int(wi_sub.expires_at.timestamp())
            if (wi_sub and wi_sub.expires_at and wi_time_active)
            else 0
        )
        effective_expire_ts = max(std_expire_ts, wi_expire_ts)

        upload_bytes = (getattr(vless_sub, "traffic_up", 0) or 0) + (
            getattr(wi_sub, "traffic_uplink_bytes", 0) or 0
        )
        download_bytes = (getattr(vless_sub, "traffic_down", 0) or 0) + (
            getattr(wi_sub, "traffic_downlink_bytes", 0) or 0
        )
        total_quota_bytes = wi_traffic_limit if wi_time_active else 0

        title_template = await get_system_setting(
            session, "sub_profile_title_template", "✦ Just1k [{devices}]"
        )
        device_suffix = f"{active_std_count}/{max_std}"
        formatted_title = title_template.replace("{devices}", device_suffix).replace(
            "{active}", str(active_std_count)
        ).replace("{limit}", str(max_std))[:25]
        b64_title = base64.b64encode(formatted_title.encode("utf-8")).decode("ascii")

        update_interval = await get_system_setting(session, "sub_update_interval_hours", "6")
        geo_interval = await get_system_setting(session, "sub_routing_geo_update_interval", "168")
        routing_enabled_val = await get_system_setting(session, "sub_routing_enabled", "true")
        routing_enabled = str(routing_enabled_val).lower() in ("1", "true", "yes", "on")
        routing_direct_ru_val = await get_system_setting(session, "sub_routing_direct_ru", "true")
        routing_direct_ru = str(routing_direct_ru_val).lower() in ("1", "true", "yes", "on")

        response_headers = dict(common_headers)
        response_headers.update(
            {
                "Content-Type": "text/plain; charset=utf-8",
                "Content-Disposition": 'inline; filename="Just1k.txt"; filename*=UTF-8\'\'Just1k.txt',
                "Subscription-Userinfo": f"upload={upload_bytes}; download={download_bytes}; total={total_quota_bytes}; expire={effective_expire_ts}",
                "Device-Limit": str(max_std),
                "Device-Active-Count": str(active_std_count),
                "Profile-Title": f"base64:{b64_title}",
                "Profile-Update-Interval": str(update_interval),
                "Hide-Url": "1",
                "Hide-Check": "1",
                "Sort-Order": "none",
                "No-Limit-Enabled": "1",
                "x-hwid-active": "true",
            }
        )

        routing_scheme = "happ" if is_happ else "incy"
        if routing_enabled:
            response_headers["Routing"] = generate_incy_routing_header(
                profile_name=formatted_title,
                direct_ru=routing_direct_ru,
                scheme=routing_scheme,
            )
            response_headers["routing-geo-update-interval"] = str(geo_interval)
            if is_happ:
                response_headers["Routing-Enable"] = "true"
        else:
            response_headers["Routing"] = "off"
            if is_happ:
                response_headers["Routing-Enable"] = "0"

        if is_happ:
            if effective_expire_ts > 0:
                response_headers["sub-expire"] = "1"
            response_headers["sub-expire-button-link"] = default_bot_url

        support_url = await get_system_setting(session, "sub_support_url", default_bot_url)
        channel_url = await get_system_setting(session, "sub_channel_url", default_bot_url)
        sanitized_support = _sanitize_header_url(support_url)
        sanitized_channel = _sanitize_header_url(channel_url)
        if sanitized_support:
            response_headers["Support-Url"] = sanitized_support
        if sanitized_channel:
            response_headers["Channel-Url"] = sanitized_channel
            response_headers["Profile-Web-Page-Url"] = sanitized_channel

        announce_text = await get_system_setting(session, "sub_announce_text", None)
        announce_url = await get_system_setting(session, "sub_announce_url", None)
        if announce_text and str(announce_text).strip():
            clean_announce = str(announce_text).strip()[:200]
            announce_b64 = base64.b64encode(clean_announce.encode("utf-8")).decode("ascii")
            response_headers["Announce"] = f"base64:{announce_b64}"
        if announce_url:
            sanitized_announce = _sanitize_header_url(announce_url)
            if sanitized_announce:
                response_headers["Announce-Url"] = sanitized_announce

        if not combined_nodes:
            if is_std_active_access and not allowed_std:
                notice = f"✦ Превышен лимит устройств ({active_std_count}/{max_std}) — отключите старые в @{clean_bot}"
                response_headers["Device-Limit-Exceeded"] = "1"
                response_headers["x-hwid-max-devices-reached"] = "true"
            else:
                notice = f"✦ Подписка закончилась — продлите доступ в @{clean_bot}"
            combined_nodes = [build_service_notice_link(notice)]

        payload = "\n".join(combined_nodes)
        b64_payload = base64.b64encode(payload.encode("utf-8")).decode("utf-8")
        return 200, b64_payload, response_headers
