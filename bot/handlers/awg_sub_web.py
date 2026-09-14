"""HTTP subscription feed endpoint for AmneziaWG (/sub/awg/{token})."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import os

from aiohttp import web
from sqlalchemy import select

from bot import texts
from config.constants import AMNEZIA_PROTOCOL
from config.enums import ServerHealthState, ServerLifecycleStatus
from database.connection import session_scope
from database.models import Server, VPNProfile
from database.repositories import users_repo
from database.repositories.profiles_repo import get_user_effective_device_count
from services.awg_subscription_feed_service import AWGSubscriptionFeedService
from services.device_service import DeviceService, RESERVING_STATUSES
from services.slots_cache import capture_server_peer_snapshot
from services.subscription import SubscriptionService
from utils.datetime_helpers import is_expired, now_utc
from utils.http_rate_limiter import HttpRateLimiter, get_trusted_client_ip
from utils.user_agent_parser import parse_device_model_from_ua
from utils.vpn_parser import build_conf_file

logger = logging.getLogger(__name__)

# Scoped rate limiters: IP bucket prevents unauthenticated DoS / token brute force,
# token bucket prevents single subscription thrashing across rotating IPs.
_ip_rate_limiter = HttpRateLimiter(rate_per_minute=60.0, burst=15)
_token_rate_limiter = HttpRateLimiter(rate_per_minute=30.0, burst=10)

DEFAULT_AWG_SUB_PATH_PREFIX = "/sub/awg"


def is_browser_request(request: web.Request) -> bool:
    """Detect if the incoming HTTP request originates from a standard web browser."""
    action = (request.query.get("action") or "").lower()
    fmt = (request.query.get("format") or "").lower()
    if fmt == "html" or action in ("browser", "web", "view"):
        return True

    accept = (request.headers.get("Accept") or "").lower()
    if "text/html" in accept or "application/xhtml+xml" in accept:
        return True

    sec_dest = (request.headers.get("Sec-Fetch-Dest") or "").lower()
    if sec_dest == "document":
        return True

    ua = (request.headers.get("User-Agent") or "").lower()
    if "mozilla/" in ua and any(
        br in ua for br in ("safari", "chrome", "firefox", "edge", "opera", "telegram")
    ):
        if not any(
            tool in ua
            for tool in ("python", "aiohttp", "curl", "wget", "httpie", "postman", "pytest")
        ):
            return True

    return False


def get_subscription_public_url(request: web.Request, token: str) -> str:
    """Determine the canonical public subscription URL for the current feed."""
    try:
        from config.settings import get_settings

        settings = get_settings()
        domain = (getattr(settings, "DOMAIN", "") or "").strip()
    except Exception:
        domain = ""
    if not domain:
        domain = os.getenv("DOMAIN", "").strip()

    sub_base_url = (
        os.getenv("PUBLIC_URL")
        or os.getenv("SUB_BASE_URL")
        or os.getenv("APP_BASE_URL")
        or (f"https://{domain}" if domain else "")
    ).rstrip("/")

    if not sub_base_url:
        forwarded_proto = (request.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip()
        proto = forwarded_proto or ("https" if request.secure else "http")
        forwarded_host = (request.headers.get("X-Forwarded-Host") or "").split(",")[0].strip()
        host = forwarded_host or (request.headers.get("Host") or "").strip() or request.host
        sub_base_url = f"{proto}://{host}"

    sub_prefix = (
        os.getenv("AWG_SUB_PATH_PREFIX") or DEFAULT_AWG_SUB_PATH_PREFIX
    ).strip().rstrip("/")
    if not sub_prefix.startswith("/"):
        sub_prefix = f"/{sub_prefix}"

    return f"{sub_base_url}{sub_prefix}/{token}"


def render_awg_browser_landing_page(sub_url: str, bot_username: str = "just1kbot") -> str:
    """Render a modern, responsive HTML landing page that auto-launches INCY via deep link."""
    deep_link = f"incy://add/{sub_url}"
    sub_url_safe = html.escape(sub_url)
    deep_link_safe = html.escape(deep_link)
    bot_username_safe = html.escape(bot_username)
    deep_link_js = json.dumps(deep_link)
    sub_url_js = json.dumps(sub_url)

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <meta name="theme-color" content="#0f172a">
    <title>Добавление подписки в INCY</title>
    <style>
        :root {{
            --bg: #0f172a;
            --card-bg: #1e293b;
            --card-border: #334155;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --text-muted: #64748b;
            --primary: #38bdf8;
            --accent: #22c55e;
            --accent-bg: rgba(34, 197, 94, 0.15);
            --code-bg: #0b1120;
            --btn-sec-bg: #334155;
            --btn-sec-hover: #475569;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg);
            color: var(--text-primary);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 16px;
        }}
        .container {{
            width: 100%;
            max-width: 480px;
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 20px;
            padding: 28px 24px;
            box-shadow: 0 20px 30px -10px rgba(0, 0, 0, 0.5);
            text-align: center;
        }}
        .app-icon {{
            width: 72px;
            height: 72px;
            margin: 0 auto 16px;
            background: linear-gradient(135deg, #0284c7, #38bdf8);
            border-radius: 18px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 34px;
            box-shadow: 0 8px 16px -4px rgba(56, 189, 248, 0.4);
        }}
        h1 {{
            font-size: 22px;
            font-weight: 700;
            margin-bottom: 6px;
            letter-spacing: -0.3px;
        }}
        .subtitle {{
            color: var(--text-secondary);
            font-size: 14px;
            margin-bottom: 20px;
        }}
        .status-badge {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 8px 16px;
            background: var(--accent-bg);
            border: 1px solid rgba(34, 197, 94, 0.3);
            border-radius: 9999px;
            color: var(--accent);
            font-size: 13px;
            font-weight: 600;
            margin-bottom: 24px;
        }}
        .pulse-dot {{
            width: 8px;
            height: 8px;
            background: var(--accent);
            border-radius: 50%;
            animation: pulse 1.5s infinite;
        }}
        @keyframes pulse {{
            0% {{ transform: scale(0.95); box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.7); }}
            70% {{ transform: scale(1); box-shadow: 0 0 0 6px rgba(34, 197, 94, 0); }}
            100% {{ transform: scale(0.95); box-shadow: 0 0 0 0 rgba(34, 197, 94, 0); }}
        }}
        .btn-group {{
            display: flex;
            flex-direction: column;
            gap: 10px;
            margin-bottom: 22px;
        }}
        .btn {{
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            width: 100%;
            padding: 14px 18px;
            border-radius: 14px;
            font-size: 15px;
            font-weight: 600;
            text-decoration: none;
            cursor: pointer;
            transition: all 0.15s ease-in-out;
            border: none;
            outline: none;
        }}
        .btn-primary {{
            background: #0284c7;
            color: #ffffff;
            box-shadow: 0 4px 12px rgba(2, 132, 199, 0.35);
        }}
        .btn-primary:hover, .btn-primary:active {{
            background: #0369a1;
            transform: translateY(-1px);
        }}
        .btn-secondary {{
            background: var(--btn-sec-bg);
            color: var(--text-primary);
        }}
        .btn-secondary:hover, .btn-secondary:active {{
            background: var(--btn-sec-hover);
        }}
        .btn-copied {{
            background: var(--accent) !important;
            color: #ffffff !important;
        }}
        .url-box {{
            background: var(--code-bg);
            border: 1px solid var(--card-border);
            border-radius: 12px;
            padding: 12px;
            margin-bottom: 24px;
            text-align: left;
            cursor: pointer;
            transition: border-color 0.15s;
        }}
        .url-box:hover {{
            border-color: var(--primary);
        }}
        .url-label {{
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-muted);
            margin-bottom: 4px;
            font-weight: 600;
        }}
        .url-code {{
            display: block;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            font-size: 12px;
            color: var(--primary);
            word-break: break-all;
            line-height: 1.4;
        }}
        .copy-hint {{
            display: block;
            font-size: 11px;
            color: var(--text-muted);
            margin-top: 4px;
            text-align: right;
        }}
        .divider {{
            height: 1px;
            background: var(--card-border);
            margin: 20px 0;
        }}
        .section-title {{
            font-size: 13px;
            font-weight: 600;
            color: var(--text-secondary);
            margin-bottom: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .dl-grid {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 8px;
            margin-bottom: 20px;
        }}
        .dl-btn {{
            background: var(--code-bg);
            border: 1px solid var(--card-border);
            border-radius: 10px;
            padding: 10px 6px;
            color: var(--text-primary);
            text-decoration: none;
            font-size: 12px;
            font-weight: 500;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 4px;
            transition: all 0.15s;
        }}
        .dl-btn:hover {{
            border-color: var(--primary);
            background: rgba(56, 189, 248, 0.05);
        }}
        .dl-btn .icon {{
            font-size: 18px;
        }}
        .steps {{
            text-align: left;
            margin-bottom: 20px;
            font-size: 13px;
            color: var(--text-secondary);
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}
        .step-item {{
            display: flex;
            align-items: flex-start;
            gap: 10px;
        }}
        .step-num {{
            background: var(--btn-sec-bg);
            color: var(--text-primary);
            width: 20px;
            height: 20px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 11px;
            font-weight: 700;
            flex-shrink: 0;
            margin-top: 1px;
        }}
        .footer {{
            font-size: 13px;
            color: var(--text-muted);
        }}
        .footer a {{
            color: var(--primary);
            text-decoration: none;
        }}
        .footer a:hover {{
            text-decoration: underline;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="app-icon">🛡️</div>
        <h1>Добавление в INCY</h1>
        <p class="subtitle">Подписка AmneziaWG готова к подключению</p>

        <div class="status-badge">
            <span class="pulse-dot"></span>
            <span>Открываем приложение...</span>
        </div>

        <div class="btn-group">
            <a href="{deep_link_safe}" id="open-btn" class="btn btn-primary">
                🚀 Открыть в приложении INCY
            </a>
            <button type="button" id="copy-btn" class="btn btn-secondary" onclick="copySubscriptionUrl()">
                📋 Скопировать ссылку подписки
            </button>
        </div>

        <div class="url-box" onclick="copySubscriptionUrl()">
            <div class="url-label">Ссылка подписки:</div>
            <code class="url-code">{sub_url_safe}</code>
            <span class="copy-hint" id="hint-text">Нажмите для копирования</span>
        </div>

        <div class="section-title">Приложение еще не установлено?</div>
        <div class="dl-grid">
            <a href="https://apps.apple.com/app/incy/id6756943388" target="_blank" rel="noopener noreferrer" class="dl-btn">
                <span class="icon">🍏</span>
                <span>App Store</span>
            </a>
            <a href="https://play.google.com/store/apps/details?id=llc.itdev.incy" target="_blank" rel="noopener noreferrer" class="dl-btn">
                <span class="icon">🤖</span>
                <span>Google Play</span>
            </a>
            <a href="https://github.com/INCY-DEV/incy-platforms/releases" target="_blank" rel="noopener noreferrer" class="dl-btn">
                <span class="icon">📦</span>
                <span>GitHub APK</span>
            </a>
        </div>

        <div class="steps">
            <div class="step-item">
                <div class="step-num">1</div>
                <div>Установите <b>INCY</b> на ваш телефон или ПК.</div>
            </div>
            <div class="step-item">
                <div class="step-num">2</div>
                <div>Нажмите <b>«Открыть в приложении INCY»</b> выше (или вставьте скопированную ссылку через «+» в приложении).</div>
            </div>
            <div class="step-item">
                <div class="step-num">3</div>
                <div>Нажмите большую кнопку подключения на главном экране INCY.</div>
            </div>
        </div>

        <div class="divider"></div>

        <div class="footer">
            <a href="https://t.me/{bot_username_safe}" target="_blank" rel="noopener noreferrer">
                ← Вернуться в Telegram-бот (@{bot_username_safe})
            </a>
        </div>
    </div>

    <script>
        var deepLink = {deep_link_js};
        var subUrl = {sub_url_js};

        (function() {{
            var redirected = false;
            function autoLaunch() {{
                if (redirected) return;
                redirected = true;
                window.location.href = deepLink;
            }}
            if (document.readyState === "complete" || document.readyState === "interactive") {{
                setTimeout(autoLaunch, 150);
            }} else {{
                window.addEventListener("DOMContentLoaded", function() {{
                    setTimeout(autoLaunch, 150);
                }});
            }}
        }})();

        function copySubscriptionUrl() {{
            var copyBtn = document.getElementById("copy-btn");
            var hintText = document.getElementById("hint-text");
            var success = function() {{
                copyBtn.innerText = "✅ Ссылка скопирована!";
                copyBtn.classList.add("btn-copied");
                if (hintText) hintText.innerText = "Скопировано в буфер обмена!";
                setTimeout(function() {{
                    copyBtn.innerText = "📋 Скопировать ссылку подписки";
                    copyBtn.classList.remove("btn-copied");
                    if (hintText) hintText.innerText = "Нажмите для копирования";
                }}, 2500);
            }};
            var fallback = function() {{
                var ta = document.createElement("textarea");
                ta.value = subUrl;
                ta.style.position = "fixed";
                ta.style.opacity = "0";
                document.body.appendChild(ta);
                ta.select();
                try {{
                    document.execCommand("copy");
                    success();
                }} catch (e) {{}}
                document.body.removeChild(ta);
            }};
            if (navigator.clipboard && navigator.clipboard.writeText) {{
                navigator.clipboard.writeText(subUrl).then(success).catch(fallback);
            }} else {{
                fallback();
            }}
        }}
    </script>
</body>
</html>"""


