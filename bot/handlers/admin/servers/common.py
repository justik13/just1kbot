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
        f"🛠 Админка › 🌍 <b>Серверы</b>\n"
        f"(стр. {page}/{total_pages}) · Всего: {total}\n"
    )
    builder = InlineKeyboardBuilder()
    if not servers:
        rendered += "<i>Серверов пока нет</i>\n"
    else:
        for server in servers:
            flag = server.country_flag or "🌍"
            status = "🟢" if server.is_active else "🔴"
            button_text = truncate_button_text(
                f"{status} {flag} {server.name} · {server.protocol}"
            )
            builder.button(
                text=button_text,
                callback_data=f"admin_server_card:{server.id}",
            )
    if page > 1:
        builder.button(
            text="⬅️",
            callback_data=f"admin_servers_page:{page - 1}",
        )
    if page < total_pages:
        builder.button(
            text="➡️",
            callback_data=f"admin_servers_page:{page + 1}",
        )
    builder.button(text="➕ Добавить сервер", callback_data="admin_server_add")
    builder.button(text="← В админку", callback_data="admin_menu")
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
    callback: CallbackQuery, session: AsyncSession, server,
):
    flag = server.country_flag or "🌍"
    status = "🟢 Активен" if server.is_active else "🔴 Отключен"
    rendered = texts.ADMIN_SERVER_CARD.format(
        flag=flag,
        name=safe(server.name),
        id=server.id,
        status=status,
        protocol=server.protocol,
        api_url=safe(server.api_url),
        max_clients=server.max_clients,
    )
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
