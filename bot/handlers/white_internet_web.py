"""HTTP subscription feed endpoint for legacy White Internet (/sub/wl/{token}) links."""

from __future__ import annotations

import functools
import html
import logging
import os
from pathlib import Path

from aiohttp import web

from config import constants
from config.constants import (
    DEFAULT_WHITE_INTERNET_SUB_PATH_PREFIX,
    WHITE_INTERNET_SUB_PATH_PREFIX,
)

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


@functools.lru_cache(maxsize=1)
def get_white_internet_pending_html() -> str:
    return (_TEMPLATES_DIR / "vless_landing_pending.html").read_text(encoding="utf-8")


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


def setup_white_internet_web_routes(app: web.Application) -> None:
    """Register White Internet HTTP subscription feed routes with support for custom prefixes."""
    from bot.handlers.vless_web import vless_report_handler

    sub_prefix = (
        os.getenv("WHITE_INTERNET_SUB_PATH_PREFIX") or WHITE_INTERNET_SUB_PATH_PREFIX
    ).strip().rstrip("/")
    if not sub_prefix.startswith("/"):
        sub_prefix = f"/{sub_prefix}"
    app.router.add_get(f"{sub_prefix}/ping", white_internet_ping_handler)
    app.router.add_get(f"{sub_prefix}/{{token}}", white_internet_subscription_feed_handler)
    app.router.add_post(f"{sub_prefix}/{{token}}/report", vless_report_handler)
    if sub_prefix != DEFAULT_WHITE_INTERNET_SUB_PATH_PREFIX:
        app.router.add_get(f"{DEFAULT_WHITE_INTERNET_SUB_PATH_PREFIX}/ping", white_internet_ping_handler)
        app.router.add_get(f"{DEFAULT_WHITE_INTERNET_SUB_PATH_PREFIX}/{{token}}", white_internet_subscription_feed_handler)
        app.router.add_post(f"{DEFAULT_WHITE_INTERNET_SUB_PATH_PREFIX}/{{token}}/report", vless_report_handler)
    logger.info(
        "White Internet subscription feed routes registered: %s/{token}, %s/{token}/report, %s/ping",
        sub_prefix,
        sub_prefix,
        sub_prefix,
    )
