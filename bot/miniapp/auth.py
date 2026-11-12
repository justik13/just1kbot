"""HTTP authentication, admin authorization, and rate-limit guards for Mini App."""

from __future__ import annotations

from aiohttp import web

from services.miniapp_auth_service import MiniAppUserContext, authenticate_miniapp_request
from utils.http_rate_limiter import HttpRateLimiter, get_trusted_client_ip

_api_rate_limiter = HttpRateLimiter(rate_per_minute=120.0, burst=30)


def check_miniapp_rate_limit(request: web.Request) -> web.Response | None:
    client_ip = get_trusted_client_ip(request)
    allowed, retry_after = _api_rate_limiter.check(client_ip)
    if not allowed:
        return web.json_response(
            {"ok": False, "error": "Too many requests"},
            status=429,
            headers={"Retry-After": str(retry_after)},
        )
    return None


def extract_miniapp_user(request: web.Request) -> MiniAppUserContext | None:
    init_data = request.headers.get("X-Telegram-Init-Data") or request.query.get("initData")
    return authenticate_miniapp_request(init_data)


def require_authenticated_user(
    request: web.Request,
) -> tuple[MiniAppUserContext | None, web.Response | None]:
    rate_err = check_miniapp_rate_limit(request)
    if rate_err is not None:
        return None, rate_err

    ctx = extract_miniapp_user(request)
    if ctx is None:
        return None, web.json_response(
            {"ok": False, "error": "Unauthorized"},
            status=401,
        )
    return ctx, None


def require_admin_user(
    request: web.Request,
) -> tuple[MiniAppUserContext | None, web.Response | None]:
    ctx, err_resp = require_authenticated_user(request)
    if err_resp is not None or ctx is None:
        return None, err_resp

    if not ctx.is_admin:
        return None, web.json_response(
            {"ok": False, "error": "Access denied"},
            status=403,
        )
    return ctx, None
