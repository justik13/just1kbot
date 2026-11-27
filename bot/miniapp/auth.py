"""HTTP authentication, admin authorization, 2FA session, and rate-limit guards for Mini App."""

from __future__ import annotations

from aiohttp import web

from services.miniapp_auth_service import (
    InitDataExpiredError,
    MiniAppUserContext,
    authenticate_miniapp_request,
    resolve_admin_session_epoch,
    verify_admin_session_token,
)
from utils.http_rate_limiter import HttpRateLimiter, get_trusted_client_ip

_api_rate_limiter = HttpRateLimiter(rate_per_minute=120.0, burst=30)
_asset_rate_limiter = HttpRateLimiter(rate_per_minute=600.0, burst=150)


def _check_rate_limit(limiter: HttpRateLimiter, request: web.Request) -> web.Response | None:
    client_ip = get_trusted_client_ip(request)
    allowed, retry_after = limiter.check(client_ip)
    if not allowed:
        return web.json_response(
            {"ok": False, "error": "Too many requests"},
            status=429,
            headers={"Retry-After": str(retry_after)},
        )
    return None


def check_miniapp_rate_limit(request: web.Request) -> web.Response | None:
    return _check_rate_limit(_api_rate_limiter, request)


def check_miniapp_asset_rate_limit(request: web.Request) -> web.Response | None:
    return _check_rate_limit(_asset_rate_limiter, request)


def _extract_init_data(request: web.Request) -> str | None:
    init_data = request.headers.get("X-Telegram-Init-Data")
    if not init_data:
        auth_header = (request.headers.get("Authorization") or "").strip()
        if auth_header.lower().startswith(("tma ", "bearer ")):
            init_data = auth_header.split(" ", 1)[1].strip()
        elif auth_header:
            init_data = auth_header
    return init_data


def extract_miniapp_user(request: web.Request) -> MiniAppUserContext | None:
    return authenticate_miniapp_request(_extract_init_data(request))


def require_authenticated_user(
    request: web.Request,
) -> tuple[MiniAppUserContext | None, web.Response | None]:
    rate_err = check_miniapp_rate_limit(request)
    if rate_err is not None:
        return None, rate_err

    try:
        ctx = authenticate_miniapp_request(
            _extract_init_data(request),
            raise_on_expired=True,
        )
    except InitDataExpiredError:
        return None, web.json_response(
            {
                "ok": False,
                "error": "Сессия Telegram устарела. Переоткройте приложение.",
                "code": "init_data_expired",
            },
            status=401,
        )
    if ctx is None:
        return None, web.json_response(
            {"ok": False, "error": "Unauthorized"},
            status=401,
        )
    return ctx, None


async def require_admin_user(
    request: web.Request,
    *,
    require_2fa: bool = True,
) -> tuple[MiniAppUserContext | None, web.Response | None]:
    ctx, err_resp = require_authenticated_user(request)
    if err_resp is not None or ctx is None:
        return None, err_resp

    if not ctx.is_admin:
        return None, web.json_response(
            {"ok": False, "error": "Access denied"},
            status=403,
        )

    if require_2fa:
        session_token = request.headers.get("X-Admin-Session")
        expected_epoch = await resolve_admin_session_epoch()
        if not verify_admin_session_token(
            session_token,
            ctx.telegram_id,
            expected_epoch=expected_epoch,
        ):
            return None, web.json_response(
                {"ok": False, "error": "Требуется подтверждение 2FA", "code": "2fa_required"},
                status=403,
            )

    return ctx, None
