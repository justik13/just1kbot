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
    get_admin_server_incy_reset_confirm_keyboard,
    get_admin_std_sub_incy_keyboard,
)
from bot.states import AdminStates
from config import constants
from config.constants import (
    WHITE_INTERNET_CHANNEL_URL,
    WHITE_INTERNET_PROFILE_TITLE,
    WHITE_INTERNET_SUPPORT_URL,
)
from database.repositories.servers_repo import (
    get_server_by_id,
    is_white_internet_origin_server,
    update_server,
)
from database.repositories.system_settings_repo import (
    get_system_setting,
    set_system_setting,
)
from utils.admin import is_admin
from utils.callbacks import parse_callback_id
from utils.telegram import render_hub, safe

router = Router()
logger = logging.getLogger(__name__)


def _get_server_incy_details(server: Any) -> dict[str, Any]:
    """Extract and format current INCY subscription settings for a server."""
    extra = server.extra_data if isinstance(getattr(server, "extra_data", None), dict) else {}
    bot_user = (os.getenv("BOT_USERNAME") or constants.DEFAULT_FALLBACK_BOT_USERNAME).lstrip("@")
    default_bot_url = f"https://t.me/{bot_user}"

    is_origin = is_white_internet_origin_server(server)
    default_title = (
        WHITE_INTERNET_PROFILE_TITLE or texts.WL_PROFILE_NAME
        if is_origin
        else os.getenv("VLESS_PROFILE_TITLE", constants.DEFAULT_VLESS_PROFILE_TITLE)
    )
    title = extra.get("profile_title") or default_title
    announce = extra.get("announce")
    announce_url = extra.get("announce_url")

    default_origin_name = texts.WL_ORIGIN_VLESS_TAG if is_origin else (getattr(server, "name", None) or "Server")
    origin_name = extra.get("origin_tag") or extra.get("vless_name") or default_origin_name
    origin_badge_raw = extra.get("origin_badge") if "origin_badge" in extra else extra.get("server_description")
    if origin_badge_raw and origin_badge_raw.strip().lower() == "none":
        origin_badge = texts.ADMIN_SERVER_INCY_VALUE_DISABLED
    elif origin_badge_raw and origin_badge_raw.strip():
        origin_badge = origin_badge_raw.strip()
    else:
        origin_badge = texts.ADMIN_SERVER_INCY_VALUE_NONE
    origin_hidden = bool(extra.get("origin_hidden", False))

    channel_url = extra.get("channel_url") or (WHITE_INTERNET_CHANNEL_URL if is_origin else None) or default_bot_url
    support_url = extra.get("support_url") or (WHITE_INTERNET_SUPPORT_URL if is_origin else None) or default_bot_url

    origin_status = (
        texts.ADMIN_SERVER_INCY_STATUS_HIDDEN
        if origin_hidden
        else texts.ADMIN_SERVER_INCY_STATUS_ACTIVE
    )

    return {
        "title": title or texts.WL_PROFILE_NAME,
        "announce": announce or texts.ADMIN_SERVER_INCY_VALUE_NONE,
        "announce_url": announce_url or texts.ADMIN_SERVER_INCY_VALUE_NONE,
        "origin_name": origin_name,
        "origin_badge": origin_badge,
        "origin_status": origin_status,
        "origin_hidden": origin_hidden,
        "channel_url": channel_url,
        "support_url": support_url,
    }


async def _build_std_sub_incy_card(session: AsyncSession) -> tuple[str, Any]:
    title = await get_system_setting(session, "vless_profile_title", os.getenv("VLESS_PROFILE_TITLE", constants.DEFAULT_VLESS_PROFILE_TITLE))
    announce = await get_system_setting(session, "vless_announce", None)
    announce_url = await get_system_setting(session, "vless_announce_url", None)

    show_devs_raw = await get_system_setting(session, "vless_profile_show_devices", False)
    if isinstance(show_devs_raw, str):
        show_devices = show_devs_raw.lower() in ("1", "true", "yes", "on")
    else:
        show_devices = bool(show_devs_raw)

    show_devs_status = texts.ADMIN_STD_SUB_INCY_STATUS_ON if show_devices else texts.ADMIN_STD_SUB_INCY_STATUS_OFF
    card_text = texts.ADMIN_STD_SUB_INCY_CONFIG_CARD.format(
        title=safe(title or constants.DEFAULT_VLESS_PROFILE_TITLE),
        show_devices_status=show_devs_status,
        announce=safe(announce or texts.ADMIN_SERVER_INCY_VALUE_NONE),
        announce_url=safe(announce_url or texts.ADMIN_SERVER_INCY_VALUE_NONE),
    )
    keyboard = get_admin_std_sub_incy_keyboard(show_devices=show_devices)
    return card_text, keyboard


