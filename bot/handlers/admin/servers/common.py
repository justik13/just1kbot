import logging
import math
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_admin_server_card_keyboard
from config.constants import AMNEZIA_PROTOCOLS
from database.repositories.servers_repo import (
    get_server_peer_counts,
    is_white_internet_origin_server,
    is_xray_server,
)
from utils.datetime_helpers import now_utc
from utils.formatters import format_traffic
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
    servers,
    page: int,
    total_pages: int,
    total: int,
    session: AsyncSession | None = None,
    filter_type: str | None = None,
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
        vless_sub_count: int | None = None
        if any(
            (str(getattr(s, "protocol", "") or "").lower() == "vless" or "vless" in (getattr(s, "capabilities", None) or []))
            for s in servers
        ) and session is not None:
            from database.models import VlessSubscription
            vless_sub_count = (
                await session.execute(
                    select(func.count(VlessSubscription.id)).where(VlessSubscription.is_active.is_(True))
                )
            ).scalar_one_or_none() or 0

        for server in servers:
            flag = server.country_flag or texts.EMOJI_GLOBE
            status = texts.STATUS_ACTIVE_ICON if server.is_active else texts.STATUS_INACTIVE_ICON
            caps = getattr(server, "capabilities", None) or []
            proto = str(getattr(server, "protocol", "") or "").lower()
            has_awg = proto in AMNEZIA_PROTOCOLS or "awg" in caps
            has_vless = "vless" in caps or "xray_vless" in caps or proto == "vless"
            has_wl = is_xray_server(server)

            if has_awg and has_vless:
                proto_badge = "[AWG+VLESS]"
            elif has_vless and not has_wl:
                proto_badge = "[VLESS]"
            elif has_wl:
                proto_badge = "[Origin РФ]"
            else:
                proto_badge = "[AWG]"

            is_pure_vless = has_vless and not has_awg and not has_wl
            if is_pure_vless:
                cap_str = f"{vless_sub_count or 0} подп."
            else:
                db_used = db_counts.get(server.id, 0)
                cached_used = get_cached_peer_count(server.id)
                total = server.max_clients or 240
                effective_used = max(cached_used, db_used) if cached_used is not None else db_used
                cap_str = f"{effective_used}/{total}"
            server_display_name = f"{proto_badge} {server.name}"
            button_text = truncate_button_text(
                texts.ADMIN_SERVER_LIST_ROW_FORMAT.format(v0=status, v1=flag, v2=server_display_name, v3=cap_str)
            )
            builder.button(
                text=button_text,
                callback_data=f"admin_server_card:{server.id}",
            )
    if page > 1:
        prev_cb = f"admin_servers_page:{filter_type}:{page - 1}" if filter_type else f"admin_servers_page:{page - 1}"
        builder.button(
            text=texts.ADMIN_BTN_PAGINATION_PREV,
            callback_data=prev_cb,
        )
    if page < total_pages:
        next_cb = f"admin_servers_page:{filter_type}:{page + 1}" if filter_type else f"admin_servers_page:{page + 1}"
        builder.button(
            text=texts.ADMIN_BTN_PAGINATION_NEXT,
            callback_data=next_cb,
        )
    add_cb = f"admin_server_add:{filter_type}" if filter_type else "admin_server_add"
    builder.button(
        text=texts.ADMIN_BTN_ADD_SERVER,
        callback_data=add_cb,
    )
    if filter_type == "std":
        back_text = texts.BTN_BACK
        back_cb = "admin_cat_std_sub"
    elif filter_type == "wi":
        back_text = texts.BTN_BACK
        back_cb = "admin_cat_wi_sub"
    else:
        back_text = texts.BTN_ADMIN_MENU
        back_cb = "admin_menu"
    builder.button(
        text=back_text,
        callback_data=back_cb,
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
    callback: CallbackQuery,
    session: AsyncSession,
    page: int = 1,
    filter_type: str | None = None,
):
    from database.models import Server

    result = await session.execute(select(Server).order_by(Server.name))
    all_servers = list(result.scalars().all())

    if filter_type == "std":
        servers = [s for s in all_servers if not is_white_internet_origin_server(s)]
    elif filter_type == "wi":
        servers = [s for s in all_servers if is_white_internet_origin_server(s)]
    else:
        servers = all_servers

    total_servers = len(servers)
    total_pages = max(1, math.ceil(total_servers / SERVERS_PER_PAGE))
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * SERVERS_PER_PAGE
    page_servers = servers[start_idx : start_idx + SERVERS_PER_PAGE]

    rendered, kb = await _build_servers_list_text_and_kb(
        page_servers, page, total_pages, total_servers, session, filter_type=filter_type
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
    caps = getattr(server, "capabilities", None) or []
    proto = str(getattr(server, "protocol", "") or "").lower()
    has_vless = "vless" in caps or "xray_vless" in caps or proto == "vless"
    has_awg = proto in AMNEZIA_PROTOCOLS or "awg" in caps or "amnezia" in caps
    is_origin = is_white_internet_origin_server(server)
    section_crumb = texts.BTN_WI_SERVERS if is_origin else texts.BTN_STD_SERVERS
    header = format_admin_breadcrumbs(section_crumb, f"{flag} {server.name}")

    is_pure_vless = has_vless and not has_awg and not is_origin
    if is_pure_vless:
        from database.models import VlessSubscription

        vless_sub_count = (
            await session.execute(
                select(func.count(VlessSubscription.id)).where(VlessSubscription.is_active.is_(True))
            )
        ).scalar_one_or_none() or 0
        slots_text = texts.ADMIN_SERVER_SLOTS_VLESS.format(vless_sub_count=vless_sub_count)
    elif cached_used is not None and cached_used != db_used:
        slots_text = texts.ADMIN_SERVER_SLOTS_VALUE.format(
            used_clients=effective_capacity, max_clients=max_clients
        ) + texts.ADMIN_SERVER_SLOTS_BREAKDOWN_NOTE.format(
            cached_used=cached_used, db_used=db_used
        )
    else:
        slots_text = texts.ADMIN_SERVER_SLOTS_VALUE.format(
            used_clients=effective_capacity, max_clients=max_clients
        )

    server_extra = getattr(server, "extra_data", {}) or {}
    client_cycle = server_extra.get("traffic_cycle")
    host_cycle = server_extra.get("host_traffic_cycle")
    current_cycle = now_utc().strftime("%Y-%m")
    monthly_bytes = int(server_extra.get("monthly_traffic_bytes", 0) or 0) if client_cycle == current_cycle else 0
    host_bytes = int(server_extra.get("host_monthly_traffic_bytes", 0) or 0) if host_cycle == current_cycle else 0
    traffic_month = format_traffic(monthly_bytes)
    if host_bytes > 0:
        traffic_month += texts.ADMIN_SERVER_HOST_TRAFFIC_SUFFIX.format(
            host_traffic=format_traffic(host_bytes)
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
        + texts.ADMIN_SERVER_MONTHLY_TRAFFIC.format(traffic_month=traffic_month)
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
                is_xray=is_origin,
                has_vless=has_vless,
                is_origin=is_origin,
                has_awg=has_awg,
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        if "not_modified" in str(e).lower().replace(" ", "_"):
            logger.debug("_show_server_card message not modified: %s", e)
        else:
            logger.warning("TelegramBadRequest in _show_server_card: %s", e)
