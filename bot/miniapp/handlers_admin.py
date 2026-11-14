"""Thin HTTP handlers for Mini App identity, admin 2FA, node management, and user stats."""

from __future__ import annotations

from decimal import Decimal
from typing import TypeVar
from uuid import uuid4

from aiohttp import web
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, or_, select

from bot.miniapp.auth import require_admin_user, require_authenticated_user
from bot.miniapp.schemas import (
    AddNodePayload,
    AdminUserAdjustPayload,
    DeployCertPayload,
    NodeLinksUpdatePayload,
    SetupPinPayload,
    ToggleNodePayload,
    UpdateNodePayload,
    Verify2FAPayload,
)
from database.connection import session_scope
from database.models import AccountLedgerEntry, User, WhiteInternetSubscription
from database.repositories.account_ledger_repo import (
    create_admin_adjustment,
    get_account_balance,
)
from database.repositories.vless_subscription_repo import get_or_create_subscription
from database.repositories.white_internet_repo import get_subscription_by_user_id
from services import admin_nodes_service
from services.admin_nodes_service import AdminNodeServiceError
from services.miniapp_auth_service import (
    build_totp_uri,
    generate_totp_secret,
    get_admin_2fa_config,
    issue_totp_setup_token,
    setup_admin_pin,
    verify_admin_2fa,
    verify_admin_session_token,
)
from services.subscription import SubscriptionService
from services.white_internet_service import WhiteInternetService
from utils.datetime_helpers import now_utc

SchemaT = TypeVar("SchemaT", bound=BaseModel)


def _parse_server_id(request: web.Request) -> tuple[int | None, web.Response | None]:
    raw_id = request.match_info.get("server_id", "")
    if not raw_id.isdigit():
        return None, web.json_response({"ok": False, "error": "Invalid node id"}, status=400)
    server_id = int(raw_id)
    if server_id < 1 or server_id > 2_147_483_647:
        return None, web.json_response({"ok": False, "error": "Invalid node id"}, status=400)
    return server_id, None


async def _parse_json_model(
    request: web.Request,
    model_cls: type[SchemaT],
) -> tuple[SchemaT | None, web.Response | None]:
    try:
        raw_body = await request.json()
    except Exception:
        return None, web.json_response({"ok": False, "error": "Invalid JSON body"}, status=400)

    if not isinstance(raw_body, dict):
        return None, web.json_response({"ok": False, "error": "JSON object expected"}, status=400)

    try:
        return model_cls.model_validate(raw_body), None
    except ValidationError as exc:
        first_err = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(part) for part in first_err.get("loc", ()))
        msg = first_err.get("msg", "Validation error")
        detail = f"{loc}: {msg}" if loc else msg
        return None, web.json_response({"ok": False, "error": detail}, status=400)


def _service_error_response(exc: AdminNodeServiceError) -> web.Response:
    return web.json_response({"ok": False, "error": exc.message}, status=exc.status_code)


async def api_me_handler(request: web.Request) -> web.Response:
    """Return authenticated Telegram Mini App user role and real 2FA status."""
    ctx, err_resp = require_authenticated_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    display_name = " ".join(part for part in (ctx.first_name, ctx.last_name) if part).strip()
    if ctx.is_admin:
        session_token = request.headers.get("X-Admin-Session")
        session_valid = verify_admin_session_token(session_token, ctx.telegram_id)
        async with session_scope() as session:
            cfg = await get_admin_2fa_config(session, ctx.telegram_id)
        security_payload = {
            "two_factor_required": True,
            "two_factor_verified": session_valid,
            "two_factor_configured": cfg["configured"],
            "two_factor_mode": cfg["mode"],
        }
    else:
        security_payload = {
            "two_factor_required": False,
            "two_factor_verified": True,
        }

    return web.json_response({
        "ok": True,
        "user": {
            "telegram_id": ctx.telegram_id,
            "name": display_name or (f"@{ctx.username}" if ctx.username else str(ctx.telegram_id)),
            "username": ctx.username,
            "is_admin": ctx.is_admin,
        },
        "security": security_payload,
    })


async def api_admin_2fa_setup_handler(request: web.Request) -> web.Response:
    """Generate a temporary TOTP secret & otpauth URI for initial admin 2FA setup."""
    ctx, err_resp = require_admin_user(request, require_2fa=False)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    async with session_scope() as session:
        cfg = await get_admin_2fa_config(session, ctx.telegram_id)
        if cfg["configured"]:
            return web.json_response(
                {"ok": False, "error": "2FA уже настроена"},
                status=409,
            )

    secret = generate_totp_secret()
    account = f"@{ctx.username}" if ctx.username else str(ctx.telegram_id)
    setup_token = issue_totp_setup_token(ctx.telegram_id, secret)
    return web.json_response({
        "ok": True,
        "secret": secret,
        "otpauth_uri": build_totp_uri(secret, account),
        "setup_token": setup_token,
    })