def render_awg_browser_error_page(
    title: str, message: str, bot_username: str = "just1kbot"
) -> str:
    """Render a styled HTML error page for browser requests."""
    title_safe = html.escape(title)
    message_safe = html.escape(message)
    bot_username_safe = html.escape(bot_username)

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="theme-color" content="#0f172a">
    <title>{title_safe}</title>
    <style>
        :root {{
            --bg: #0f172a;
            --card-bg: #1e293b;
            --card-border: #334155;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --error: #ef4444;
            --btn-bg: #334155;
            --btn-hover: #475569;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg);
            color: var(--text-primary);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 16px;
        }}
        .card {{
            width: 100%;
            max-width: 440px;
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 20px;
            padding: 32px 24px;
            box-shadow: 0 20px 30px -10px rgba(0, 0, 0, 0.5);
            text-align: center;
        }}
        .icon {{
            font-size: 44px;
            margin-bottom: 16px;
        }}
        h1 {{
            font-size: 20px;
            font-weight: 700;
            color: var(--error);
            margin-bottom: 10px;
        }}
        p {{
            font-size: 14px;
            color: var(--text-secondary);
            line-height: 1.5;
            margin-bottom: 24px;
        }}
        .btn {{
            display: inline-block;
            padding: 12px 24px;
            background: var(--btn-bg);
            color: var(--text-primary);
            text-decoration: none;
            border-radius: 12px;
            font-size: 14px;
            font-weight: 600;
            transition: background 0.15s;
        }}
        .btn:hover {{
            background: var(--btn-hover);
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">⚠️</div>
        <h1>{title_safe}</h1>
        <p>{message_safe}</p>
        <a href="https://t.me/{bot_username_safe}" class="btn">Открыть Telegram-бот (@{bot_username_safe})</a>
    </div>
</body>
</html>"""


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
        if is_browser_request(request):
            bot_username = os.getenv("BOT_USERNAME", "just1kbot").lstrip("@")
            return web.Response(
                status=404,
                text=render_awg_browser_error_page(
                    title="Подписка не найдена",
                    message="Ссылка подписки недействительна или не существует.",
                    bot_username=bot_username,
                ),
                content_type="text/html",
                charset="utf-8",
                headers={"Cache-Control": "no-store"},
            )
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
        if is_browser_request(request):
            bot_username = os.getenv("BOT_USERNAME", "just1kbot").lstrip("@")
            async with session_scope() as session:
                user = await users_repo.get_user_by_subscription_token(
                    session, token, for_update=False
                )
                if user is None:
                    return web.Response(
                        status=404,
                        text=render_awg_browser_error_page(
                            title="Подписка не найдена",
                            message="Ссылка подписки недействительна или не существует.",
                            bot_username=bot_username,
                        ),
                        content_type="text/html",
                        charset="utf-8",
                        headers=common_headers,
                    )

                if (
                    getattr(user, "is_banned", False) is True
                    or getattr(user, "is_deleted", False) is True
                    or getattr(user, "financial_hold", False) is True
                ):
                    return web.Response(
                        status=403,
                        text=render_awg_browser_error_page(
                            title="Доступ ограничен",
                            message="Действие подписки приостановлено администратором.",
                            bot_username=bot_username,
                        ),
                        content_type="text/html",
                        charset="utf-8",
                        headers=common_headers,
                    )

                if not user.subscription_end or is_expired(user.subscription_end):
                    return web.Response(
                        status=403,
                        text=render_awg_browser_error_page(
                            title="Подписка истекла",
                            message="Срок действия вашей подписки закончился. Продлите её в Telegram-боте.",
                            bot_username=bot_username,
                        ),
                        content_type="text/html",
                        charset="utf-8",
                        headers=common_headers,
                    )

                sub_url = get_subscription_public_url(request, token)
                html_body = render_awg_browser_landing_page(
                    sub_url=sub_url,
                    bot_username=bot_username,
                )
                return web.Response(
                    status=200,
                    text=html_body,
                    content_type="text/html",
                    charset="utf-8",
                    headers=common_headers,
                )

        headers = dict(common_headers)
        headers["x-hwid-required"] = "true"
        return web.Response(status=403, text=texts.AWG_WEB_HWID_REQUIRED, headers=headers)

    hwid_hash = hashlib.sha256(raw_hwid.lower().encode("utf-8")).hexdigest()
    now = now_utc()

    async with session_scope() as session:
        user = await users_repo.get_user_by_subscription_token(session, token, for_update=False)
        if user is None:
            return web.Response(status=404, text="Not Found", headers=common_headers)

        if (
            getattr(user, "is_banned", False) is True
            or getattr(user, "is_deleted", False) is True
            or getattr(user, "financial_hold", False) is True
        ):
            return web.Response(status=403, text="Forbidden", headers=common_headers)

        if not user.subscription_end or is_expired(user.subscription_end):
            return web.Response(status=403, text=texts.AWG_WEB_EXPIRED, headers=common_headers)

        # Quota check: Distinct logical sub devices + manual configurations
        active_sub_devices = dict(user.active_sub_devices or {})
        is_existing = hwid_hash in active_sub_devices
        new_sub_device_record = None

        if not is_existing:
            total_active = await get_user_effective_device_count(
                session, user.id, active_sub_devices
            )
            effective_limit = await SubscriptionService.get_effective_device_limit(session, user)
            if effective_limit is not None:
                limit = effective_limit
            else:
                user_lim = getattr(user, "device_limit", None)
                limit = user_lim if user_lim is not None else 2

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

            existing_indices = {
                dev.get("device_index")
                for dev in active_sub_devices.values()
                if isinstance(dev, dict) and isinstance(dev.get("device_index"), int)
            }
            device_idx = 1
            while device_idx in existing_indices:
                device_idx += 1

            raw_ua = request.headers.get("User-Agent", "")
            detected_label = parse_device_model_from_ua(
                raw_ua,
                fallback_index=device_idx,
                fallback_template=texts.AWG_SUB_DEVICE_LABEL_TEMPLATE,
            )
            new_sub_device_record = {
                "device_index": device_idx,
                "label": detected_label,
                "first_seen": now.isoformat(),
                "last_seen": now.isoformat(),
            }
        else:
            existing_rec = active_sub_devices.get(hwid_hash) or {}
            device_idx = existing_rec.get("device_index") or 1

        # Find active AWG servers
        servers_stmt = select(Server).where(
            Server.is_active.is_(True),
            Server.protocol == AMNEZIA_PROTOCOL,
            Server.health_state == ServerHealthState.ONLINE,
            Server.lifecycle_status == ServerLifecycleStatus.ACTIVE,
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
            VPNProfile.provisioning_status != "deleted",
        )
        existing_profiles = (await session.execute(existing_profiles_stmt)).scalars().all()
        profiles_by_server = {}
        failed_profiles_by_server = {}
        for p in existing_profiles:
            if p.provisioning_status in RESERVING_STATUSES:
                profiles_by_server[p.server_id] = p
            elif p.provisioning_status in ("create_failed", "create_cleanup_pending"):
                failed_profiles_by_server[p.server_id] = p

        newly_created = False
        for srv in awg_servers:
            if srv.id not in profiles_by_server:
                replaces_id = None
                if srv.id in failed_profiles_by_server:
                    old_p = failed_profiles_by_server[srv.id]
                    # If remote cleanup is already in-flight on the server, wait for cleanup worker
                    # to avoid overloading server capacity or creating duplicate active peers.
                    if old_p.provisioning_status == "create_cleanup_pending":
                        logger.info(
                            "Profile %s on server %s is still awaiting remote peer cleanup; deferring recreation",
                            old_p.id,
                            srv.id,
                        )
                        continue

                    replaces_id = old_p.id
                    try:
                        async with session.begin_nested():
                            await DeviceService.delete_device(
                                session, old_p, actor_id=user.telegram_id, force=True
                            )
                    except Exception as del_exc:
                        logger.warning(
                            "Failed to clean up stale/failed profile %s on server %s: %s",
                            old_p.id,
                            srv.id,
                            del_exc,
                        )
                        # Skip recreation on this server if cleanup failed to avoid orphaned state or capacity breach
                        continue

                try:
                    snapshot = await capture_server_peer_snapshot(srv.id)
                    async with session.begin_nested():
                        dev_name = texts.AWG_SUB_PROFILE_NAME_TEMPLATE.format(
                            server_name=srv.name or "AWG",
                            index=device_idx,
                        )
                        profile = await DeviceService.create_device(
                            session,
                            user_id=user.id,
                            server_id=srv.id,
                            device_name=dev_name,
                            snapshot=snapshot,
                            device_type="sub",
                            sub_device_hash=hwid_hash,
                            replaces_profile_id=replaces_id,
                        )
                        profiles_by_server[srv.id] = profile
                        newly_created = True
                except Exception as exc:
                    logger.warning("Failed to create sub profile for server %s: %s", srv.id, exc)

        valid_profiles = [
            p for p in profiles_by_server.values() if p.provisioning_status in RESERVING_STATUSES
        ]
        if not valid_profiles:
            # All profile creations failed. Fail-closed: do not register new device or occupy slot!
            headers = dict(common_headers)
            headers["Retry-After"] = "10"
            return web.Response(status=503, text=texts.AWG_WEB_NO_CONFIGS, headers=headers)

        # Atomic logical device registration: only persist once at least one profile is created/available
        if not is_existing and new_sub_device_record:
            active_sub_devices[hwid_hash] = new_sub_device_record
            user.active_sub_devices = active_sub_devices
            await session.flush()
        elif is_existing:
            existing_dev = dict(active_sub_devices.get(hwid_hash) or {})
            curr_label = existing_dev.get("label", "")
            if curr_label.startswith(texts.AWG_DEFAULT_DEVICE_PREFIX) or curr_label.startswith(
                texts.AWG_LEGACY_DEVICE_PREFIX
            ):
                raw_ua = request.headers.get("User-Agent", "")
                friendly = parse_device_model_from_ua(
                    raw_ua,
                    fallback_index=device_idx,
                    fallback_template=texts.AWG_SUB_DEVICE_LABEL_TEMPLATE,
                )
                if not (
                    friendly.startswith(texts.AWG_DEFAULT_DEVICE_PREFIX)
                    or friendly.startswith(texts.AWG_LEGACY_DEVICE_PREFIX)
                ):
                    existing_dev["label"] = friendly
            existing_dev["last_seen"] = now.isoformat()
            existing_dev["notified_inactive_at"] = None
            active_sub_devices[hwid_hash] = existing_dev
            user.active_sub_devices = active_sub_devices
            await session.flush()

        if newly_created:
            await session.commit()

        # Check if any profile is still provisioning
        has_pending = any(
            p.provisioning_status in ("pending_create", "pending_update") or not p.raw_config
            for p in valid_profiles
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
            if not p or not p.raw_config or getattr(p, "is_active", None) is False or p.provisioning_status != "active":
                continue
            conf = build_conf_file(p.raw_config)
            if conf:
                server_configs.append(
                    (
                        conf,
                        srv.name or "Server",
                        srv.country_flag or "",
                        getattr(srv, "ping", None),
                    )
                )

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
        (os.getenv("AWG_SUB_PATH_PREFIX") or DEFAULT_AWG_SUB_PATH_PREFIX).strip().rstrip("/")
    )
    if not sub_prefix.startswith("/"):
        sub_prefix = f"/{sub_prefix}"

    app.router.add_get(f"{sub_prefix}/ping", awg_ping_handler)
    app.router.add_get(f"{sub_prefix}/{{token}}", awg_subscription_feed_handler)
    if sub_prefix != DEFAULT_AWG_SUB_PATH_PREFIX:
        app.router.add_get(f"{DEFAULT_AWG_SUB_PATH_PREFIX}/ping", awg_ping_handler)
        app.router.add_get(
            f"{DEFAULT_AWG_SUB_PATH_PREFIX}/{{token}}", awg_subscription_feed_handler
        )
    logger.info(
        "AWG subscription feed routes registered: %s/{token} and %s/ping", sub_prefix, sub_prefix
    )
