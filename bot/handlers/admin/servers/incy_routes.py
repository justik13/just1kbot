"""Admin handlers for configuring INCY subscription appearance."""

from __future__ import annotations

import logging
import os
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from bot.keyboards.admin.servers import (
    get_admin_server_incy_keyboard,
    get_admin_server_incy_relay_actions_keyboard,
    get_admin_server_incy_relays_keyboard,
)
from bot.states import AdminStates
from config.constants import (
    WHITE_INTERNET_CHANNEL_URL,
    WHITE_INTERNET_PROFILE_DESCRIPTION,
    WHITE_INTERNET_PROFILE_TITLE,
    WHITE_INTERNET_SUPPORT_URL,
)
from database.repositories.servers_repo import get_server_by_id, update_server
from utils.admin import is_admin
from utils.callbacks import parse_callback_id
from utils.telegram import safe

router = Router()
logger = logging.getLogger(__name__)


def _get_server_incy_details(server: Any) -> dict[str, str]:
    """Extract and format current INCY subscription settings for a server."""
    extra = server.extra_data if isinstance(getattr(server, "extra_data", None), dict) else {}
    bot_user = os.getenv("BOT_USERNAME", "just1kbot").lstrip("@")
    default_bot_url = f"https://t.me/{bot_user}"

    title = extra.get("profile_title") or WHITE_INTERNET_PROFILE_TITLE or texts.WL_PROFILE_NAME
    description = extra.get("profile_description") or WHITE_INTERNET_PROFILE_DESCRIPTION

    origin_name = extra.get("origin_tag") or getattr(server, "name", None) or texts.WL_ORIGIN_VLESS_TAG
    origin_badge = extra.get("origin_badge")

    channel_url = extra.get("channel_url") or WHITE_INTERNET_CHANNEL_URL or default_bot_url
    support_url = extra.get("support_url") or WHITE_INTERNET_SUPPORT_URL or default_bot_url

    return {
        "title": title or texts.WL_PROFILE_NAME,
        "description": description or texts.ADMIN_SERVER_INCY_VALUE_NONE,
        "origin_name": origin_name,
        "origin_badge": origin_badge or texts.ADMIN_SERVER_INCY_VALUE_NONE,
        "channel_url": channel_url,
        "support_url": support_url,
    }


@router.callback_query(F.data.startswith("admin_server_incy:"))
async def show_server_incy_card(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    server_id = parse_callback_id(callback.data, 1)
    if server_id is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    await callback.answer(show_alert=False)
    await state.clear()

    server = await get_server_by_id(session, server_id)
    if not server:
        await callback.answer(texts.ERROR_SERVER_NOT_FOUND, show_alert=True)
        return

    details = _get_server_incy_details(server)
    flag = server.country_flag or texts.EMOJI_GLOBE
    card_text = texts.ADMIN_SERVER_INCY_CARD.format(
        flag=flag,
        name=safe(server.name),
        title=safe(details["title"]),
        description=safe(details["description"]),
        origin_name=safe(details["origin_name"]),
        origin_badge=safe(details["origin_badge"]),
        channel_url=safe(details["channel_url"]),
        support_url=safe(details["support_url"]),
    )

    try:
        await callback.message.edit_text(
            card_text,
            reply_markup=get_admin_server_incy_keyboard(server_id),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"show_server_incy_card edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_server_incy_edit:"))
