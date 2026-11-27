"""Thin HTTP handler for Mini App authenticated identity endpoint."""

from __future__ import annotations

from aiohttp import web

from bot.miniapp.auth import require_authenticated_user


async def api_me_handler(request: web.Request) -> web.Response:
    """Return authenticated Telegram Mini App user role."""
    ctx, err_resp = require_authenticated_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    display_name = " ".join(part for part in (ctx.first_name, ctx.last_name) if part).strip()
    return web.json_response({
        "ok": True,
        "user": {
            "telegram_id": ctx.telegram_id,
            "name": display_name or (f"@{ctx.username}" if ctx.username else str(ctx.telegram_id)),
            "username": ctx.username,
            "is_admin": ctx.is_admin,
        },
        "security": {
            "two_factor_required": False,
            "two_factor_verified": True,
        },
    })