def _build_server_incy_card(server: Any, details: dict[str, Any]) -> tuple[str, Any]:
    """Build formatted text and keyboard for server INCY configuration card."""
    flag = server.country_flag or texts.EMOJI_GLOBE
    is_origin = is_white_internet_origin_server(server)
    if is_origin:
        card_text = texts.ADMIN_SERVER_INCY_CARD.format(
            flag=flag,
            name=safe(server.name),
            title=safe(details["title"]),
            announce=safe(details["announce"]),
            announce_url=safe(details["announce_url"]),
            origin_name=safe(details["origin_name"]),
            origin_badge=safe(details["origin_badge"]),
            origin_status=safe(details["origin_status"]),
        )
    else:
        card_text = texts.ADMIN_SERVER_INCY_STANDARD_CARD.format(
            flag=flag,
            name=safe(server.name),
            origin_name=safe(details["origin_name"]),
            origin_badge=safe(details["origin_badge"]),
            origin_status=safe(details["origin_status"]),
        )
    keyboard = get_admin_server_incy_keyboard(
        server.id,
        origin_hidden=details["origin_hidden"],
        is_origin=is_origin,
    )
    return card_text, keyboard


@router.callback_query(F.data == "admin_std_incy")
async def show_std_sub_incy_card(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    await callback.answer(show_alert=False)
    await state.clear()

    card_text, reply_markup = await _build_std_sub_incy_card(session)
    try:
        await callback.message.edit_text(
            card_text,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"show_std_sub_incy_card edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_std_incy_edit:"))