async def start_edit_server_incy_param(
    callback: CallbackQuery,
    state: FSMContext,
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
    except ValueError:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    param = parts[2]
    server = await get_server_by_id(session, server_id)
    if not server:
        await callback.answer(texts.ERROR_SERVER_NOT_FOUND, show_alert=True)
        return

    await callback.answer(show_alert=False)
    await state.clear()

    details = _get_server_incy_details(server)
    prompt_map = {
        "title": (texts.ADMIN_SERVER_INCY_PROMPT_TITLE, details["title"]),
        "desc": (texts.ADMIN_SERVER_INCY_PROMPT_DESC, details["description"]),
        "origin_name": (texts.ADMIN_SERVER_INCY_PROMPT_ORIGIN_NAME, details["origin_name"]),
        "origin_badge": (texts.ADMIN_SERVER_INCY_PROMPT_ORIGIN_BADGE, details["origin_badge"]),
        "channel": (texts.ADMIN_SERVER_INCY_PROMPT_CHANNEL, details["channel_url"]),
        "support": (texts.ADMIN_SERVER_INCY_PROMPT_SUPPORT, details["support_url"]),
    }

    if param not in prompt_map:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    prompt_tpl, cur_val = prompt_map[param]
    prompt_text = prompt_tpl.format(current=safe(cur_val))

    await state.update_data(server_id=server_id, incy_param=param)
    await state.set_state(AdminStates.editing_server_incy_param)

    try:
        await callback.message.edit_text(
            prompt_text,
            reply_markup=get_back_button(f"admin_server_incy:{server_id}"),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"start_edit_server_incy_param edit_text failed: {e}")


@router.message(AdminStates.editing_server_incy_param)
async def process_server_incy_param_input(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if not is_admin(message.from_user.id):
        await message.answer(texts.ERROR_ACCESS_DENIED)
        return

    data = await state.get_data()
    server_id = data.get("server_id")
    param = data.get("incy_param")

    if not server_id or not param:
        await state.clear()
        return

    server = await get_server_by_id(session, server_id)
    if not server:
        await message.answer(texts.ERROR_SERVER_NOT_FOUND)
        await state.clear()
        return

    raw_text = (message.text or "").strip()
    if raw_text == "/cancel":
        await state.clear()
        details = _get_server_incy_details(server)
        flag = server.country_flag or texts.EMOJI_GLOBE
        card_text = texts.ADMIN_SERVER_INCY_CARD.format(
            flag=flag,
            name=safe(server.name),
            title=safe(details["title"]),
            description=safe(details["description"]),
            origin_name=safe(details["origin_name"]),
            origin_badge=safe(details["origin_badge"]),
            channel_url=safe(details["channel_url"]),
            support_url=safe(details["support_url"]),
        )
        await message.answer(
            card_text,
            reply_markup=get_admin_server_incy_keyboard(server_id),
            parse_mode="HTML",
        )
        return

    is_clear = raw_text in ("/clear", "-", "none")
    val = "" if is_clear else raw_text

    extra = dict(server.extra_data or {})
    param_key_map = {
        "title": "profile_title",
        "desc": "profile_description",
        "origin_name": "origin_tag",
        "origin_badge": "origin_badge",
        "channel": "channel_url",
        "support": "support_url",
    }

    key = param_key_map.get(param)
    if key:
        if is_clear:
            extra[key] = ""
        else:
            if param in ("title", "origin_name", "origin_badge"):
                val = val[:30]
            elif param == "desc":
                val = val[:50]
            extra[key] = val

        await update_server(session, server, extra_data=extra)

    await state.clear()
    await session.refresh(server)

    details = _get_server_incy_details(server)
    flag = server.country_flag or texts.EMOJI_GLOBE
    card_text = f"{texts.ADMIN_SERVER_INCY_SAVED}\n\n" + texts.ADMIN_SERVER_INCY_CARD.format(
        flag=flag,
        name=safe(server.name),
        title=safe(details["title"]),
        description=safe(details["description"]),
        origin_name=safe(details["origin_name"]),
        origin_badge=safe(details["origin_badge"]),
        channel_url=safe(details["channel_url"]),
        support_url=safe(details["support_url"]),
    )

    await message.answer(
        card_text,
        reply_markup=get_admin_server_incy_keyboard(server_id),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin_server_incy_relays:"))
async def show_server_incy_relays(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    server_id = parse_callback_id(callback.data, 1)
    if server_id is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    await callback.answer(show_alert=False)
    await state.clear()

    server = await get_server_by_id(session, server_id)
    if not server:
        await callback.answer(texts.ERROR_SERVER_NOT_FOUND, show_alert=True)
        return

    extra = server.extra_data or {}
    relays = extra.get("relays", [])
    custom_names = extra.get("relay_names", {})
    custom_badges = extra.get("relay_badges", {})

    if not relays:
        await callback.answer(texts.ADMIN_SERVER_INCY_NO_RELAYS, show_alert=True)
        return

    try:
        await callback.message.edit_text(
            texts.ADMIN_SERVER_INCY_RELAYS_TITLE,
            reply_markup=get_admin_server_incy_relays_keyboard(
                server_id, relays, custom_names, custom_badges
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"show_server_incy_relays edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_server_incy_relay_view:"))
async def show_server_incy_relay_card(
    callback: CallbackQuery,
    state: FSMContext,
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
    except ValueError:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    relay_code = parts[2]
    server = await get_server_by_id(session, server_id)
    if not server:
        await callback.answer(texts.ERROR_SERVER_NOT_FOUND, show_alert=True)
        return

    await callback.answer(show_alert=False)
    await state.clear()

    extra = server.extra_data or {}
    relays = extra.get("relays", [])
    custom_names = extra.get("relay_names", {})
    custom_badges = extra.get("relay_badges", {})

    relay = next((r for r in relays if (r.get("code") or r.get("name")) == relay_code), None)
    base_name = relay.get("name") if relay else relay_code
    custom_name = custom_names.get(relay_code) or base_name
    custom_badge = (
        custom_badges.get(relay_code)
        or (relay.get("badge") if relay else None)
        or texts.ADMIN_SERVER_INCY_VALUE_NONE
    )

    card_text = texts.ADMIN_SERVER_INCY_RELAY_CARD.format(
        relay_name=safe(base_name),
        relay_code=safe(relay_code),
        custom_name=safe(custom_name),
        custom_badge=safe(custom_badge),
    )

    try:
        await callback.message.edit_text(
            card_text,
            reply_markup=get_admin_server_incy_relay_actions_keyboard(server_id, relay_code),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"show_server_incy_relay_card edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_server_incy_relay_edit_name:"))
async def start_edit_relay_specific_name(
    callback: CallbackQuery,
    state: FSMContext,
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
    except ValueError:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    relay_code = parts[2]
    server = await get_server_by_id(session, server_id)
    if not server:
        await callback.answer(texts.ERROR_SERVER_NOT_FOUND, show_alert=True)
        return

    await callback.answer(show_alert=False)
    await state.clear()

    extra = server.extra_data or {}
    relays = extra.get("relays", [])
    custom_names = extra.get("relay_names", {})

    relay = next((r for r in relays if (r.get("code") or r.get("name")) == relay_code), None)
    base_name = relay.get("name") if relay else relay_code
    current_name = custom_names.get(relay_code) or base_name

    await state.update_data(server_id=server_id, relay_code=relay_code)
    await state.set_state(AdminStates.editing_server_incy_relay_name)

    prompt_text = texts.ADMIN_SERVER_INCY_PROMPT_RELAY_NAME.format(
        relay_code=safe(relay_code),
        current=safe(current_name),
    )

    try:
        await callback.message.edit_text(
            prompt_text,
            reply_markup=get_back_button(f"admin_server_incy_relay_view:{server_id}:{relay_code}"),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"start_edit_relay_specific_name edit_text failed: {e}")


@router.message(AdminStates.editing_server_incy_relay_name)
async def process_server_incy_relay_name_input(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if not is_admin(message.from_user.id):
        await message.answer(texts.ERROR_ACCESS_DENIED)
        return

    data = await state.get_data()
    server_id = data.get("server_id")
    relay_code = data.get("relay_code")

    if not server_id or not relay_code:
        await state.clear()
        return

    server = await get_server_by_id(session, server_id)
    if not server:
        await message.answer(texts.ERROR_SERVER_NOT_FOUND)
        await state.clear()
        return

    raw_text = (message.text or "").strip()
    extra = dict(server.extra_data or {})
    relay_names = dict(extra.get("relay_names") or {})

    if raw_text != "/cancel":
        if raw_text in ("/clear", "-", "none"):
            relay_names.pop(relay_code, None)
        else:
            relay_names[relay_code] = raw_text[:30]

        extra["relay_names"] = relay_names
        await update_server(session, server, extra_data=extra)
        await session.refresh(server)

    await state.clear()

    relays = extra.get("relays", [])
    custom_names = extra.get("relay_names", {})
    custom_badges = extra.get("relay_badges", {})

    relay = next((r for r in relays if (r.get("code") or r.get("name")) == relay_code), None)
    base_name = relay.get("name") if relay else relay_code
    custom_name = custom_names.get(relay_code) or base_name
    custom_badge = (
        custom_badges.get(relay_code)
        or (relay.get("badge") if relay else None)
        or texts.ADMIN_SERVER_INCY_VALUE_NONE
    )

    card_text = f"{texts.ADMIN_SERVER_INCY_SAVED}\n\n" + texts.ADMIN_SERVER_INCY_RELAY_CARD.format(
        relay_name=safe(base_name),
        relay_code=safe(relay_code),
        custom_name=safe(custom_name),
        custom_badge=safe(custom_badge),
    )

    await message.answer(
        card_text,
        reply_markup=get_admin_server_incy_relay_actions_keyboard(server_id, relay_code),
        parse_mode="HTML",
    )


@router.callback_query(
    F.data.startswith("admin_server_incy_relay_edit_badge:")
    | F.data.startswith("admin_server_incy_relay_edit:")
)
async def start_edit_relay_specific_badge(
    callback: CallbackQuery,
    state: FSMContext,
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
    except ValueError:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    relay_code = parts[2]
    server = await get_server_by_id(session, server_id)
    if not server:
        await callback.answer(texts.ERROR_SERVER_NOT_FOUND, show_alert=True)
        return

    await callback.answer(show_alert=False)
    await state.clear()

    extra = server.extra_data or {}
    relays = extra.get("relays", [])
    custom_badges = extra.get("relay_badges", {})

    relay = next((r for r in relays if (r.get("code") or r.get("name")) == relay_code), None)
    relay_name = relay.get("name") if relay else relay_code
    current_badge = (
        custom_badges.get(relay_code)
        or (relay.get("badge") if relay else None)
        or texts.ADMIN_SERVER_INCY_VALUE_NONE
    )

    await state.update_data(server_id=server_id, relay_code=relay_code)
    await state.set_state(AdminStates.editing_server_incy_relay_badge)

    prompt_text = texts.ADMIN_SERVER_INCY_PROMPT_RELAY_SPECIFIC.format(
        relay_name=safe(relay_name),
        relay_code=safe(relay_code),
        current=safe(current_badge),
    )

    try:
        await callback.message.edit_text(
            prompt_text,
            reply_markup=get_back_button(f"admin_server_incy_relay_view:{server_id}:{relay_code}"),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"start_edit_relay_specific_badge edit_text failed: {e}")


@router.message(AdminStates.editing_server_incy_relay_badge)
async def process_server_incy_relay_badge_input(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if not is_admin(message.from_user.id):
        await message.answer(texts.ERROR_ACCESS_DENIED)
        return

    data = await state.get_data()
    server_id = data.get("server_id")
    relay_code = data.get("relay_code")

    if not server_id or not relay_code:
        await state.clear()
        return

    server = await get_server_by_id(session, server_id)
    if not server:
        await message.answer(texts.ERROR_SERVER_NOT_FOUND)
        await state.clear()
        return

    raw_text = (message.text or "").strip()
    extra = dict(server.extra_data or {})
    relay_badges = dict(extra.get("relay_badges") or {})

    if raw_text != "/cancel":
        if raw_text in ("/clear", "-", "none"):
            relay_badges.pop(relay_code, None)
        else:
            relay_badges[relay_code] = raw_text[:30]

        extra["relay_badges"] = relay_badges
        await update_server(session, server, extra_data=extra)
        await session.refresh(server)

    await state.clear()

    relays = extra.get("relays", [])
    custom_names = extra.get("relay_names", {})
    custom_badges = extra.get("relay_badges", {})

    relay = next((r for r in relays if (r.get("code") or r.get("name")) == relay_code), None)
    base_name = relay.get("name") if relay else relay_code
    custom_name = custom_names.get(relay_code) or base_name
    custom_badge = (
        custom_badges.get(relay_code)
        or (relay.get("badge") if relay else None)
        or texts.ADMIN_SERVER_INCY_VALUE_NONE
    )

    card_text = f"{texts.ADMIN_SERVER_INCY_SAVED}\n\n" + texts.ADMIN_SERVER_INCY_RELAY_CARD.format(
        relay_name=safe(base_name),
        relay_code=safe(relay_code),
        custom_name=safe(custom_name),
        custom_badge=safe(custom_badge),
    )

    await message.answer(
        card_text,
        reply_markup=get_admin_server_incy_relay_actions_keyboard(server_id, relay_code),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin_server_incy_reset:"))
async def reset_server_incy_to_defaults(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    server_id = parse_callback_id(callback.data, 1)
    if server_id is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    server = await get_server_by_id(session, server_id)
    if not server:
        await callback.answer(texts.ERROR_SERVER_NOT_FOUND, show_alert=True)
        return

    extra = dict(server.extra_data or {})
    for k in (
        "profile_title",
        "profile_description",
        "origin_tag",
        "origin_badge",
        "relay_badge",
        "relay_names",
        "relay_badges",
        "channel_url",
        "support_url",
    ):
        extra.pop(k, None)

    await update_server(session, server, extra_data=extra)
    await session.refresh(server)

    await callback.answer(texts.ADMIN_SERVER_INCY_RESET_SUCCESS, show_alert=True)

    details = _get_server_incy_details(server)
    flag = server.country_flag or texts.EMOJI_GLOBE
    card_text = texts.ADMIN_SERVER_INCY_CARD.format(
        flag=flag,
        name=safe(server.name),
        title=safe(details["title"]),
        description=safe(details["description"]),
        origin_name=safe(details["origin_name"]),
        origin_badge=safe(details["origin_badge"]),
        channel_url=safe(details["channel_url"]),
        support_url=safe(details["support_url"]),
    )

    try:
        await callback.message.edit_text(
            card_text,
            reply_markup=get_admin_server_incy_keyboard(server_id),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"reset_server_incy_to_defaults edit_text failed: {e}")
