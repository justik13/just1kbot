import asyncio
from datetime import datetime, timezone
import logging
import math

import aiohttp
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, selectinload

from bot import texts
from bot.formatters import format_admin_breadcrumbs
from bot.keyboards.admin.servers import get_admin_server_peers_keyboard
from database.models import Server, User, VPNProfile
from services.amnezia_client import AmneziaClient
from utils.admin import is_admin
from utils.datetime_helpers import now_utc
from utils.telegram import safe
from utils.text_limits import truncate_button_text

router = Router()
logger = logging.getLogger(__name__)

PEERS_PER_PAGE = 8

NO_PEER_LIFECYCLE_STATUSES = {
    "pending_create",
    "create_cleanup_pending",
    "create_failed",
}


@router.callback_query(F.data.startswith("admin_server_peers:"))
async def show_server_peers(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    try:
        server_id = int(parts[1])
        page = int(parts[2])
    except ValueError:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    server = await session.get(Server, server_id)
    if not server:
        await callback.answer(texts.ERROR_SERVER_NOT_FOUND, show_alert=True)
        return

    await callback.answer(show_alert=False)

    # 1. Fetch bot profiles for this server (include all lifecycle states so
    # in-flight deleting profiles are not falsely classified as external)
    stmt = (
        select(VPNProfile)
        .where(VPNProfile.server_id == server_id)
        .options(
            load_only(
                VPNProfile.id,
                VPNProfile.server_id,
                VPNProfile.user_id,
                VPNProfile.peer_id,
                VPNProfile.device_name,
                VPNProfile.client_name,
                VPNProfile.last_connected,
                VPNProfile.provisioning_status,
            ),
            selectinload(VPNProfile.user).load_only(
                User.id,
                User.telegram_id,
                User.username,
                User.first_name,
            ),
        )
        .order_by(VPNProfile.id.asc())
    )
    profiles = list((await session.scalars(stmt)).all())

    # 2. Fetch live peers from Amnezia node.
    # IMPORTANT: get_all_clients() returns None on failure (not []).
    # We must preserve None to signal "API unavailable" vs [] for "truly empty node".
    client = AmneziaClient(server.api_url, server.api_key)
    api_available = True
    try:
        real_clients = await asyncio.wait_for(client.get_all_clients(), timeout=10.0)
        if real_clients is None:
            # API returned None — node responded but data is unavailable
            api_available = False
            real_clients = []
    except (asyncio.TimeoutError, TimeoutError, aiohttp.ClientError, OSError) as e:
        logger.warning("Failed to fetch live clients for server %s: %s", server.id, e)
        api_available = False
        real_clients = []
    except Exception as e:
        logger.exception("Unexpected error fetching live clients for server %s: %s", server.id, e)
        api_available = False
        real_clients = []

    real_peer_ids: set[str] = {c.id for c in real_clients if getattr(c, "id", None)}
    real_clients_by_id = {c.id: c for c in real_clients if getattr(c, "id", None)}
    db_profiles_by_peer_id = {p.peer_id: p for p in profiles if p.peer_id}

    # 3. Classify bot profiles: on-node vs missing vs pending
    bot_items_on_node = []
    bot_items_missing = []
    bot_items_pending = []
    now = now_utc()
    for p in profiles:
        is_on_node = (p.peer_id in real_peer_ids) if (p.peer_id and api_available) else False
        user = getattr(p, "user", None)
        u_name = f"@{user.username}" if (user and user.username) else (f"ID {user.telegram_id}" if user else "—")
        if len(u_name) > 32:
            u_name = u_name[:31] + "…"
        username = safe(u_name)

        f_name = user.first_name if (user and user.first_name) else ""
        if len(f_name) > 32:
            f_name = f_name[:31] + "…"
        first_name = safe(f_name)

        fallback_device = texts.ADMIN_USERS_DEVICE_DEVICE.format(profile_id=p.id)
        raw_dev = p.device_name or fallback_device
        if len(raw_dev) > 40:
            raw_dev = raw_dev[:39] + "…"
        device_name = safe(raw_dev)
        ip = safe(getattr(p, "allocated_ip", None) or getattr(p, "client_name", None) or texts.PLACEHOLDER_DASH)

        # Node-authoritative activity: check node telemetry first (lastHandshake/lastSeen/updatedAt),
        # falling back to database last_connected if unavailable
        node_client = real_clients_by_id.get(p.peer_id) if (p.peer_id and is_on_node) else None
        last_activity = None
        if node_client:
            last_conn_raw = (
                getattr(node_client, "lastHandshake", None)
                or getattr(node_client, "lastSeen", None)
                or getattr(node_client, "updatedAt", None)
            )
            if last_conn_raw:
                try:
                    ts = int(float(str(last_conn_raw)))
                    if ts > 1e12:
                        ts = ts // 1000
                    last_activity = datetime.fromtimestamp(ts, tz=timezone.utc)
                except (ValueError, TypeError, OverflowError):
                    pass

        if not last_activity:
            last_activity = getattr(p, "last_connected", None)

        is_online = False
        if last_activity:
            if last_activity.tzinfo is None:
                last_activity = last_activity.replace(tzinfo=now.tzinfo)
            delta_sec = (now - last_activity).total_seconds()
            if 0 <= delta_sec <= 180:
                is_online = True

        if p.provisioning_status == "deleting":
            status_online = texts.ADMIN_SERVER_PEER_STATUS_DELETING
        else:
            status_online = (
                texts.ADMIN_SERVER_PEER_STATUS_ONLINE
                if is_online
                else texts.ADMIN_SERVER_PEER_STATUS_OFFLINE
            )
        item = {
            "type": "bot",
            "profile": p,
            "user": user,
            "username": username,
            "first_name": first_name,
            "device_name": device_name,
            "ip": ip,
            "is_on_node": is_on_node,
            "status_online": status_online,
            "provisioning_status": p.provisioning_status,
        }
        if not api_available:
            bot_items_on_node.append(item)
        elif is_on_node:
            bot_items_on_node.append(item)
        elif p.peer_id:
            # Had a peer_id in DB, but physically absent from node!
            item["type"] = "missing"
            bot_items_missing.append(item)
        elif p.provisioning_status in NO_PEER_LIFECYCLE_STATUSES:
            # Legitimate unprovisioned lifecycle state
            item["type"] = "pending"
            bot_items_pending.append(item)
        else:
            # Anomalous state: no peer_id and not in known lifecycle statuses
            logger.warning(
                "Anomalous VPNProfile %s on server %s: no peer_id with status '%s'",
                p.id,
                server_id,
                p.provisioning_status,
            )
            item["type"] = "pending"
            bot_items_pending.append(item)

    # 4. External (untracked) peers: on node but not in DB
    external_items = []
    for c in real_clients:
        if c.id not in db_profiles_by_peer_id:
            raw_c_name = (
                getattr(c, "client_name", None)
                or getattr(c, "peer_name", None)
                or getattr(c, "username", None)
                or texts.ADMIN_SERVER_PEERS_FALLBACK_DEVICE
            )
            if len(raw_c_name) > 40:
                raw_c_name = raw_c_name[:39] + "…"
            ext_ip = getattr(c, "ip", None) or getattr(c, "allocated_ip", None) or texts.PLACEHOLDER_DASH
            cid = str(c.id)
            c_key = f"{cid[:6]}…{cid[-6:]}" if len(cid) > 16 else cid
            external_items.append({
                "type": "external",
                "client": c,
                "device_name": safe(raw_c_name),
                "key": safe(c_key),
                "ip": safe(str(ext_ip)),
            })

    # 5. Page layout: bot (on-node) first, then external, then missing, then pending
    # Semantics: live_peers = peers confirmed on node; missing = DB-only profiles; pending = unprovisioned
    live_peers = len(bot_items_on_node) + len(external_items)
    missing_count = len(bot_items_missing)
    pending_count = len(bot_items_pending)
    all_items = bot_items_on_node + external_items + bot_items_missing + bot_items_pending

    total_items = len(all_items)
    total_pages = max(1, math.ceil(total_items / PEERS_PER_PAGE))
    page = min(max(1, page), total_pages)

    start_idx = (page - 1) * PEERS_PER_PAGE
    page_items = all_items[start_idx : start_idx + PEERS_PER_PAGE]

    flag = server.country_flag or texts.EMOJI_GLOBE
    header = format_admin_breadcrumbs(texts.BTN_SERVERS, f"{flag} {server.name}", texts.ADMIN_SERVER_PEERS_BREADCRUMB)

    if not api_available:
        status_banner = texts.ADMIN_SERVER_PEERS_API_ERROR_BANNER
        rendered = texts.ADMIN_SERVER_PEERS_HEADER_DEGRADED.format(
            header=header,
            flag=flag,
            server_name=safe(server.name),
            status_banner=status_banner,
            db_profiles_count=len(profiles),
            page=page,
            total_pages=total_pages,
        )
    else:
        status_banner = ""
        deleting_count = sum(
            1 for item in bot_items_on_node if item.get("provisioning_status") == "deleting"
        )
        deleting_note = (
            texts.ADMIN_SERVER_PEERS_DELETING_NOTE.format(deleting_count=deleting_count)
            if deleting_count > 0
            else ""
        )
        missing_note = (
            texts.ADMIN_SERVER_PEERS_MISSING_NOTE.format(missing_count=missing_count)
            if missing_count > 0
            else ""
        )
        pending_note = (
            texts.ADMIN_SERVER_PEERS_PENDING_NOTE.format(pending_count=pending_count)
            if pending_count > 0
            else ""
        )
        rendered = texts.ADMIN_SERVER_PEERS_HEADER.format(
            header=header,
            flag=flag,
            server_name=safe(server.name),
            status_banner=status_banner,
            live_peers=live_peers,
            bot_peers=len(bot_items_on_node),
            external_peers=len(external_items),
            deleting_note=deleting_note,
            missing_note=missing_note,
            pending_note=pending_note,
            page=page,
            total_pages=total_pages,
        )

    if not all_items:
        rendered += texts.ADMIN_SERVER_PEERS_EMPTY

    peer_buttons: list[tuple[str, str]] = []
    for item in page_items:
        if not api_available:
            rendered += texts.ADMIN_SERVER_PEER_UNKNOWN_ROW.format(
                username=item["username"],
                first_name=item["first_name"],
                device_name=item["device_name"],
                ip=item["ip"],
            )
            if item["user"]:
                btn_label = truncate_button_text(texts.ADMIN_SERVER_PEER_BTN_BOT.format(username=item["username"], device_name=item["device_name"]))
                btn_cb = f"admin_user_card:{item['user'].telegram_id}:server_peers:{server_id}:{page}"
                peer_buttons.append((btn_label, btn_cb))
        elif item["type"] == "bot":
            rendered += texts.ADMIN_SERVER_PEER_BOT_ROW.format(
                username=item["username"],
                first_name=item["first_name"],
                device_name=item["device_name"],
                ip=item["ip"],
                status_online=item["status_online"],
            )
            if item["user"]:
                btn_label = truncate_button_text(texts.ADMIN_SERVER_PEER_BTN_BOT.format(username=item["username"], device_name=item["device_name"]))
                btn_cb = f"admin_user_card:{item['user'].telegram_id}:server_peers:{server_id}:{page}"
                peer_buttons.append((btn_label, btn_cb))
        elif item["type"] == "missing":
            rendered += texts.ADMIN_SERVER_PEER_MISSING_ROW.format(
                username=item["username"],
                device_name=item["device_name"],
                ip=item["ip"],
            )
            if item["user"]:
                btn_label = truncate_button_text(texts.ADMIN_SERVER_PEER_BTN_BOT.format(username=item["username"], device_name=item["device_name"]))
                btn_cb = f"admin_user_card:{item['user'].telegram_id}:server_peers:{server_id}:{page}"
                peer_buttons.append((btn_label, btn_cb))
        elif item["type"] == "pending":
            p_status = item.get("provisioning_status", "pending_create")
            if p_status == "pending_create":
                badge = texts.ADMIN_SERVER_PEER_BADGE_PENDING
                icon = "⏳"
            elif p_status == "create_cleanup_pending":
                badge = texts.ADMIN_SERVER_PEER_BADGE_CLEANUP
                icon = "🧹"
            elif p_status == "create_failed":
                badge = texts.ADMIN_SERVER_PEER_BADGE_FAILED
                icon = "❌"
            else:
                badge = texts.ADMIN_SERVER_PEER_BADGE_DEFAULT.format(status=p_status)
                icon = "⏳"

            rendered += texts.ADMIN_SERVER_PEER_PENDING_ROW.format(
                badge=badge,
                username=item["username"],
                device_name=item["device_name"],
                ip=item["ip"],
                status=p_status,
            )
            if item["user"]:
                btn_label = truncate_button_text(
                    texts.ADMIN_SERVER_PEER_BTN_PENDING.format(
                        icon=icon,
                        username=item["username"],
                        device_name=item["device_name"],
                    )
                )
                btn_cb = f"admin_user_card:{item['user'].telegram_id}:server_peers:{server_id}:{page}"
                peer_buttons.append((btn_label, btn_cb))
        else:  # external
            rendered += texts.ADMIN_SERVER_PEER_EXTERNAL_ROW.format(
                device_name=item["device_name"],
                ip=item["ip"],
                key=item["key"][:16] + "...",
            )
            btn_label = truncate_button_text(texts.ADMIN_SERVER_PEER_BTN_EXTERNAL.format(device_name=item["device_name"]))
            btn_cb = f"admin_server_peer_info:{server_id}"
            peer_buttons.append((btn_label, btn_cb))

    kb = get_admin_server_peers_keyboard(
        server_id=server_id,
        page=page,
        total_pages=total_pages,
        peers_buttons=peer_buttons,
    )

    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=kb,
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        if "not_modified" in str(e).lower().replace(" ", "_"):
            logger.debug("show_server_peers message not modified: %s", e)
        else:
            logger.warning("TelegramBadRequest in show_server_peers: %s", e)


@router.callback_query(F.data.startswith("admin_server_peer_info:"))
async def show_server_peer_info(
    callback: CallbackQuery,
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    await callback.answer(
        texts.ADMIN_SERVER_PEER_INFO_ALERT,
        show_alert=True,
    )
