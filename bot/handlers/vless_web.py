"""HTTP subscription feed endpoint for standard access (/sub/access/{token} and /sub/vless/{token})."""

from __future__ import annotations

import base64
import html
import logging
import os
import re

from aiohttp import web

from bot import texts
from database.connection import session_scope
from database.repositories import users_repo, vless_subscription_repo
from database.repositories.system_settings_repo import get_system_setting
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


def _render_vless_landing_html(bot_username: str = "just1kbot", is_active: bool = True) -> str:
    """Render universal landing page for INCY 1-click import and installation guidance."""
    clean_bot = html.escape(bot_username.strip().lstrip("@"))
    bot_link = f"https://t.me/{clean_bot}"

    if not is_active:
        return f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Подключение к сервису</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; }}
        body {{ background: #0b0f19; color: #f8fafc; min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 16px; }}
        .card {{ width: 100%; max-width: 440px; background: #151d30; border: 1px solid #334155; border-radius: 20px; padding: 32px 24px; text-align: center; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.5); }}
        .icon {{ font-size: 48px; margin-bottom: 16px; }}
        h1 {{ font-size: 22px; font-weight: 700; margin-bottom: 8px; }}
        p {{ font-size: 14px; color: #94a3b8; margin-bottom: 24px; line-height: 1.5; }}
        .btn {{ display: flex; align-items: center; justify-content: center; width: 100%; padding: 14px 18px; border-radius: 12px; font-size: 16px; font-weight: 600; text-decoration: none; cursor: pointer; background: #2563eb; color: #fff; transition: background 0.2s; }}
        .btn:hover {{ background: #1d4ed8; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">⏳</div>
        <h1>Подписка не активна</h1>
        <p>Срок действия доступа истек или подписка приостановлена. Для возобновления перейдите в бота.</p>
        <a href="{bot_link}" class="btn">Перейти в бота @{clean_bot}</a>
    </div>
</body>
</html>"""

    return """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Подключение к сервису</title>
    <style>
        :root {
            --bg-color: #0b0f19;
            --card-bg: #151d30;
            --text-main: #f8fafc;
            --text-sub: #94a3b8;
            --accent: #2563eb;
            --accent-hover: #1d4ed8;
            --btn-sec-bg: #1e293b;
            --btn-sec-border: #334155;
            --btn-sec-hover: #293548;
            --success: #10b981;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; }
        body {
            background-color: var(--bg-color);
            color: var(--text-main);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 16px;
        }
        .card {
            width: 100%;
            max-width: 440px;
            background-color: var(--card-bg);
            border: 1px solid var(--btn-sec-border);
            border-radius: 20px;
            padding: 28px 24px;
            text-align: center;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
        }
        .icon { font-size: 48px; margin-bottom: 16px; }
        h1 { font-size: 22px; font-weight: 700; margin-bottom: 8px; }
        p.subtitle { font-size: 14px; color: var(--text-sub); margin-bottom: 24px; line-height: 1.5; }
        .btn {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            width: 100%;
            padding: 14px 18px;
            border-radius: 12px;
            font-size: 16px;
            font-weight: 600;
            text-decoration: none;
            cursor: pointer;
            transition: all 0.2s ease;
            margin-bottom: 12px;
            border: none;
        }
        .btn-primary { background-color: var(--accent); color: #ffffff; }
        .btn-primary:hover { background-color: var(--accent-hover); }
        .btn-secondary { background-color: var(--btn-sec-bg); color: var(--text-main); border: 1px solid var(--btn-sec-border); }
        .btn-secondary:hover { background-color: var(--btn-sec-hover); }
        .divider { height: 1px; background-color: var(--btn-sec-border); margin: 24px 0; }
        .section-title { font-size: 13px; font-weight: 600; color: var(--text-sub); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px; }
        .store-buttons { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 10px; }
        .store-btn {
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 10px;
            border-radius: 10px;
            background-color: var(--btn-sec-bg);
            border: 1px solid var(--btn-sec-border);
            color: var(--text-main);
            text-decoration: none;
            font-size: 13px;
            font-weight: 500;
            transition: background-color 0.2s;
        }
        .store-btn:hover { background-color: var(--btn-sec-hover); }
        .steps {
            text-align: left;
            background-color: rgba(0, 0, 0, 0.2);
            border-radius: 12px;
            padding: 14px 16px;
            font-size: 13px;
            color: var(--text-sub);
            line-height: 1.6;
        }
        .steps ol { padding-left: 18px; }
        .steps li { margin-bottom: 4px; }
        .steps strong { color: var(--text-main); }
        .copy-toast { display: none; margin-top: 8px; font-size: 13px; color: var(--success); font-weight: 500; }
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">⚡️</div>
        <h1>Подключение к сервису</h1>
        <p class="subtitle">Открытие приложения для автоматической настройки доступа.</p>

        <a id="deepLinkBtn" href="#" class="btn btn-primary">
            <span>🚀 Открыть в приложении INCY</span>
        </a>

        <button id="copyBtn" class="btn btn-secondary" onclick="copyLink()">
            <span>📋 Скопировать ссылку</span>
        </button>
        <div id="copyToast" class="copy-toast">Ссылка скопирована в буфер!</div>

        <div class="divider"></div>

        <div class="section-title">Приложение еще не установлено?</div>
        <div class="store-buttons">
            <a href="https://apps.apple.com/app/incy/id6756943388" target="_blank" rel="noopener" class="store-btn">🍏 App Store</a>
            <a href="https://play.google.com/store/apps/details?id=llc.itdev.incy" target="_blank" rel="noopener" class="store-btn">🤖 Google Play</a>
        </div>
        <a href="https://github.com/INCY-DEV/incy-platforms/releases" target="_blank" rel="noopener" class="store-btn" style="margin-bottom: 16px;">💻 Скачать для ПК / APK (GitHub)</a>

        <div class="steps">
            <strong>Как подключить:</strong>
            <ol>
                <li>Установите приложение <strong>INCY</strong> по ссылкам выше.</li>
                <li>Нажмите кнопку <strong>«Открыть в приложении INCY»</strong>.</li>
            </ol>
        </div>
    </div>

    <script>
        const subUrl = window.location.href;
        const deepLink = "incy://add/" + subUrl;
        const btn = document.getElementById("deepLinkBtn");
        if (btn) {
            btn.href = deepLink;
        }

        // Auto-redirect to INCY
        try {
            window.location.href = deepLink;
        } catch (e) {}

        function copyLink() {
            navigator.clipboard.writeText(subUrl).then(function() {
                const toast = document.getElementById("copyToast");
                if (toast) {
                    toast.textContent = "Ссылка скопирована в буфер!";
                    toast.style.display = "block";
                    setTimeout(function() { toast.style.display = "none"; }, 2500);
                }
            }).catch(function() {
                const toast = document.getElementById("copyToast");
                if (toast) {
                    toast.textContent = "Не удалось скопировать";
                    toast.style.display = "block";
                }
            });
        }
    </script>
</body>
</html>"""


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

            bot_username = os.getenv("BOT_USERNAME", "just1kbot").lstrip("@")
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
                "Support-Url": f"https://t.me/{bot_username}",
            }
            return web.Response(status=200, text=b64_payload, headers=response_headers)

        # Strict HWID validation for active/emergency subscription requests
        if not raw_hwid:
            bot_username = os.getenv("BOT_USERNAME", "just1kbot").lstrip("@")
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

        # Pre-flight check: ensure eligible VLESS servers exist before mutating HWID quota
        servers = await VlessSubscriptionService.get_eligible_vless_servers(session)
        if not servers:
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
            bot_username = os.getenv("BOT_USERNAME", "just1kbot").lstrip("@")
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
                "x-hwid-limit": str(effective_vless_limit),
                "x-hwid-active": str(active_hwid_count),
                "Hide-Url": "1",
                "No-Limit-Enabled": "1",
                "Support-Url": f"https://t.me/{bot_username}",
            })
            return web.Response(status=200, text=b64_payload, headers=headers)

        await session.refresh(sub)
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

        profile_title = await get_system_setting(session, "vless_profile_title", os.getenv("VLESS_PROFILE_TITLE", "✦ Just1k"))
        if not isinstance(profile_title, str) or not profile_title.strip():
            profile_title = os.getenv("VLESS_PROFILE_TITLE", "✦ Just1k") or "✦ Just1k"

        profile_desc = await get_system_setting(session, "vless_profile_description", None)
        if not isinstance(profile_desc, str) or not profile_desc.strip():
            profile_desc = await get_system_setting(session, "vless_profile_desc", None)
        if not isinstance(profile_desc, str) or not profile_desc.strip():
            profile_desc = None

        announce = await get_system_setting(session, "vless_announce", None)
        if not isinstance(announce, str) or not announce.strip():
            announce = None

        announce_url = await get_system_setting(session, "vless_announce_url", None)
        if not isinstance(announce_url, str) or not announce_url.strip():
            announce_url = None
        elif any(c in announce_url for c in (" ", "\n", "\r", "\t")):
            announce_url = None
        support_url = f"https://t.me/{bot_username}"
        channel_url = None

        if servers:
            first_srv = servers[0]
            first_extra = first_srv.extra_data if isinstance(first_srv.extra_data, dict) else {}
            if first_extra.get("support_url"):
                support_url = first_extra["support_url"]
            if first_extra.get("channel_url"):
                channel_url = first_extra["channel_url"]

        b64_title = base64.b64encode(str(profile_title).strip()[:25].encode("utf-8")).decode("ascii")
        response_headers = {
            **common_headers,
            "Content-Type": "text/plain; charset=utf-8",
            "Profile-Update-Interval": _get_vless_update_interval(),
            "Subscription-Userinfo": f"upload=0; download=0; total=0; expire={expire_ts}",
            "Device-Limit": str(effective_vless_limit),
            "Device-Active-Count": str(active_hwid_count),
            "Profile-Title": f"base64:{b64_title}",
            "Hide-Url": "1",
            "No-Limit-Enabled": "1",
            "Support-Url": support_url,
        }
        if profile_desc:
            b64_desc = base64.b64encode(str(profile_desc).strip()[:100].encode("utf-8")).decode("ascii")
            response_headers["Profile-Description"] = f"base64:{b64_desc}"
        if channel_url:
            response_headers["Profile-Web-Page-Url"] = channel_url
            response_headers["Channel-Url"] = channel_url
        if announce:
            b64_ann = base64.b64encode(str(announce).strip()[:200].encode("utf-8")).decode("ascii")
            response_headers["Announce"] = f"base64:{b64_ann}"
        if announce_url:
            response_headers["Announce-Url"] = announce_url
        return web.Response(status=200, text=b64_payload, headers=response_headers)


def setup_vless_web_routes(app: web.Application) -> None:
    """Register HTTP subscription feed route for VLESS access."""
    app.router.add_get("/sub/access/{token}", vless_subscription_feed_handler)
    app.router.add_get("/sub/vless/{token}", vless_subscription_feed_handler)
