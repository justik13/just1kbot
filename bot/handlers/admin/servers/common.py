import logging
import math
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_admin_server_card_keyboard
from database.repositories.servers_repo import (
    get_server_count,
    get_server_peer_counts,
    get_servers_paginated,
)
from utils.telegram import safe
from utils.text_limits import truncate_button_text

logger = logging.getLogger(__name__)

SERVERS_PER_PAGE = 10

URL_REGEX = re.compile(
    r"^https?://"+
    r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|"+
    r"localhost|"+
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"+
    r"(?::\d+)?"+
    r"(?:/?|[/?]\S+)$",
    re.IGNORECASE,
)


def normalize_api_url(url: str) -> str:
    url = url.strip()
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, parts.fragment))


async def _build_servers_list_text_and_kb(
    servers, page: int, total_pages: int, total: int, session: AsyncSession | None = None,
) -> tuple[str, InlineKeyboardBuilder]:
    rendered = (
        texts.ADMIN_SERVERS_COMMON.format(v0=page, v1=total_pages, v2=total)
    )
    builder = InlineKeyboardBuilder()
    if not servers:
        rendered += texts.ADMIN_SERVERS_EMPTY
    else:
        from services.slots_cache import get_cached_peer_count
        db_counts = await get_server_peer_counts(session) if session is not None else {}
        for server in servers:
            flag = server.country_flag or texts.EMOJI_GLOBE
            status = texts.STATUS_ACTIVE_ICON if server.is_active else texts.STATUS_INACTIVE_ICON
            db_used = db_counts.get(server.id, 0)
            cached_used = get_cached_peer_count(server.id)
            total = server.max_clients or 240
            effective_used = max(cached_used, db_used) if cached_used is not None else db_used
            cap_str = f"{effective_used}/{total}"
            button_text = truncate_button_text(
                texts.ADMIN_SERVER_LIST_ROW_FORMAT.format(v0=status, v1=flag, v2=server.name, v3=cap_str)
            )
            builder.button(
                text=button_text,
                callback_data=f"admin_server_card:{server.id}",
            )
    if page > 1:
        builder.button(
            text=texts.ADMIN_BTN_PAGINATION_PREV,
            callback_data=f"admin_servers_page:{page - 1}",
        )
    if page < total_pages:
        builder.button(
            text=texts.ADMIN_BTN_PAGINATION_NEXT,
            callback_data=f"admin_servers_page:{page + 1}",
        )
    builder.button(
        text=texts.ADMIN_BTN_ADD_SERVER,
        callback_data="admin_server_add",
    )
    builder.button(
        text=texts.BTN_ADMIN_MENU,
        callback_data="admin_menu",
    )

    item_count = len(servers) if servers else 0
    adjust_pattern = [1] * item_count
    nav_buttons = (1 if page > 1 else 0) + (1 if page < total_pages else 0)
    if nav_buttons > 0:
        adjust_pattern.append(nav_buttons)
    adjust_pattern.extend([1, 1])

    builder.adjust(*adjust_pattern)
    return rendered, builder


async def _show_servers_list(
    callback: CallbackQuery, session: AsyncSession, page: int = 1,
):
    total_servers = await get_server_count(session)
    total_pages = max(1, math.ceil(total_servers / SERVERS_PER_PAGE))
    page = min(page, total_pages)
    servers = await get_servers_paginated(
        session, page=page, per_page=SERVERS_PER_PAGE,
    )
    rendered, kb = await _build_servers_list_text_and_kb(
        servers, page, total_pages, total_servers, session
    )
    try:
        await callback.message.edit_text(
            rendered, reply_markup=kb.as_markup(), parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"_show_servers_list edit_text failed: {e}")


async def _show_server_card(
    callback: CallbackQuery,
    session: AsyncSession,
    server: Any,
    ping_result: str | None = None,
) -> None:
    from bot.formatters import format_admin_breadcrumbs
    from database.models import Server

    if isinstance(server, int):
        server = await session.get(Server, server)
    if not server:
        await callback.answer(texts.ERROR_SERVER_NOT_FOUND, show_alert=True)
        return

    flag = server.country_flag or texts.EMOJI_GLOBE

    from utils.datetime_helpers import format_datetime_msk

    if server.is_active:
        status_line = texts.COMMON_AKTIVEN
        extra_status_info = ""
    elif server.disabled_reason == "AUTO_UNAVAILABLE":
        status_line = texts.COMMON_AVTOMATICHESKI_OTKLYUCHEN
        disabled_at_str = format_datetime_msk(server.disabled_at) if server.disabled_at else "—"
        last_check_str = format_datetime_msk(server.last_successful_check) if server.last_successful_check else "—"
        extra_status_info = (
            texts.COMMON_REASON_API_NEDOSTUPEN_NESTAB.format()+
            texts.COMMON_OTKLYUCHEN.format(disabled_at_str=disabled_at_str)+
            texts.COMMON_POSLEDNIY_OTKLIK.format(last_check_str=last_check_str)
        )
    else:
        status_line = texts.COMMON_OTKLYUCHEN_VRUCHNUYU
        extra_status_info = ""

    from services.slots_cache import get_cached_peer_count

    db_counts = await get_server_peer_counts(session)
    db_used = db_counts.get(server.id, 0)
    cached_used = get_cached_peer_count(server.id)
    effective_capacity = max(cached_used, db_used) if cached_used is not None else db_used
    actual_peers = cached_used
    max_clients = server.max_clients or 240
    header = format_admin_breadcrumbs(texts.BTN_SERVERS, f"{flag} {server.name}")

    if cached_used is not None and cached_used != db_used:
        slots_text = texts.ADMIN_SERVER_SLOTS_VALUE.format(
            used_clients=effective_capacity, max_clients=max_clients
        ) + texts.ADMIN_SERVER_SLOTS_BREAKDOWN_NOTE.format(
            cached_used=cached_used, db_used=db_used
        )
    else:
        slots_text = texts.ADMIN_SERVER_SLOTS_VALUE.format(
            used_clients=effective_capacity, max_clients=max_clients
        )

    rendered = (
        f"{header}"
        + texts.COMMON_KARTOCHKA_VPN_SERVER_ID.format(
            flag=flag, safe_server_name=safe(server.name), server_id=server.id
        )
        + texts.COMMON_STATUS_V_BOTE.format(status_line=status_line)
        + f"{extra_status_info}"
        + texts.COMMON_PROTOKOL.format(safe_server_protocol=safe(server.protocol))
        + texts.COMMON_ZAPOLNENNOST_SLOTOV.format(slots_text=slots_text)
        + texts.ADMIN_SERVER_API_URL.format(api_url=safe(server.api_url))
    )

    if ping_result:
        rendered += texts.COMMON_REZULTAT_PROVERKI_SVYAZI.format(ping_result=ping_result)

    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=get_admin_server_card_keyboard(
                server.id,
                server.is_active,
                used_clients=actual_peers,
                max_clients=max_clients,
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        if "not_modified" in str(e).lower().replace(" ", "_"):
            logger.debug("_show_server_card message not modified: %s", e)
        else:
            logger.warning("TelegramBadRequest in _show_server_card: %s", e)