async def api_admin_2fa_setup_pin_handler(request: web.Request) -> web.Response:
    """Set initial admin PIN when 2FA is not yet configured."""
    ctx, err_resp = require_admin_user(request, require_2fa=False)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, SetupPinPayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    async with session_scope() as session:
        result = await setup_admin_pin(session, ctx.telegram_id, payload.pin)

    if not result.get("verified"):
        return web.json_response({"ok": False, "error": result.get("error", "Ошибка")}, status=400)
    return web.json_response({"ok": True, **result})


async def api_admin_2fa_verify_handler(request: web.Request) -> web.Response:
    """Verify TOTP or PIN code and issue signed X-Admin-Session token."""
    ctx, err_resp = require_admin_user(request, require_2fa=False)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, Verify2FAPayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    async with session_scope() as session:
        result = await verify_admin_2fa(
            session,
            ctx.telegram_id,
            payload.code,
            setup_token=payload.setup_token,
        )

    if not result.get("verified"):
        return web.json_response({"ok": False, "error": result.get("error", "Неверный код")}, status=403)
    return web.json_response({"ok": True, **result})


async def api_admin_nodes_list_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    async with session_scope() as session:
        nodes = await admin_nodes_service.list_nodes(session)
        return web.json_response({"ok": True, "nodes": nodes})


async def api_admin_nodes_add_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, AddNodePayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    try:
        async with session_scope() as session:
            result = await admin_nodes_service.discover_and_add_node(
                session,
                admin_id=ctx.telegram_id,
                api_url=payload.api_url,
                api_key=payload.api_key,
                name=payload.name,
                country_flag=payload.country_flag,
                allow_bootstrap_tls=payload.allow_bootstrap_tls,
            )
            return web.json_response({"ok": True, **result}, status=201)
    except AdminNodeServiceError as exc:
        return _service_error_response(exc)


async def api_admin_nodes_refresh_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    server_id, id_err = _parse_server_id(request)
    if id_err is not None or server_id is None:
        return id_err  # type: ignore[return-value]

    try:
        async with session_scope() as session:
            node = await admin_nodes_service.refresh_node(
                session,
                admin_id=ctx.telegram_id,
                server_id=server_id,
            )
            return web.json_response({"ok": True, "node": node})
    except AdminNodeServiceError as exc:
        return _service_error_response(exc)


async def api_admin_nodes_patch_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    server_id, id_err = _parse_server_id(request)
    if id_err is not None or server_id is None:
        return id_err  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, UpdateNodePayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    try:
        async with session_scope() as session:
            node = await admin_nodes_service.update_node_settings(
                session,
                admin_id=ctx.telegram_id,
                server_id=server_id,
                name=payload.name,
                country_flag=payload.country_flag,
                allow_bootstrap_tls=payload.allow_bootstrap_tls,
            )
            return web.json_response({"ok": True, "node": node})
    except AdminNodeServiceError as exc:
        return _service_error_response(exc)


async def api_admin_nodes_toggle_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    server_id, id_err = _parse_server_id(request)
    if id_err is not None or server_id is None:
        return id_err  # type: ignore[return-value]

    raw_text = await request.text()
    target_active: bool | None = None
    if raw_text.strip():
        payload, parse_err = await _parse_json_model(request, ToggleNodePayload)
        if parse_err is not None or payload is None:
            return parse_err  # type: ignore[return-value]
        target_active = payload.is_active

    try:
        async with session_scope() as session:
            node = await admin_nodes_service.toggle_node(
                session,
                admin_id=ctx.telegram_id,
                server_id=server_id,
                is_active=target_active,
            )
            return web.json_response({"ok": True, "node": node})
    except AdminNodeServiceError as exc:
        return _service_error_response(exc)


async def api_admin_nodes_delete_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    server_id, id_err = _parse_server_id(request)
    if id_err is not None or server_id is None:
        return id_err  # type: ignore[return-value]

    try:
        async with session_scope() as session:
            deleted = await admin_nodes_service.delete_node(
                session,
                admin_id=ctx.telegram_id,
                server_id=server_id,
            )
            return web.json_response({"ok": True, **deleted})
    except AdminNodeServiceError as exc:
        return _service_error_response(exc)


