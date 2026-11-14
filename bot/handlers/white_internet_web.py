"""HTTP subscription feed endpoint for White Internet (/sub/wl/{token})."""

from __future__ import annotations

import functools
import html
import logging
import os
from pathlib import Path

from aiohttp import web

from bot import texts
from config import constants
from config.constants import (
    DEFAULT_WHITE_INTERNET_SUB_PATH_PREFIX,
    WHITE_INTERNET_SUB_PATH_PREFIX,
)
from database.connection import session_scope
from database.repositories import users_repo, white_internet_repo
from utils.http_rate_limiter import HttpRateLimiter, get_trusted_client_ip

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


@functools.lru_cache(maxsize=1)
def get_white_internet_pending_html() -> str:
    return (_TEMPLATES_DIR / "vless_landing_pending.html").read_text(encoding="utf-8")


_report_ip_limiter = HttpRateLimiter(rate_per_minute=5.0, burst=3)
_report_token_limiter = HttpRateLimiter(rate_per_minute=3.0, burst=2)


def _render_white_internet_pending_landing_html(bot_username: str = "") -> str:
    """Render landing page for PENDING state with auto-refresh."""
    clean_bot = (
        html.escape(bot_username.strip().lstrip("@"))
        if bot_username
        else constants.DEFAULT_FALLBACK_BOT_USERNAME
    )
    bot_link = f"https://t.me/{clean_bot}"
    return get_white_internet_pending_html().replace("{clean_bot}", clean_bot).replace("{bot_link}", bot_link)


async def white_internet_subscription_feed_handler(request: web.Request) -> web.Response:
    """Serve unified no-store Base64 subscription feed for legacy /sub/wl/{token} links."""
    from bot.handlers.vless_web import serve_unified_subscription_feed

    return await serve_unified_subscription_feed(request, token_source="white_internet")


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


async def white_internet_report_handler(request: web.Request) -> web.Response:
    """Handle emergency contact reports from White Internet landing pages."""
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
            sub = await white_internet_repo.get_subscription_by_token(session, token)
            if not sub:
                return web.json_response(
                    {"ok": False, "error": texts.LANDING_REPORT_INVALID_LINK},
                    status=404,
                    headers={"Cache-Control": "no-store"},
                )
            user = await users_repo.get_user_by_id(session, sub.user_id)
            if not user or getattr(user, "is_deleted", False):
                return web.json_response(
                    {"ok": False, "error": texts.LANDING_REPORT_INVALID_LINK},
                    status=404,
                    headers={"Cache-Control": "no-store"},
                )
            sub_status = sub.status.value if hasattr(sub.status, "value") else str(sub.status)
            active_count = white_internet_repo.count_active_hwids(sub.active_hwids)
            max_devs = white_internet_repo._effective_device_limit(sub)
            devices_info = f"{active_count} / {max_devs}"
            user_tag = f"@{html.escape(user.username)}" if user.username else "—"
            user_info = f"ID: <code>{user.telegram_id}</code> ({user_tag})"
    except Exception as exc:
        logger.error("Failed to query subscription info for emergency report: %s", exc)
        return web.json_response(
            {"ok": False, "error": texts.LANDING_REPORT_DELIVERY_FAILED},
            status=500,
            headers={"Cache-Control": "no-store"},
        )

    admin_text = texts.ADMIN_ALERT_EMERGENCY_SUPPORT.format(
        user_info=user_info,
        category=texts.LANDING_WHITE_INTERNET_PAGE_TITLE,
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


def setup_white_internet_web_routes(app: web.Application) -> None:
    """Register White Internet HTTP subscription feed routes with support for custom prefixes."""
    sub_prefix = (
        os.getenv("WHITE_INTERNET_SUB_PATH_PREFIX") or WHITE_INTERNET_SUB_PATH_PREFIX
    ).strip().rstrip("/")
    if not sub_prefix.startswith("/"):
        sub_prefix = f"/{sub_prefix}"
    app.router.add_get(f"{sub_prefix}/ping", white_internet_ping_handler)
    app.router.add_get(f"{sub_prefix}/{{token}}", white_internet_subscription_feed_handler)
    app.router.add_post(f"{sub_prefix}/{{token}}/report", white_internet_report_handler)
    if sub_prefix != DEFAULT_WHITE_INTERNET_SUB_PATH_PREFIX:
        app.router.add_get(f"{DEFAULT_WHITE_INTERNET_SUB_PATH_PREFIX}/ping", white_internet_ping_handler)
        app.router.add_get(f"{DEFAULT_WHITE_INTERNET_SUB_PATH_PREFIX}/{{token}}", white_internet_subscription_feed_handler)
        app.router.add_post(f"{DEFAULT_WHITE_INTERNET_SUB_PATH_PREFIX}/{{token}}/report", white_internet_report_handler)
    logger.info("White Internet subscription feed routes registered: %s/{token}, %s/{token}/report, %s/ping", sub_prefix, sub_prefix, sub_prefix)
