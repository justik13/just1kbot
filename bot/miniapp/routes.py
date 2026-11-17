"""HTTP router and safe static asset serving for modular Mini App frontend."""

from __future__ import annotations

import html
import json
import logging
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import web

from bot.miniapp.auth import check_miniapp_rate_limit
from config.settings import get_settings
from bot.miniapp.handlers_admin import (
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
    api_admin_nodes_list_handler,
    api_admin_nodes_patch_handler,
    api_admin_nodes_refresh_handler,
    api_admin_nodes_toggle_handler,
    api_admin_payments_maintenance_get_handler,
    api_admin_payments_maintenance_post_handler,
    api_admin_user_adjust_handler,
    api_admin_user_details_handler,
    api_admin_user_devices_reset_handler,
    api_admin_user_message_handler,
    api_admin_user_token_rotate_handler,
    api_admin_users_handler,
    api_me_handler,
)
from bot.miniapp.handlers_user import (
    api_user_auto_renew_handler,
    api_user_dashboard_handler,
    api_user_device_delete_handler,
    api_user_device_priority_handler,
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

_MIME_TYPES = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
    ".json": "application/json",
    ".svg": "image/svg+xml",
}

_NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


async def miniapp_index_handler(request: web.Request) -> web.Response:
    """Serve Mini App HTML entrypoint."""
    rate_err = check_miniapp_rate_limit(request)
    if rate_err is not None:
        return rate_err

    index_path = FRONTEND_DIR / "index.html"
    if not index_path.is_file():
        return web.Response(status=404, text="Mini App frontend not found")

    content = index_path.read_text(encoding="utf-8")
    return web.Response(
        text=content,
        content_type="text/html",
        charset="utf-8",
        headers=_NO_CACHE_HEADERS,
    )


async def miniapp_asset_handler(request: web.Request) -> web.Response:
    """Serve modular JS/CSS assets strictly from FRONTEND_DIR without traversal."""
    rate_err = check_miniapp_rate_limit(request)
    if rate_err is not None:
        return rate_err

    rel_path = (request.match_info.get("asset_path") or "").lstrip("/")
    if not rel_path or ".." in rel_path.split("/"):
        return web.Response(status=404, text="Not Found")

    target = (FRONTEND_DIR / rel_path).resolve()
    if FRONTEND_DIR not in target.parents or not target.is_file():
        return web.Response(status=404, text="Not Found")

    content_type = _MIME_TYPES.get(target.suffix.lower())
    if not content_type:
        return web.Response(status=404, text="Not Found")

    text = target.read_text(encoding="utf-8")
    return web.Response(
        text=text,
        content_type=content_type,
        charset="utf-8",
        headers=_NO_CACHE_HEADERS,
    )


async def miniapp_open_client_handler(request: web.Request) -> web.Response:
    """Serve an external-browser bridge page to launch incy:// or happ:// without crashing WebView."""
    client = (request.query.get("client") or "").strip().lower()
    sub_url = (request.query.get("sub") or "").strip()
    if client not in {"incy", "happ"} or not sub_url:
        return web.Response(status=400, text="Invalid request")

    parsed = urlparse(sub_url)
    allowed_hosts = {request.host.split(":", 1)[0].lower()}
    configured_domain = (get_settings().DOMAIN or "").strip().lower()
    if configured_domain:
        allowed_hosts.add(configured_domain)

    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.hostname.lower() not in allowed_hosts
        or not parsed.path.startswith("/sub/")
    ):
        return web.Response(status=400, text="Invalid subscription URL")

    deep_link = f"{client}://add/{sub_url}"
    client_label = "INCY" if client == "incy" else "Happ"
    escaped_link = html.escape(deep_link, quote=True)
    escaped_sub = html.escape(sub_url, quote=True)
    js_link = json.dumps(deep_link)
    js_sub = json.dumps(sub_url)

    page = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Подключение — {client_label}</title>