async def api_admin_nodes_links_put_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    server_id, id_err = _parse_server_id(request)
    if id_err is not None or server_id is None:
        return id_err  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, NodeLinksUpdatePayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    try:
        async with session_scope() as session:
            result = await admin_nodes_service.update_node_links(
                session,
                admin_id=ctx.telegram_id,
                server_id=server_id,
                links_payload=payload.model_dump(exclude_none=True),
            )
            return web.json_response({"ok": True, **result})
    except AdminNodeServiceError as exc:
        return _service_error_response(exc)


async def api_admin_nodes_cert_post_handler(request: web.Request) -> web.Response:
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    server_id, id_err = _parse_server_id(request)
    if id_err is not None or server_id is None:
        return id_err  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, DeployCertPayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    try:
        async with session_scope() as session:
            result = await admin_nodes_service.push_node_certificate(
                session,
                admin_id=ctx.telegram_id,
                server_id=server_id,
                domain=payload.domain,
                fullchain_pem=payload.fullchain_pem,
                privkey_pem=payload.privkey_pem,
            )
            return web.json_response({"ok": True, **result})
    except AdminNodeServiceError as exc:
        return _service_error_response(exc)


async def api_admin_users_handler(request: web.Request) -> web.Response:
    """Return user statistics and paginated user list with balances and subscriptions."""
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    q = (request.query.get("q") or "").strip()
    try:
        limit = min(50, max(1, int(request.query.get("limit", "25"))))
    except ValueError:
        limit = 25
    try:
        offset = max(0, int(request.query.get("offset", "0")))
    except ValueError:
        offset = 0

    now = now_utc()

    async with session_scope() as session:
        total_users = int(
            await session.scalar(select(func.count(User.id)).where(User.is_deleted.is_(False))) or 0
        )
        active_standard = int(
            await session.scalar(
                select(func.count(User.id)).where(
                    User.is_deleted.is_(False),
                    User.subscription_end.is_not(None),
                    User.subscription_end > now,
                )
            )
            or 0
        )
        active_wi = int(
            await session.scalar(
                select(func.count(WhiteInternetSubscription.id)).where(
                    WhiteInternetSubscription.status == "active",
                    WhiteInternetSubscription.expires_at.is_not(None),
                    WhiteInternetSubscription.expires_at > now,
                )
            )
            or 0
        )
        total_balance_rub = Decimal(
            await session.scalar(select(func.coalesce(func.sum(AccountLedgerEntry.amount), 0))) or 0
        )

        stmt = select(User).where(User.is_deleted.is_(False))
        if q:
            if q.isdigit():
                stmt = stmt.where(
                    or_(
                        User.telegram_id == int(q),
                        User.id == int(q),
                    )
                )
            else:
                pattern = f"%{q.lstrip('@')}%"
                stmt = stmt.where(
                    or_(
                        User.username.ilike(pattern),
                        User.first_name.ilike(pattern),
                    )
                )

        stmt = stmt.order_by(User.created_at.desc(), User.id.desc()).limit(limit).offset(offset)
        users = (await session.scalars(stmt)).all()

        user_ids = [u.id for u in users]
        wi_map: dict[int, WhiteInternetSubscription] = {}
        if user_ids:
            wi_rows = (
                await session.scalars(
                    select(WhiteInternetSubscription).where(
                        WhiteInternetSubscription.user_id.in_(user_ids)
                    )
                )
            ).all()
            wi_map = {w.user_id: w for w in wi_rows}

        items: list[dict] = []
        for u in users:
            bal = await get_account_balance(session, user_id=u.id)
            wi = wi_map.get(u.id)
            std_active = bool(u.subscription_end and u.subscription_end > now)
            wi_active = bool(wi and wi.status == "active" and wi.expires_at and wi.expires_at > now)
            items.append({
                "id": u.id,
                "telegram_id": u.telegram_id,
                "username": u.username,
                "first_name": u.first_name,
                "real_balance": int(bal.real_available),
                "bonus_balance": int(bal.bonus_available),
                "total_balance": int(bal.available),
                "standard_active": std_active,
                "standard_end": u.subscription_end.isoformat() if u.subscription_end else None,
                "standard_device_limit": int(u.device_limit or 0),
                "wi_active": wi_active,
                "wi_end": wi.expires_at.isoformat() if wi and wi.expires_at else None,
                "wi_device_limit": int(wi.device_limit or 0) if wi else 0,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            })

        return web.json_response({
            "ok": True,
            "stats": {
                "total_users": total_users,
                "active_standard": active_standard,
                "active_wi": active_wi,
                "total_balance_rub": int(max(Decimal(0), total_balance_rub)),
            },
            "users": items,
        })


