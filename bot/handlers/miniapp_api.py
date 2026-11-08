"""Telegram Mini App HTTP endpoints and API backend for Just1k 2.0."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
from urllib.parse import parse_qsl

import aiohttp
from aiohttp import web
from decimal import Decimal

from config.settings import get_settings
from database.connection import session_scope
from database.repositories import servers_repo, vless_subscription_repo, white_internet_repo
from database.repositories.account_ledger_repo import get_account_balance
from database.repositories.users_repo import get_user_by_telegram_id
from services.order_service import OrderService
from services.subscription import SubscriptionService
from utils.datetime_helpers import now_utc
from bot.formatters import format_days_left, format_subscription_date

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
MINIAPP_HTML_PATH = TEMPLATES_DIR / "miniapp.html"


def validate_telegram_init_data(init_data: str, bot_token: str) -> dict | None:
    """Validate Telegram WebApp initData string using HMAC-SHA256 according to Telegram specifications."""
    if not init_data:
        return None

    try:
        parsed_data = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = parsed_data.pop("hash", None)
        if not received_hash:
            return None

        # Build data-check-string (keys sorted alphabetically, joined by \n)
        data_check_list = [f"{k}={v}" for k, v in sorted(parsed_data.items())]
        data_check_string = "\n".join(data_check_list)

        # Secret key: HMAC-SHA256 of bot_token with key "WebAppData"
        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
        calculated_hash = hmac.new(
            secret_key, data_check_string.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(received_hash, calculated_hash):
            logger.warning("Telegram WebApp initData signature mismatch")
            return None

        user_raw = parsed_data.get("user")
        if user_raw:
            return json.loads(user_raw)
    except Exception as e:
        logger.warning("Failed to validate initData: %s", e)
        return None

    return None


async def _extract_authenticated_user(request: web.Request) -> dict | None:
    """Extract and authenticate Telegram user from request headers or query params."""
    settings = get_settings()
    init_data = request.headers.get("X-Telegram-Init-Data") or request.query.get("initData")

    if init_data:
        user_info = validate_telegram_init_data(init_data, settings.BOT_TOKEN)
        if user_info and "id" in user_info:
            return user_info

    # Local development fallback if DEV_MODE enabled
    if os.getenv("DEV_MODE") == "1" or settings.LOG_LEVEL.upper() == "DEBUG":
        test_uid = request.headers.get("X-Test-User-ID") or request.query.get("testUserId")
        if test_uid and test_uid.isdigit():
            return {"id": int(test_uid), "first_name": "DevUser", "username": "dev"}

    return None


async def miniapp_html_handler(request: web.Request) -> web.Response:
    """Serve single-page Telegram Mini App frontend."""
    if not MINIAPP_HTML_PATH.exists():
        return web.Response(status=404, text="Mini App template not found")

    content = MINIAPP_HTML_PATH.read_text(encoding="utf-8")
    return web.Response(
        text=content,
        content_type="text/html",
        charset="utf-8",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


async def api_me_handler(request: web.Request) -> web.Response:
    """Return user profile, active subscription details, connected HWID devices, and admin status."""
    tg_user = await _extract_authenticated_user(request)
    if not tg_user:
        return web.json_response({"ok": False, "error": "Unauthorized (invalid initData)"}, status=401)

    tg_id = int(tg_user["id"])
    settings = get_settings()

    async with session_scope() as session:
        db_user = await SubscriptionService.process_onboarding(
            session=session,
            telegram_id=tg_id,
            username=tg_user.get("username"),
            first_name=tg_user.get("first_name"),
        )
        if not db_user:
            return web.json_response({"ok": False, "error": "User not found"}, status=404)

        is_active = await SubscriptionService.check_access(session, db_user.telegram_id)
        is_admin = db_user.telegram_id in settings.ADMIN_IDS
        balance = await get_account_balance(session, user_id=db_user.id)
        device_limit = await SubscriptionService.get_effective_device_limit(session, db_user)

        vless_sub = await vless_subscription_repo.get_or_create_subscription(session, db_user.id)
        active_hwids_dict = vless_subscription_repo.prune_stale_hwids(vless_sub.active_hwids)

        devices_list = []
        for hwid, val in active_hwids_dict.items():
            if isinstance(val, dict):
                dev_name = val.get("name") or "Устройство"
                client = val.get("client") or "INCY / Happ"
                last_seen_dt = vless_subscription_repo._parse_hwid_last_seen_dt(val)
                is_blacklisted = bool(val.get("is_blacklisted", False))
            else:
                dev_name = "Устройство"
                client = "INCY / Happ"
                last_seen_dt = vless_subscription_repo._parse_hwid_last_seen_dt(val)
                is_blacklisted = False

            devices_list.append({
                "id": hwid,
                "name": dev_name,
                "client": client,
                "last_seen": last_seen_dt.strftime("%d.%m.%Y %H:%M") if last_seen_dt.year > 2000 else "Недавно",
                "is_blacklisted": is_blacklisted,
            })

        wi_sub = await white_internet_repo.get_subscription_by_user_id(session, db_user.id)
        now = now_utc()
        is_wi_active = bool(
            wi_sub
            and wi_sub.expires_at
            and wi_sub.expires_at > now
        )

        valid_until_str = (
            format_subscription_date(db_user.subscription_end)
            if db_user.subscription_end
            else "-"
        )
        days_left_str = (
            format_days_left(db_user.subscription_end)
            if db_user.subscription_end
            else "0 дн."
        )

        tariff_name = "Стандарт" if is_active else "Базовый"
        if db_user.current_tariff and getattr(db_user.current_tariff, "name", None):
            tariff_name = db_user.current_tariff.name

        sub_url = f"https://{settings.DOMAIN}/sub/{vless_sub.token}"

        data = {
            "ok": True,
            "user": {
                "id": db_user.telegram_id,
                "first_name": db_user.first_name or "",
                "username": db_user.username or "",
                "is_admin": is_admin,
            },
            "subscription": {
                "token": vless_sub.token,
                "sub_url": sub_url,
                "is_active": is_active,
                "status": "active" if is_active else "inactive",
                "valid_until": valid_until_str,
                "days_left": days_left_str,
                "tariff_name": tariff_name,
                "device_limit": device_limit,
                "devices_count": len(devices_list),
            },
            "devices": devices_list,
            "balance": {
                "real": int(balance.real_available),
                "bonus": int(balance.bonus_available),
            },
            "white_internet": {
                "is_active": is_wi_active,
                "valid_until": format_subscription_date(wi_sub.expires_at) if wi_sub and wi_sub.expires_at else "-",
                "traffic_used_gb": round((wi_sub.traffic_used_bytes or 0) / (1024**3), 1) if wi_sub else 0,
                "traffic_total_gb": round(((wi_sub.base_traffic_bytes or 0) + (wi_sub.extra_traffic_bytes or 0)) / (1024**3), 1) if wi_sub else 0,
                "device_limit": getattr(wi_sub, "device_limit", 1) if wi_sub else 1,
            },
        }
        return web.json_response(data)


async def api_devices_rename_handler(request: web.Request) -> web.Response:
    """Rename a friendly device label for an HWID slot."""
    tg_user = await _extract_authenticated_user(request)
    if not tg_user:
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

    hwid = body.get("hwid")
    new_name = body.get("name")
    if not hwid or not new_name:
        return web.json_response({"ok": False, "error": "hwid and name are required"}, status=400)

    async with session_scope() as session:
        db_user = await get_user_by_telegram_id(session, int(tg_user["id"]))
        if not db_user:
            return web.json_response({"ok": False, "error": "User not found"}, status=404)

        success = await vless_subscription_repo.rename_hwid(session, db_user.id, hwid, new_name)
        if not success:
            return web.json_response({"ok": False, "error": "Device not found"}, status=404)

        return web.json_response({"ok": True})


async def api_devices_delete_handler(request: web.Request) -> web.Response:
    """Disconnect and delete an HWID slot."""
    tg_user = await _extract_authenticated_user(request)
    if not tg_user:
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

    hwid = body.get("hwid")
    if not hwid:
        return web.json_response({"ok": False, "error": "hwid is required"}, status=400)

    async with session_scope() as session:
        db_user = await get_user_by_telegram_id(session, int(tg_user["id"]))
        if not db_user:
            return web.json_response({"ok": False, "error": "User not found"}, status=404)

        success = await vless_subscription_repo.remove_hwid(session, db_user.id, hwid)
        if not success:
            return web.json_response({"ok": False, "error": "Device not found"}, status=404)

        return web.json_response({"ok": True})


async def api_devices_blacklist_handler(request: web.Request) -> web.Response:
    """Add an HWID to blacklist."""
    tg_user = await _extract_authenticated_user(request)
    if not tg_user:
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

    hwid = body.get("hwid")
    if not hwid:
        return web.json_response({"ok": False, "error": "hwid is required"}, status=400)

    async with session_scope() as session:
        db_user = await get_user_by_telegram_id(session, int(tg_user["id"]))
        if not db_user:
            return web.json_response({"ok": False, "error": "User not found"}, status=404)

        success = await vless_subscription_repo.blacklist_hwid(session, db_user.id, hwid)
        if not success:
            return web.json_response({"ok": False, "error": "Device not found"}, status=404)

        return web.json_response({"ok": True})


async def api_devices_reset_handler(request: web.Request) -> web.Response:
    """Reset all device bindings and rotate client UUID/token."""
    tg_user = await _extract_authenticated_user(request)
    if not tg_user:
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

    settings = get_settings()
    async with session_scope() as session:
        db_user = await get_user_by_telegram_id(session, int(tg_user["id"]))
        if not db_user:
            return web.json_response({"ok": False, "error": "User not found"}, status=404)

        new_uuid, new_token = await vless_subscription_repo.reset_subscription_credentials(session, db_user.id)
        sub_url = f"https://{settings.DOMAIN}/sub/{new_token}"
        return web.json_response({"ok": True, "token": new_token, "sub_url": sub_url})


async def api_checkout_handler(request: web.Request) -> web.Response:
    """Create commercial order and generate YooKassa checkout URL."""
    tg_user = await _extract_authenticated_user(request)
    if not tg_user:
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

    service_type = body.get("service_type", "standard")
    duration_days = int(body.get("duration_days", 30))
    device_limit = int(body.get("device_limit", 2))
    traffic_gb = int(body.get("traffic_gb", 0))
    payment_method = body.get("payment_method", "yookassa")
    amount_rub = Decimal(str(body.get("amount_rub", "99")))

    bot_inst = request.app.get("bot")
    bot_username = (await bot_inst.get_me()).username if bot_inst else None

    async with session_scope() as session:
        db_user = await get_user_by_telegram_id(session, int(tg_user["id"]))
        if not db_user:
            return web.json_response({"ok": False, "error": "User not found"}, status=404)

        if payment_method == "wallet":
            try:
                order = await OrderService.pay_from_wallet(
                    session=session,
                    user_id=db_user.id,
                    service_type=service_type,
                    amount_rub=amount_rub,
                    duration_days=duration_days,
                    device_limit=device_limit,
                    traffic_bytes=traffic_gb * (1024**3),
                )
                return web.json_response({"ok": True, "success": True, "order_id": str(order.id)})
            except Exception as e:
                return web.json_response({"ok": False, "error": str(e)}, status=400)

        order = await OrderService.create_order(
            session=session,
            user_id=db_user.id,
            service_type=service_type,
            amount_rub=amount_rub,
            duration_days=duration_days,
            traffic_bytes=traffic_gb * (1024**3),
            device_limit=device_limit,
            payment_method="yookassa",
            bot_username=bot_username,
        )

        return web.json_response({
            "ok": True,
            "order_id": str(order.id),
            "payment_url": order.payment_url,
        })


async def api_admin_servers_handler(request: web.Request) -> web.Response:
    """Admin-only: list configured just1knode servers."""
    tg_user = await _extract_authenticated_user(request)
    if not tg_user:
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

    settings = get_settings()
    if int(tg_user["id"]) not in settings.ADMIN_IDS:
        return web.json_response({"ok": False, "error": "Access denied"}, status=403)

    async with session_scope() as session:
        servers = await servers_repo.get_all_servers(session)
        result = []
        for s in servers:
            result.append({
                "id": s.id,
                "name": s.name,
                "country_flag": s.country_flag or "🌐",
                "api_url": s.api_url,
                "protocol": s.protocol,
                "is_active": s.is_active,
                "health_state": s.health_state,
                "max_clients": s.max_clients,
            })
        return web.json_response({"ok": True, "servers": result})


async def api_admin_servers_add_handler(request: web.Request) -> web.Response:
    """Admin-only: add a new just1knode server with automated probe check."""
    tg_user = await _extract_authenticated_user(request)
    if not tg_user:
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

    settings = get_settings()
    if int(tg_user["id"]) not in settings.ADMIN_IDS:
        return web.json_response({"ok": False, "error": "Access denied"}, status=403)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

    name = (body.get("name") or "").strip()
    api_url = (body.get("api_url") or "").strip().rstrip("/")
    api_key = (body.get("api_key") or "").strip()
    country_flag = (body.get("country_flag") or "🌐").strip()

    if not name or not api_url or not api_key:
        return web.json_response({"ok": False, "error": "name, api_url, and api_key are required"}, status=400)

    # Perform probe check on just1knode
    probe_url = f"{api_url}/health"
    probe_ok = False
    probe_error = ""
    try:
        timeout = aiohttp.ClientTimeout(total=5.0)
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.get(probe_url, headers={"X-API-Key": api_key}) as resp:
                if resp.status in (200, 204):
                    probe_ok = True
                else:
                    probe_error = f"HTTP {resp.status}"
    except Exception as e:
        probe_error = str(e)

    async with session_scope() as session:
        server = await servers_repo.create_server(
            session=session,
            name=name,
            api_url=api_url,
            api_key=api_key,
            country_flag=country_flag,
            protocol="vless",
            is_active=True,
        )
        return web.json_response({
            "ok": True,
            "server_id": server.id,
            "probe_ok": probe_ok,
            "probe_error": probe_error if not probe_ok else None,
        })


def setup_miniapp_routes(app: web.Application) -> None:
    """Register all Telegram Mini App web and API routes."""
    app.router.add_get("/app", miniapp_html_handler)
    app.router.add_get("/app/", miniapp_html_handler)

    app.router.add_get("/api/app/me", api_me_handler)
    app.router.add_post("/api/app/devices/rename", api_devices_rename_handler)
    app.router.add_post("/api/app/devices/delete", api_devices_delete_handler)
    app.router.add_post("/api/app/devices/blacklist", api_devices_blacklist_handler)
    app.router.add_post("/api/app/devices/reset", api_devices_reset_handler)
    app.router.add_post("/api/app/checkout", api_checkout_handler)

    app.router.add_get("/api/app/admin/servers", api_admin_servers_handler)
    app.router.add_post("/api/app/admin/servers/add", api_admin_servers_add_handler)

    logger.info("Registered Telegram Mini App routes: GET /app and /api/app/*")