async def start_edit_std_incy_param(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    field = callback.data.split(":")[1]
    title = await get_system_setting(session, "vless_profile_title", os.getenv("VLESS_PROFILE_TITLE", constants.DEFAULT_VLESS_PROFILE_TITLE))
    announce = await get_system_setting(session, "vless_announce", None)
    announce_url = await get_system_setting(session, "vless_announce_url", None)

    prompt_map = {
        "title": (texts.ADMIN_SERVER_INCY_PROMPT_TITLE, title or constants.DEFAULT_VLESS_PROFILE_TITLE),
        "announce": (texts.ADMIN_SERVER_INCY_PROMPT_ANNOUNCE, announce or texts.ADMIN_SERVER_INCY_VALUE_NONE),
        "announce_url": (texts.ADMIN_SERVER_INCY_PROMPT_ANNOUNCE_URL, announce_url or texts.ADMIN_SERVER_INCY_VALUE_NONE),
    }

    if field not in prompt_map:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    prompt_tpl, cur_val = prompt_map[field]
    prompt_text = prompt_tpl.format(current=safe(cur_val))

    await state.update_data(field=field)
    await state.set_state(AdminStates.editing_std_incy_param)

    try:
        await callback.message.edit_text(
            prompt_text,
            reply_markup=get_back_button("admin_std_incy"),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"start_edit_std_incy_param edit_text failed: {e}")


@router.message(AdminStates.editing_std_incy_param)
async def process_std_incy_param_input(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    try:
        await message.delete()
    except Exception:
        pass

    if not is_admin(message.from_user.id):
        await state.clear()
        return

    data = await state.get_data()
    field = data.get("field")
    if not field:
        await state.clear()
        return

    if not message.text:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_TEXT_REQUIRED,
            get_back_button("admin_std_incy"),
            trigger_message_id=getattr(message, "message_id", None),
        )
        return

    text = message.text.strip()
    if text.startswith("/"):
        await state.clear()
        card_text, reply_markup = await _build_std_sub_incy_card(session)
        await render_hub(
            message.bot,
            message.chat.id,
            card_text,
            reply_markup,
            parse_mode="HTML",
            trigger_message_id=getattr(message, "message_id", None),
        )
        return

    is_clear = text in ("-", "—", "–")
    is_none = text.lower() == "none"

    if field == "title":
        if is_clear or is_none or not text:
            new_val = os.getenv("VLESS_PROFILE_TITLE", constants.DEFAULT_VLESS_PROFILE_TITLE) or constants.DEFAULT_VLESS_PROFILE_TITLE
        else:
            new_val = text[:25]
        await set_system_setting(session, "vless_profile_title", new_val, updated_by=message.from_user.id)
    elif field == "announce":
        new_val = None if (is_clear or is_none or not text) else text[:200]
        await set_system_setting(session, "vless_announce", new_val, updated_by=message.from_user.id)
    elif field == "announce_url":
        if is_clear or is_none or not text:
            new_val = None
        else:
            if any(c in text for c in (" ", "\n", "\r", "\t")) or not (
                text.startswith("https://") or text.startswith("http://") or text.startswith("tg://")
            ):
                await render_hub(
                    message.bot,
                    message.chat.id,
                    texts.ADMIN_SERVER_INCY_ERR_INVALID_URL,
                    get_back_button("admin_std_incy"),
                    trigger_message_id=getattr(message, "message_id", None),
                )
                return
            new_val = text
        await set_system_setting(session, "vless_announce_url", new_val, updated_by=message.from_user.id)

    await session.commit()
    await state.clear()

    card_text, reply_markup = await _build_std_sub_incy_card(session)
    await render_hub(
        message.bot,
        message.chat.id,
        card_text,
        reply_markup,
        parse_mode="HTML",
        trigger_message_id=getattr(message, "message_id", None),
    )


@router.callback_query(F.data == "admin_std_incy_toggle_devs")
async def toggle_std_sub_incy_devices(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    await state.clear()
    current_raw = await get_system_setting(session, "vless_profile_show_devices", False)
    if isinstance(current_raw, str):
        current_bool = current_raw.lower() in ("1", "true", "yes", "on")
    else:
        current_bool = bool(current_raw)
    new_val = not current_bool

    await set_system_setting(
        session, "vless_profile_show_devices", str(new_val).lower(), updated_by=callback.from_user.id
    )
    await session.commit()

    card_text, reply_markup = await _build_std_sub_incy_card(session)
    try:
        await callback.message.edit_text(
            card_text,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"toggle_std_sub_incy_devices edit_text failed: {e}")
    await callback.answer()


@router.callback_query(F.data == "admin_std_incy_reset")
async def reset_std_sub_incy_to_defaults(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    await state.clear()
    default_title = (
        os.getenv("VLESS_PROFILE_TITLE", constants.DEFAULT_VLESS_PROFILE_TITLE)
        or constants.DEFAULT_VLESS_PROFILE_TITLE
    )
    await set_system_setting(session, "vless_profile_title", default_title, updated_by=callback.from_user.id)
    await set_system_setting(session, "vless_profile_description", None, updated_by=callback.from_user.id)
    await set_system_setting(session, "vless_profile_desc", None, updated_by=callback.from_user.id)
    await set_system_setting(session, "vless_profile_show_devices", "false", updated_by=callback.from_user.id)
    await set_system_setting(session, "vless_announce", None, updated_by=callback.from_user.id)
    await set_system_setting(session, "vless_announce_url", None, updated_by=callback.from_user.id)
    await session.commit()

    await callback.answer(texts.ADMIN_SERVER_INCY_RESET_SUCCESS, show_alert=False)
    card_text, reply_markup = await _build_std_sub_incy_card(session)
    try:
        await callback.message.edit_text(
            card_text,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"reset_std_sub_incy_to_defaults edit_text failed: {e}")


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
    card_text, reply_markup = _build_server_incy_card(server, details)

    try:
        await callback.message.edit_text(
            card_text,
            reply_markup=reply_markup,
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
    is_origin = is_white_internet_origin_server(server)
    name_prompt = (
        texts.ADMIN_SERVER_INCY_PROMPT_ORIGIN_NAME
        if is_origin
        else texts.ADMIN_SERVER_INCY_PROMPT_STANDARD_NAME
    )
    badge_prompt = (
        texts.ADMIN_SERVER_INCY_PROMPT_ORIGIN_BADGE
        if is_origin
        else texts.ADMIN_SERVER_INCY_PROMPT_STANDARD_BADGE
    )
    prompt_map = {
        "title": (texts.ADMIN_SERVER_INCY_PROMPT_TITLE, details["title"]),
        "announce": (texts.ADMIN_SERVER_INCY_PROMPT_ANNOUNCE, details["announce"]),
        "announce_url": (texts.ADMIN_SERVER_INCY_PROMPT_ANNOUNCE_URL, details["announce_url"]),
        "origin_name": (name_prompt, details["origin_name"]),
        "origin_badge": (badge_prompt, details["origin_badge"]),
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
    try:
        await message.delete()
    except Exception:
        pass

    if not is_admin(message.from_user.id):
        await state.clear()
        return

    data = await state.get_data()
    server_id = data.get("server_id")
    param = data.get("incy_param")

    if not server_id or not param:
        await state.clear()
        return

    server = await get_server_by_id(session, server_id, for_update=True)
    if not server:
        await state.clear()
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_SERVER_NOT_FOUND,
            get_back_button("admin_servers"),
            trigger_message_id=getattr(message, "message_id", None),
        )
        return

    raw_text = (message.text or "").strip()
    if raw_text.startswith("/"):
        await state.clear()
        return

    is_clear = raw_text in ("-", "—", "–")
    is_none = raw_text.lower() == "none"

    if param == "announce_url" and not (is_clear or is_none):
        v = raw_text.strip()
        if any(c in v for c in (" ", "\n", "\r", "\t")) or not (
            v.startswith("https://") or v.startswith("http://") or v.startswith("tg://")
        ):
            details = _get_server_incy_details(server)
            prompt_text = texts.ADMIN_SERVER_INCY_PROMPT_ANNOUNCE_URL.format(
                current=safe(details["announce_url"])
            )
            await render_hub(
                message.bot,
                message.chat.id,
                f"{texts.ADMIN_SERVER_INCY_ERR_INVALID_URL}\n\n{prompt_text}",
                get_back_button(f"admin_server_incy:{server_id}"),
                parse_mode="HTML",
                trigger_message_id=getattr(message, "message_id", None),
            )
            return

    extra = dict(server.extra_data or {})
    param_key_map = {
        "title": "profile_title",
        "announce": "announce",
        "announce_url": "announce_url",
        "origin_name": "origin_tag",
        "origin_badge": "origin_badge",
    }

    key = param_key_map.get(param)
    if key:
        if is_clear:
            extra[key] = ""
        elif param == "origin_badge" and is_none:
            extra[key] = "none"
        else:
            val = "" if is_none else raw_text
            if param == "title":
                val = val[:25]
            elif param in ("origin_name", "origin_badge"):
                val = val[:30]
            elif param == "announce":
                val = val[:200]
            elif param == "announce_url":
                val = val.strip()
            extra[key] = val

        await update_server(session, server, extra_data=extra)

    await state.clear()
    await session.refresh(server)

    details = _get_server_incy_details(server)
    card_text, reply_markup = _build_server_incy_card(server, details)
    full_text = f"{texts.ADMIN_SERVER_INCY_SAVED}\n\n{card_text}"

    await render_hub(
        message.bot,
        message.chat.id,
        full_text,
        reply_markup=reply_markup,
        parse_mode="HTML",
        trigger_message_id=getattr(message, "message_id", None),
    )


@router.callback_query(F.data.startswith("admin_server_incy_toggle_origin:"))
async def toggle_server_incy_origin_visibility(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    server_id = parse_callback_id(callback.data, 1)
    if server_id is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    server = await get_server_by_id(session, server_id, for_update=True)
    if not server:
        await callback.answer(texts.ERROR_SERVER_NOT_FOUND, show_alert=True)
        return

    extra = dict(server.extra_data or {})
    extra["origin_hidden"] = not bool(extra.get("origin_hidden", False))
    await update_server(session, server, extra_data=extra)
    await session.refresh(server)

    await callback.answer(texts.ADMIN_SERVER_INCY_SAVED, show_alert=False)

    details = _get_server_incy_details(server)
    card_text, reply_markup = _build_server_incy_card(server, details)

    try:
        await callback.message.edit_text(
            card_text,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"toggle_server_incy_origin_visibility edit_text failed: {e}")


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
    hidden_relays = extra.get("relay_hidden", [])

    if not relays:
        await callback.answer(texts.ADMIN_SERVER_INCY_NO_RELAYS, show_alert=True)
        return

    try:
        await callback.message.edit_text(
            texts.ADMIN_SERVER_INCY_RELAYS_TITLE,
            reply_markup=get_admin_server_incy_relays_keyboard(
                server_id, relays, custom_names, custom_badges, hidden_relays=hidden_relays
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
    hidden_relays = set(extra.get("relay_hidden", []))

    relay = next((r for r in relays if (r.get("code") or r.get("name")) == relay_code), None)
    base_name = relay.get("name") if relay else relay_code
    custom_name = custom_names.get(relay_code) or base_name
    custom_badge_raw = custom_badges.get(relay_code)
    if custom_badge_raw and custom_badge_raw.strip().lower() == "none":
        custom_badge = texts.ADMIN_SERVER_INCY_VALUE_DISABLED
    elif custom_badge_raw and custom_badge_raw.strip():
        custom_badge = custom_badge_raw.strip()
    elif relay and relay.get("badge"):
        custom_badge = relay.get("badge")
    else:
        custom_badge = texts.ADMIN_SERVER_INCY_VALUE_NONE
    is_hidden = relay_code in hidden_relays
    status = (
        texts.ADMIN_SERVER_INCY_STATUS_HIDDEN
        if is_hidden
        else texts.ADMIN_SERVER_INCY_STATUS_ACTIVE
    )

    card_text = texts.ADMIN_SERVER_INCY_RELAY_CARD.format(
        relay_name=safe(base_name),
        relay_code=safe(relay_code),
        custom_name=safe(custom_name),
        custom_badge=safe(custom_badge),
        status=safe(status),
    )

    try:
        await callback.message.edit_text(
            card_text,
            reply_markup=get_admin_server_incy_relay_actions_keyboard(
                server_id, relay_code, is_hidden=is_hidden
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"show_server_incy_relay_card edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_server_incy_relay_toggle:"))
async def toggle_server_incy_relay_visibility(
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
    except ValueError:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    relay_code = parts[2]
    server = await get_server_by_id(session, server_id, for_update=True)
    if not server:
        await callback.answer(texts.ERROR_SERVER_NOT_FOUND, show_alert=True)
        return

    extra = dict(server.extra_data or {})
    hidden_relays = set(extra.get("relay_hidden", []))
    if relay_code in hidden_relays:
        hidden_relays.remove(relay_code)
    else:
        hidden_relays.add(relay_code)
    extra["relay_hidden"] = list(hidden_relays)

    await update_server(session, server, extra_data=extra)
    await session.refresh(server)

    await callback.answer(texts.ADMIN_SERVER_INCY_SAVED, show_alert=False)

    relays = extra.get("relays", [])
    custom_names = extra.get("relay_names", {})
    custom_badges = extra.get("relay_badges", {})
    relay = next((r for r in relays if (r.get("code") or r.get("name")) == relay_code), None)
    base_name = relay.get("name") if relay else relay_code
    custom_name = custom_names.get(relay_code) or base_name
    custom_badge_raw = custom_badges.get(relay_code)
    if custom_badge_raw and custom_badge_raw.strip().lower() == "none":
        custom_badge = texts.ADMIN_SERVER_INCY_VALUE_DISABLED
    elif custom_badge_raw and custom_badge_raw.strip():
        custom_badge = custom_badge_raw.strip()
    elif relay and relay.get("badge"):
        custom_badge = relay.get("badge")
    else:
        custom_badge = texts.ADMIN_SERVER_INCY_VALUE_NONE
    is_hidden = relay_code in hidden_relays
    status = (
        texts.ADMIN_SERVER_INCY_STATUS_HIDDEN
        if is_hidden
        else texts.ADMIN_SERVER_INCY_STATUS_ACTIVE
    )

    card_text = texts.ADMIN_SERVER_INCY_RELAY_CARD.format(
        relay_name=safe(base_name),
        relay_code=safe(relay_code),
        custom_name=safe(custom_name),
        custom_badge=safe(custom_badge),
        status=safe(status),
    )

    try:
        await callback.message.edit_text(
            card_text,
            reply_markup=get_admin_server_incy_relay_actions_keyboard(
                server_id, relay_code, is_hidden=is_hidden
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"toggle_server_incy_relay_visibility edit_text failed: {e}")


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
    try:
        await message.delete()
    except Exception:
        pass

    if not is_admin(message.from_user.id):
        await state.clear()
        return

    data = await state.get_data()
    server_id = data.get("server_id")
    relay_code = data.get("relay_code")

    if not server_id or not relay_code:
        await state.clear()
        return

    server = await get_server_by_id(session, server_id, for_update=True)
    if not server:
        await state.clear()
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_SERVER_NOT_FOUND,
            get_back_button("admin_servers"),
            trigger_message_id=getattr(message, "message_id", None),
        )
        return

    raw_text = (message.text or "").strip()
    if raw_text.startswith("/"):
        await state.clear()
        return

    extra = dict(server.extra_data or {})
    relay_names = dict(extra.get("relay_names") or {})

    if raw_text in ("-", "—", "–"):
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
    hidden_relays = set(extra.get("relay_hidden", []))

    relay = next((r for r in relays if (r.get("code") or r.get("name")) == relay_code), None)
    base_name = relay.get("name") if relay else relay_code
    custom_name = custom_names.get(relay_code) or base_name
    custom_badge_raw = custom_badges.get(relay_code)
    if custom_badge_raw and custom_badge_raw.strip().lower() == "none":
        custom_badge = texts.ADMIN_SERVER_INCY_VALUE_DISABLED
    elif custom_badge_raw and custom_badge_raw.strip():
        custom_badge = custom_badge_raw.strip()
    elif relay and relay.get("badge"):
        custom_badge = relay.get("badge")
    else:
        custom_badge = texts.ADMIN_SERVER_INCY_VALUE_NONE
    is_hidden = relay_code in hidden_relays
    status = (
        texts.ADMIN_SERVER_INCY_STATUS_HIDDEN
        if is_hidden
        else texts.ADMIN_SERVER_INCY_STATUS_ACTIVE
    )

    card_text = f"{texts.ADMIN_SERVER_INCY_SAVED}\n\n" + texts.ADMIN_SERVER_INCY_RELAY_CARD.format(
        relay_name=safe(base_name),
        relay_code=safe(relay_code),
        custom_name=safe(custom_name),
        custom_badge=safe(custom_badge),
        status=safe(status),
    )

    await render_hub(
        message.bot,
        message.chat.id,
        card_text,
        reply_markup=get_admin_server_incy_relay_actions_keyboard(
            server_id, relay_code, is_hidden=is_hidden
        ),
        parse_mode="HTML",
        trigger_message_id=getattr(message, "message_id", None),
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
    custom_badge_raw = custom_badges.get(relay_code)
    if custom_badge_raw and custom_badge_raw.strip().lower() == "none":
        current_badge = texts.ADMIN_SERVER_INCY_VALUE_DISABLED
    elif custom_badge_raw and custom_badge_raw.strip():
        current_badge = custom_badge_raw.strip()
    elif relay and relay.get("badge"):
        current_badge = relay.get("badge")
    else:
        current_badge = texts.ADMIN_SERVER_INCY_VALUE_NONE

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
    try:
        await message.delete()
    except Exception:
        pass

    if not is_admin(message.from_user.id):
        await state.clear()
        return

    data = await state.get_data()
    server_id = data.get("server_id")
    relay_code = data.get("relay_code")

    if not server_id or not relay_code:
        await state.clear()
        return

    server = await get_server_by_id(session, server_id, for_update=True)
    if not server:
        await state.clear()
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_SERVER_NOT_FOUND,
            get_back_button("admin_servers"),
            trigger_message_id=getattr(message, "message_id", None),
        )
        return

    raw_text = (message.text or "").strip()
    if raw_text.startswith("/"):
        await state.clear()
        return

    extra = dict(server.extra_data or {})
    relay_badges = dict(extra.get("relay_badges") or {})

    if raw_text in ("-", "—", "–"):
        relay_badges.pop(relay_code, None)
    elif raw_text.lower() == "none":
        relay_badges[relay_code] = "none"
    else:
        relay_badges[relay_code] = raw_text[:30]

    extra["relay_badges"] = relay_badges
    await update_server(session, server, extra_data=extra)
    await session.refresh(server)
    await state.clear()

    relays = extra.get("relays", [])
    custom_names = extra.get("relay_names", {})
    custom_badges = extra.get("relay_badges", {})
    hidden_relays = set(extra.get("relay_hidden", []))

    relay = next((r for r in relays if (r.get("code") or r.get("name")) == relay_code), None)
    base_name = relay.get("name") if relay else relay_code
    custom_name = custom_names.get(relay_code) or base_name
    custom_badge_raw = custom_badges.get(relay_code)
    if custom_badge_raw and custom_badge_raw.strip().lower() == "none":
        custom_badge = texts.ADMIN_SERVER_INCY_VALUE_DISABLED
    elif custom_badge_raw and custom_badge_raw.strip():
        custom_badge = custom_badge_raw.strip()
    elif relay and relay.get("badge"):
        custom_badge = relay.get("badge")
    else:
        custom_badge = texts.ADMIN_SERVER_INCY_VALUE_NONE
    is_hidden = relay_code in hidden_relays
    status = (
        texts.ADMIN_SERVER_INCY_STATUS_HIDDEN
        if is_hidden
        else texts.ADMIN_SERVER_INCY_STATUS_ACTIVE
    )

    card_text = f"{texts.ADMIN_SERVER_INCY_SAVED}\n\n" + texts.ADMIN_SERVER_INCY_RELAY_CARD.format(
        relay_name=safe(base_name),
        relay_code=safe(relay_code),
        custom_name=safe(custom_name),
        custom_badge=safe(custom_badge),
        status=safe(status),
    )

    await render_hub(
        message.bot,
        message.chat.id,
        card_text,
        reply_markup=get_admin_server_incy_relay_actions_keyboard(
            server_id, relay_code, is_hidden=is_hidden
        ),
        parse_mode="HTML",
        trigger_message_id=getattr(message, "message_id", None),
    )


@router.callback_query(F.data.startswith("admin_server_incy_reset:"))
async def confirm_server_incy_reset(
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

    await callback.answer(show_alert=False)

    confirm_text = texts.ADMIN_SERVER_INCY_RESET_CONFIRM_TITLE.format(
        name=safe(server.name)
    )

    try:
        await callback.message.edit_text(
            confirm_text,
            reply_markup=get_admin_server_incy_reset_confirm_keyboard(server_id),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"confirm_server_incy_reset edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_server_incy_reset_apply:"))
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

    server = await get_server_by_id(session, server_id, for_update=True)
    if not server:
        await callback.answer(texts.ERROR_SERVER_NOT_FOUND, show_alert=True)
        return

    extra = dict(server.extra_data or {})
    for k in (
        "profile_title",
        "profile_description",
        "announce",
        "announce_url",
        "origin_tag",
        "origin_badge",
        "origin_hidden",
        "relay_badge",
        "relay_names",
        "relay_badges",
        "relay_hidden",
        "channel_url",
        "support_url",
    ):
        extra.pop(k, None)

    await update_server(session, server, extra_data=extra)
    await session.refresh(server)

    await callback.answer(texts.ADMIN_SERVER_INCY_RESET_SUCCESS, show_alert=True)

    details = _get_server_incy_details(server)
    card_text, reply_markup = _build_server_incy_card(server, details)

    try:
        await callback.message.edit_text(
            card_text,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"reset_server_incy_to_defaults edit_text failed: {e}")