<style>
  body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; background:#0d1117; color:#f0f6fc; display:flex; align-items:center; justify-content:center; min-height:100vh; padding:20px; box-sizing:border-box; }}
  .card {{ width:100%; max-width:380px; background:#161b22; border:1px solid #30363d; border-radius:16px; padding:24px; text-align:center; }}
  h1 {{ font-size:18px; margin:0 0 8px; }}
  p {{ font-size:13px; color:#8b949e; margin:0 0 18px; line-height:1.45; }}
  .btn {{ display:block; width:100%; box-sizing:border-box; padding:12px 16px; border-radius:10px; font-size:14px; font-weight:600; text-decoration:none; border:none; cursor:pointer; margin-bottom:10px; }}
  .btn-primary {{ background:#2f81f7; color:#fff; }}
  .btn-secondary {{ background:#21262d; color:#f0f6fc; border:1px solid #30363d; }}
  .status {{ font-size:12px; color:#58a6ff; min-height:16px; margin-top:4px; }}
</style>
</head>
<body>
  <div class="card">
    <h1>Открываем {client_label}…</h1>
    <p>Если приложение не открылось автоматически, нажмите кнопку ниже или скопируйте ключ.</p>
    <a class="btn btn-primary" href="{escaped_link}">Открыть в {client_label}</a>
    <button type="button" class="btn btn-secondary" onclick="copyKey()">Скопировать ключ</button>
    <div id="status" class="status" data-sub="{escaped_sub}"></div>
  </div>
  <script>
    const deepLink = {js_link};
    const subKey = {js_sub};
    function copyKey() {{
      if (navigator.clipboard && navigator.clipboard.writeText) {{
        navigator.clipboard.writeText(subKey).then(() => {{
          document.getElementById("status").textContent = "Ключ скопирован";
        }});
      }}
    }}
    setTimeout(() => {{ window.location.href = deepLink; }}, 120);
  </script>
</body>
</html>"""
    return web.Response(
        text=page,
        content_type="text/html",
        charset="utf-8",
        headers=_NO_CACHE_HEADERS,
    )


def setup_miniapp_routes(app: web.Application) -> None:
    """Register Mini App frontend shell, modular static assets, user and admin JSON APIs."""
    app.router.add_get("/app", miniapp_index_handler)
    app.router.add_get("/app/", miniapp_index_handler)
    app.router.add_get("/app/open-client", miniapp_open_client_handler)
    app.router.add_get("/app/assets/{asset_path:.+}", miniapp_asset_handler)

    app.router.add_get("/api/app/me", api_me_handler)

    # Admin 2FA & session
    app.router.add_post("/api/app/admin/2fa/setup", api_admin_2fa_setup_handler)
    app.router.add_post("/api/app/admin/2fa/verify", api_admin_2fa_verify_handler)

    # Admin nodes & users
    app.router.add_get("/api/app/admin/nodes", api_admin_nodes_list_handler)
    app.router.add_get("/api/app/admin/incy-settings", api_admin_incy_get_handler)
    app.router.add_put("/api/app/admin/incy-settings", api_admin_incy_put_handler)
    app.router.add_post("/api/app/admin/nodes/add", api_admin_nodes_add_handler)
    app.router.add_post("/api/app/admin/nodes/{server_id}/refresh", api_admin_nodes_refresh_handler)
    app.router.add_patch("/api/app/admin/nodes/{server_id}", api_admin_nodes_patch_handler)
    app.router.add_post("/api/app/admin/nodes/{server_id}/toggle", api_admin_nodes_toggle_handler)
    app.router.add_delete("/api/app/admin/nodes/{server_id}", api_admin_nodes_delete_handler)
    app.router.add_get("/api/app/admin/users", api_admin_users_handler)
    app.router.add_get("/api/app/admin/users/{user_id}/details", api_admin_user_details_handler)
    app.router.add_post("/api/app/admin/users/{user_id}/devices/reset", api_admin_user_devices_reset_handler)
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
    app.router.add_post("/api/app/user/devices/{hwid}/priority", api_user_device_priority_handler)

    logger.info("Registered modular Mini App routes: /app, /app/assets/*, /api/app/*")
