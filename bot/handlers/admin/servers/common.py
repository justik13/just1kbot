import logging
import math
import re
from urllib.parse import urlsplit, urlunsplit

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_admin_server_card_keyboard
from database.repositories.servers_repo import (
    get_server_count,
    get_servers_paginated,
)
from utils.telegram import safe
from utils.text_limits import truncate_button_text

logger = logging.getLogger(__name__)

SERVERS_PER_PAGE = 10

URL_REGEX = re.compile(
    r"^https?://"
    r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|"
    r"localhost|"
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"
    r"(?::\d+)?"
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
    servers, page: int, total_pages: int, total: int,
) -> tuple[str, InlineKeyboardBuilder]:
    rendered = (
        texts.RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L48_1.format(value_0=page, value_1=total_pages, value_2=total)
    )
    builder = InlineKeyboardBuilder()
    if not servers:
        rendered += texts.RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L53_1
    else:
        for server in servers:
            flag = server.country_flag or texts.RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L56_1
            status = texts.RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L57_1 if server.is_active else texts.STATUS_INACTIVE_ICON
            button_text = truncate_button_text(
                texts.RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L59_1.format(value_0=status, value_1=flag, value_2=server.name, value_3=server.protocol)
            )
            builder.button(
                text=button_text,
                callback_data=f"admin_server_card:{server.id}",
            )
    if page > 1:
        builder.button(
            text=texts.UI_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L67_1,
            callback_data=f"admin_servers_page:{page - 1}",
        )
    if page < total_pages:
        builder.button(
            text=texts.UI_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L72_1,
            callback_data=f"admin_servers_page:{page + 1}",
        )
    builder.button(text=texts.UI_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L75_1, callback_data="admin_server_add")
    builder.button(text=texts.UI_BOT_HANDLERS_ADMIN_SERVERS_COMMON_L76_1, callback_data="admin_menu")
    builder.adjust(1)
    return rendered, builder


async def _show_servers_list(
    callback: CallbackQuery, session: AsyncSession, page: int = 1,
):
    total_servers = await get_server_count(session)
    total_pages = max(1, math.ceil(total_servers / SERVERS_PER_PAGE))
    if page > total_pages:
        page = total_pages
    servers = await get_servers_paginated(
        session, page=page, per_page=SERVERS_PER_PAGE,
    )
    rendered, kb = await _build_servers_list_text_and_kb(
        servers, page, total_pages, total_servers,
    )
    try:
        await callback.message.edit_text(
            rendered, reply_markup=kb.as_markup(), parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"_show_servers_list edit_text failed: {e}")


async def _show_server_card(
    callback: CallbackQuery, session: AsyncSession, server, ping_result: str | None = None
):
    from utils.formatters import format_admin_breadcrumbs
    flag = server.country_flag or "🌐"
    status = "🟢 Активен" if server.is_active else "🔴 Отключен"
    used_clients = getattr(server, "current_clients_count", 0) or 0
    max_clients = server.max_clients or 240
    header = format_admin_breadcrumbs("🖥 Серверы", f"{flag} {server.name}")

    rendered = (
        f"{header}"
        f"🖥 <b>Карточка VPN-сервера {flag} {safe(server.name)}</b> (ID: {server.id})\n\n"
        f"• Статус в боте: <b>{status}</b>\n"
        f"• Протокол: <code>{server.protocol}</code>\n"
        f"• Заполненность слотов: <b>{used_clients} / {max_clients}</b>\n"
        f"• API URL: <code>{safe(server.api_url)}</code>\n"
    )

    if ping_result:
        rendered += f"\n⚡ <b>Результат проверки связи:</b>\n{ping_result}\n"

    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=get_admin_server_card_keyboard(
                server.id, server.is_active,
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"_show_server_card edit_text failed: {e}")