def _parse_user_id(request: web.Request) -> tuple[int | None, web.Response | None]:
    raw_id = request.match_info.get("user_id", "")
    if not raw_id.isdigit():
        return None, web.json_response({"ok": False, "error": "Invalid user id"}, status=400)
    user_id = int(raw_id)
    if user_id < 1 or user_id > 2_147_483_647:
        return None, web.json_response({"ok": False, "error": "Invalid user id"}, status=400)
    return user_id, None


async def api_admin_user_adjust_handler(request: web.Request) -> web.Response:
    """Admin action: adjust user balance or grant/extend Standard or White Internet access."""
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None:
        return err_resp
    if ctx is None:
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

    target_user_id, id_err = _parse_user_id(request)
    if id_err is not None or target_user_id is None:
        return id_err  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, AdminUserAdjustPayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    note = (payload.note or "").strip()

    async with session_scope() as session:
        user = await session.get(User, target_user_id)
        if user is None or getattr(user, "is_deleted", False):
            return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)

        if payload.action == "balance":
            assert payload.amount_rub is not None
            if payload.amount_rub < 0:
                bal_before = await get_account_balance(session, user_id=user.id)
                if abs(payload.amount_rub) > bal_before.available:
                    return web.json_response(
                        {"ok": False, "error": "Недостаточно средств на балансе пользователя"},
                        status=400,
                    )
            default_desc = "Бонусное пополнение (админ)" if payload.amount_rub > 0 else "Списание администратором"
            await create_admin_adjustment(
                session,
                user_id=user.id,
                signed_amount=payload.amount_rub,
                idempotency_key=f"miniapp:admin:{ctx.telegram_id}:{user.id}:{uuid4().hex}",
                metadata={
                    "source": "miniapp_admin",
                    "admin_telegram_id": ctx.telegram_id,
                    "description": note or default_desc,
                },
            )
            bal_after = await get_account_balance(session, user_id=user.id)
            return web.json_response({
                "ok": True,
                "action": "balance",
                "real_balance": int(bal_after.real_available),
                "bonus_balance": int(bal_after.bonus_available),
                "total_balance": int(bal_after.available),
                "message": f"Баланс обновлён ({payload.amount_rub:+d} ₽)",
            })

        if payload.action == "grant_standard":
            assert payload.days is not None
            await get_or_create_subscription(session, user.id)
            target_limit = payload.device_limit or (
                user.device_limit if (user.device_limit or 0) >= 2 else 2
            )
            try:
                updated_user = await SubscriptionService.extend_subscription(
                    session,
                    user.telegram_id,
                    days=payload.days,
                    new_device_limit=target_limit,
                    admin_id=ctx.telegram_id,
                    reason=note or "miniapp_admin_grant",
                )
            except ValueError as exc:
                return web.json_response({"ok": False, "error": str(exc)}, status=400)
            if updated_user is None:
                return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)
            return web.json_response({
                "ok": True,
                "action": "grant_standard",
                "standard_end": updated_user.subscription_end.isoformat() if updated_user.subscription_end else None,
                "standard_device_limit": int(updated_user.device_limit or 0),
                "message": f"Подписка «Стандарт» выдана/продлена на {payload.days} дн.",
            })

        # grant_wi
        assert payload.days is not None
        wi_sub = await get_subscription_by_user_id(session, user.id)
        if wi_sub is None:
            ok, msg, created = await WhiteInternetService.purchase_subscription(
                session,
                user.id,
                debit_balance=False,
                device_limit=payload.device_limit or 1,
            )
            if not ok:
                return web.json_response({"ok": False, "error": msg}, status=400)
            if payload.days > 30 and created is not None:
                ok_ext, msg_ext, created_ext = await WhiteInternetService.extend_subscription(
                    session, user.id, payload.days - 30
                )
                if not ok_ext:
                    return web.json_response({"ok": False, "error": msg_ext}, status=400)
                created = created_ext
            end_iso = created.expires_at.isoformat() if created and created.expires_at else None
            return web.json_response({
                "ok": True,
                "action": "grant_wi",
                "wi_end": end_iso,
                "message": "Подписка «Белый интернет» активирована",
            })

        ok, msg, updated = await WhiteInternetService.extend_subscription(
            session, user.id, payload.days
        )
        if not ok:
            return web.json_response({"ok": False, "error": msg}, status=400)
        end_iso = updated.expires_at.isoformat() if updated and updated.expires_at else None
        return web.json_response({
            "ok": True,
            "action": "grant_wi",
            "wi_end": end_iso,
            "message": f"Подписка «Белый интернет» продлена на {payload.days} дн.",
        })
