"""HTTP router and safe static asset serving for modular Mini App frontend."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from aiohttp import web

from bot.miniapp.auth import check_miniapp_asset_rate_limit, check_miniapp_rate_limit
from bot.miniapp.handlers_admin import (
    api_admin_2fa_logout_handler,
    api_admin_2fa_setup_handler,
    api_admin_2fa_verify_handler,
    api_admin_audit_logs_handler,
    api_admin_broadcast_cancel_handler,
    api_admin_broadcast_preview_handler,
    api_admin_broadcast_start_handler,
    api_admin_broadcasts_list_handler,
    api_admin_finances_handler,
    api_admin_incy_get_handler,
    api_admin_incy_put_handler,
    api_admin_mass_bonus_handler,
    api_admin_mass_extend_handler,
    api_admin_nodes_add_handler,
    api_admin_nodes_delete_handler,
    api_admin_nodes_lifecycle_handler,
    api_admin_nodes_list_handler,
    api_admin_nodes_patch_handler,
    api_admin_nodes_refresh_handler,
    api_admin_nodes_resync_clients_handler,
    api_admin_nodes_sync_topology_handler,
    api_admin_nodes_toggle_handler,
    api_admin_overview_handler,
    api_admin_payments_maintenance_get_handler,
    api_admin_payments_maintenance_post_handler,
    api_admin_user_adjust_handler,
    api_admin_user_details_handler,
    api_admin_user_device_delete_handler,
    api_admin_user_devices_reset_handler,
    api_admin_user_message_handler,
    api_admin_user_resync_handler,
    api_admin_user_token_rotate_handler,
    api_admin_users_handler,
    api_admin_webhook_dismiss_handler,
    api_admin_webhook_retry_handler,
    api_admin_webhooks_handler,
    api_me_handler,
)
from bot.miniapp.handlers_user import (
    api_user_auto_renew_handler,
    api_user_dashboard_handler,
    api_user_device_delete_handler,
    api_user_device_priority_handler,
    api_user_device_std_priority_handler,
    api_user_devices_reset_handler,
    api_user_order_check_handler,
    api_user_standard_checkout_handler,
    api_user_standard_downgrade_handler,
    api_user_standard_quote_handler,
    api_user_topup_handler,
    api_user_wi_checkout_handler,
    api_user_wi_downgrade_cancel_handler,
    api_user_wi_downgrade_handler,
)

logger = logging.getLogger(__name__)

FRONTEND_DIR = (Path(__file__).resolve().parents[2] / "frontend" / "miniapp").resolve()
GUIDES_ASSETS_DIR = (Path(__file__).resolve().parents[2] / "bot" / "templates" / "vless_landing" / "assets").resolve()

_MIME_TYPES = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".mp4": "video/mp4",
}

_NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}

_ASSET_CACHE_HEADERS = {
    "Cache-Control": "no-cache, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _compute_miniapp_asset_version() -> str:
    """Compute startup content hash across Mini App frontend assets for cache-busting."""
    if not FRONTEND_DIR.is_dir():
        return "2.8.0"
    digest = hashlib.sha256()
    for path in sorted(FRONTEND_DIR.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".html", ".css", ".js"}:
            digest.update(path.relative_to(FRONTEND_DIR).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()[:10]


_MINIAPP_ASSET_VERSION = _compute_miniapp_asset_version()


def _rewrite_asset_version(text: str) -> str:
    return text.replace("?v=2.8.0", f"?v={_MINIAPP_ASSET_VERSION}")


async def miniapp_index_handler(request: web.Request) -> web.Response:
    """Serve Mini App HTML entrypoint."""
    rate_err = check_miniapp_rate_limit(request)
    if rate_err is not None:
        return rate_err

    index_path = FRONTEND_DIR / "index.html"
    if not index_path.is_file():
        return web.Response(status=404, text="Mini App frontend not found")

    content = _rewrite_asset_version(index_path.read_text(encoding="utf-8"))
    return web.Response(
        text=content,
        content_type="text/html",
        charset="utf-8",
        headers=_NO_CACHE_HEADERS,
    )


async def miniapp_asset_handler(request: web.Request) -> web.Response:
    """Serve modular JS/CSS assets strictly from FRONTEND_DIR (or guide images) without traversal."""
    rate_err = check_miniapp_asset_rate_limit(request)
    if rate_err is not None:
        return rate_err

    rel_path = (request.match_info.get("asset_path") or "").lstrip("/")
    if not rel_path or ".." in rel_path.split("/"):
        return web.Response(status=404, text="Not Found")

    if rel_path.startswith("guides/"):
        guide_rel = rel_path[len("guides/"):]
        if not guide_rel or "/" in guide_rel:
            return web.Response(status=404, text="Not Found")
        target = (GUIDES_ASSETS_DIR / guide_rel).resolve()
        if GUIDES_ASSETS_DIR not in target.parents or not target.is_file():
            return web.Response(status=404, text="Not Found")
    else:
        target = (FRONTEND_DIR / rel_path).resolve()
        if FRONTEND_DIR not in target.parents or not target.is_file():
            return web.Response(status=404, text="Not Found")

    content_type = _MIME_TYPES.get(target.suffix.lower())
    if not content_type:
        return web.Response(status=404, text="Not Found")

    headers = (
        {"Cache-Control": "public, max-age=604800"}
        if rel_path.startswith("guides/")
        else _ASSET_CACHE_HEADERS
    )
    if not rel_path.startswith("guides/") and target.suffix.lower() in {".js", ".css"}:
        rewritten = _rewrite_asset_version(target.read_text(encoding="utf-8"))
        return web.Response(
            text=rewritten,
            content_type=content_type,
            charset="utf-8",
            headers=headers,
        )
    return web.FileResponse(target, headers=headers)


def setup_miniapp_routes(app: web.Application) -> None:
    """Register Mini App frontend shell, modular static assets, user and admin JSON APIs."""
    app.router.add_get("/app", miniapp_index_handler)
    app.router.add_get("/app/", miniapp_index_handler)
    app.router.add_get("/app/assets/{asset_path:.+}", miniapp_asset_handler)

    app.router.add_get("/api/app/me", api_me_handler)

    # Admin 2FA & session
    app.router.add_post("/api/app/admin/2fa/setup", api_admin_2fa_setup_handler)
    app.router.add_post("/api/app/admin/2fa/verify", api_admin_2fa_verify_handler)
    app.router.add_post("/api/app/admin/2fa/logout", api_admin_2fa_logout_handler)

    # Admin overview & webhooks
    app.router.add_get("/api/app/admin/overview", api_admin_overview_handler)
    app.router.add_get("/api/app/admin/webhooks", api_admin_webhooks_handler)
    app.router.add_post("/api/app/admin/webhooks/{webhook_id}/retry", api_admin_webhook_retry_handler)
    app.router.add_post("/api/app/admin/webhooks/{webhook_id}/dismiss", api_admin_webhook_dismiss_handler)

    # Admin nodes & users
    app.router.add_get("/api/app/admin/nodes", api_admin_nodes_list_handler)
    app.router.add_get("/api/app/admin/incy-settings", api_admin_incy_get_handler)
    app.router.add_put("/api/app/admin/incy-settings", api_admin_incy_put_handler)
    app.router.add_post("/api/app/admin/nodes/add", api_admin_nodes_add_handler)
    app.router.add_post("/api/app/admin/nodes/sync-topology", api_admin_nodes_sync_topology_handler)
    app.router.add_post("/api/app/admin/nodes/{server_id}/refresh", api_admin_nodes_refresh_handler)
    app.router.add_post("/api/app/admin/nodes/{server_id}/resync-clients", api_admin_nodes_resync_clients_handler)
    app.router.add_post("/api/app/admin/nodes/{server_id}/lifecycle", api_admin_nodes_lifecycle_handler)
    app.router.add_patch("/api/app/admin/nodes/{server_id}", api_admin_nodes_patch_handler)
    app.router.add_post("/api/app/admin/nodes/{server_id}/toggle", api_admin_nodes_toggle_handler)
    app.router.add_delete("/api/app/admin/nodes/{server_id}", api_admin_nodes_delete_handler)
    app.router.add_get("/api/app/admin/users", api_admin_users_handler)
    app.router.add_get("/api/app/admin/users/{user_id}/details", api_admin_user_details_handler)
    app.router.add_delete("/api/app/admin/users/{user_id}/devices/{hwid}", api_admin_user_device_delete_handler)
    app.router.add_post("/api/app/admin/users/{user_id}/devices/reset", api_admin_user_devices_reset_handler)
    app.router.add_post("/api/app/admin/users/{user_id}/resync", api_admin_user_resync_handler)
    app.router.add_post("/api/app/admin/users/{user_id}/token/rotate", api_admin_user_token_rotate_handler)
    app.router.add_post("/api/app/admin/users/{user_id}/adjust", api_admin_user_adjust_handler)
    app.router.add_post("/api/app/admin/users/{user_id}/message", api_admin_user_message_handler)

    # Admin broadcasts, mass operations, finances, audit logs
    app.router.add_get("/api/app/admin/settings/payments-maintenance", api_admin_payments_maintenance_get_handler)
    app.router.add_post("/api/app/admin/settings/payments-maintenance", api_admin_payments_maintenance_post_handler)
    app.router.add_get("/api/app/admin/broadcasts", api_admin_broadcasts_list_handler)
    app.router.add_post("/api/app/admin/broadcasts/preview", api_admin_broadcast_preview_handler)
    app.router.add_post("/api/app/admin/broadcasts", api_admin_broadcast_start_handler)
    app.router.add_post(
        "/api/app/admin/broadcasts/{broadcast_id}/cancel",
        api_admin_broadcast_cancel_handler,
    )
    app.router.add_post("/api/app/admin/mass/bonus", api_admin_mass_bonus_handler)
    app.router.add_post("/api/app/admin/mass/extend", api_admin_mass_extend_handler)
    app.router.add_get("/api/app/admin/finances", api_admin_finances_handler)
    app.router.add_get("/api/app/admin/audit-logs", api_admin_audit_logs_handler)

    # User cabinet APIs
    app.router.add_get("/api/app/user/dashboard", api_user_dashboard_handler)
    app.router.add_post("/api/app/user/standard/quote", api_user_standard_quote_handler)
    app.router.add_post("/api/app/user/standard/checkout", api_user_standard_checkout_handler)
    app.router.add_post("/api/app/user/standard/downgrade", api_user_standard_downgrade_handler)
    app.router.add_post("/api/app/user/wi/checkout", api_user_wi_checkout_handler)
    app.router.add_post("/api/app/user/wi/downgrade", api_user_wi_downgrade_handler)
    app.router.add_delete("/api/app/user/wi/downgrade", api_user_wi_downgrade_cancel_handler)
    app.router.add_post("/api/app/user/topup", api_user_topup_handler)
    app.router.add_post("/api/app/user/orders/{order_id}/check", api_user_order_check_handler)
    app.router.add_post("/api/app/user/auto-renew", api_user_auto_renew_handler)
    app.router.add_delete("/api/app/user/devices/{hwid}", api_user_device_delete_handler)
    app.router.add_post("/api/app/user/devices/reset", api_user_devices_reset_handler)
    app.router.add_post("/api/app/user/devices/{hwid}/priority", api_user_device_priority_handler)
    app.router.add_post("/api/app/user/devices/{hwid}/std-priority", api_user_device_std_priority_handler)

    logger.info("Registered modular Mini App routes: /app, /app/assets/*, /api/app/*")
