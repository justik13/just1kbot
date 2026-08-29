import asyncio
import logging
import math

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot import texts
from bot.formatters import format_admin_breadcrumbs
from bot.keyboards.admin.servers import get_admin_server_peers_keyboard
from database.models import Server, VPNProfile
from database.repositories.profiles_repo import PROFILE_LIST_HIDDEN_STATUSES
from services.amnezia_client import AmneziaClient
from utils.admin import is_admin
from utils.datetime_helpers import now_utc
from utils.telegram import safe
from utils.text_limits import truncate_button_text

router = Router()
logger = logging.getLogger(__name__)

PEERS_PER_PAGE = 8


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

    # 1. Fetch bot profiles for this server
    stmt = (
        select(VPNProfile)
        .where(
            VPNProfile.server_id == server_id,
            VPNProfile.provisioning_status.notin_(PROFILE_LIST_HIDDEN_STATUSES),
        )
        .options(selectinload(VPNProfile.user))
        .order_by(VPNProfile.id.asc())
    )
    profiles = list((await session.scalars(stmt)).all())

    # 2. Fetch live peers from Amnezia node
    client = AmneziaClient(server.api_url, server.api_key)
    try:
        real_clients = await asyncio.wait_for(client.get_all_clients(), timeout=5.0) or []
    except Exception as e:
        logger.warning("Failed to fetch live clients for server %s: %s", server.id, e)
        real_clients = []

    real_peer_ids = {c.id for c in real_clients if getattr(c, "id", None)}
    db_profiles_by_peer_id = {p.peer_id: p for p in profiles if p.peer_id}

    # 3. Match items
    bot_items = []
    now = now_utc()
    for p in profiles:
        is_on_node = p.peer_id in real_peer_ids if p.peer_id else False
        user = getattr(p, "user", None)
        username = f"@{user.username}" if (user and user.username) else (f"ID {user.telegram_id}" if user else "—")
        first_name = safe(user.first_name) if (user and user.first_name) else ""
        fallback_device = texts.ADMIN_USERS_DEVICE_DEVICE.format(profile_id=p.id)
        device_name = safe(p.device_name or fallback_device)
        ip = safe(p.allocated_ip or texts.PLACEHOLDER_DASH)

        last_activity = getattr(p, "last_connected", None)
        is_online = False
        if last_activity:
            if last_activity.tzinfo is None:
                last_activity = last_activity.replace(tzinfo=now.tzinfo)
            delta_sec = (now - last_activity).total_seconds()
            if 0 <= delta_sec <= 180:
                is_online = True

        status_online = (
            texts.ADMIN_SERVER_PEER_STATUS_ONLINE
            if is_online
            else texts.ADMIN_SERVER_PEER_STATUS_OFFLINE
        )
        bot_items.append({
            "type": "bot",
            "profile": p,
            "user": user,
            "username": username,
            "first_name": first_name,
            "device_name": device_name,
            "ip": ip,
            "is_on_node": is_on_node,
            "status_online": status_online,
        })

    external_items = []
    for c in real_clients:
        if c.id not in db_profiles_by_peer_id:
            c_name = getattr(c, "client_name", None) or getattr(c, "peer_name", None) or getattr(c, "username", None) or texts.ADMIN_SERVER_PEERS_FALLBACK_DEVICE
            external_items.append({
                "type": "external",
                "client": c,
                "device_name": safe(c_name),
                "key": safe(c.id),
                "ip": texts.PLACEHOLDER_DASH,
            })

    all_items = bot_items + external_items
    total_items = len(all_items)
    total_pages = max(1, math.ceil(total_items / PEERS_PER_PAGE))
    page = min(max(1, page), total_pages)

    start_idx = (page - 1) * PEERS_PER_PAGE
    page_items = all_items[start_idx : start_idx + PEERS_PER_PAGE]

    flag = server.country_flag or texts.EMOJI_GLOBE
    header = format_admin_breadcrumbs(texts.BTN_SERVERS, f"{flag} {server.name}", texts.ADMIN_SERVER_PEERS_BREADCRUMB)

    rendered = texts.ADMIN_SERVER_PEERS_HEADER.format(
        header=header,
        flag=flag,
        server_name=safe(server.name),
        total_peers=total_items,
        bot_peers=len(bot_items),
        external_peers=len(external_items),
        page=page,
        total_pages=total_pages,
    )

    if not all_items:
        rendered += texts.ADMIN_SERVER_PEERS_EMPTY

    peer_buttons: list[tuple[str, str]] = []
    for item in page_items:
        if item["type"] == "bot":
            if item["is_on_node"]:
                rendered += texts.ADMIN_SERVER_PEER_BOT_ROW.format(
                    username=item["username"],
                    first_name=item["first_name"],
                    device_name=item["device_name"],
                    ip=item["ip"],
                    status_online=item["status_online"],
                )
            else:
                rendered += texts.ADMIN_SERVER_PEER_MISSING_ROW.format(
                    username=item["username"],
                    device_name=item["device_name"],
                    ip=item["ip"],
                )

            if item["user"]:
                btn_label = truncate_button_text(texts.ADMIN_SERVER_PEER_BTN_BOT.format(username=item["username"], device_name=item["device_name"]))
                btn_cb = f"admin_user_card:{item['user'].telegram_id}:server_peers:{server_id}:{page}"
                peer_buttons.append((btn_label, btn_cb))
        else:
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
        logger.debug("show_server_peers edit_text failed: %s", e)


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
