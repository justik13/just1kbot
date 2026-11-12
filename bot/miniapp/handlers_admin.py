"""Thin HTTP handlers for Mini App identity and admin-only v2 node management."""

from __future__ import annotations

from typing import TypeVar

from aiohttp import web
from pydantic import BaseModel, ValidationError

from bot.miniapp.auth import require_admin_user, require_authenticated_user
from bot.miniapp.schemas import (
    AddNodePayload,
    DeployCertPayload,
    NodeLinksUpdatePayload,
    ToggleNodePayload,
    UpdateNodePayload,
    Verify2FAStubPayload,
)
from database.connection import session_scope
from services import admin_nodes_service
from services.admin_nodes_service import AdminNodeServiceError
from services.miniapp_auth_service import verify_admin_2fa_stub

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
    """Return authenticated Telegram Mini App user role and 2FA stub status."""
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
            "two_factor_required": ctx.is_admin,
            "two_factor_mode": "stub_todo",
            "two_factor_todo": "TODO: TOTP 2FA (pyotp) pending",
        },
    })


async def api_admin_2fa_verify_stub_handler(request: web.Request) -> web.Response:
    """Temporary 2FA verification stub endpoint (TODO: wire real TOTP check)."""
    ctx, err_resp = require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, Verify2FAStubPayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    result = verify_admin_2fa_stub(ctx.telegram_id, payload.code)
    return web.json_response({"ok": bool(result.get("verified")), **result})


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
                max_clients=payload.max_clients,
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
                max_clients=payload.max_clients,
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
