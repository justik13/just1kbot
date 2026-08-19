import logging
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.constants import AMNEZIA_PROTOCOL, TELEGRAM_MESSAGE_LIMIT
from bot.keyboards import get_back_button, get_device_keyboard
from config.settings import get_settings
from database.models import User
from database.repositories.profiles_repo import get_profile_by_id
from database.repositories.servers_repo import get_server_by_id
from services.amnezia_bridge_token_service import AmneziaBridgeTokenService
from services.subscription import SubscriptionService
from utils.callbacks import parse_callback_id
from utils.formatters import format_datetime, format_traffic
from utils.telegram import (
    append_hub_document,
    append_hub_message,
    delete_hub_ids,
    get_hub_ids,
    render_hub,
    safe,
    send_hub_document,
)
from utils.vpn_parser import (
    build_conf_file_from_dict,
    build_vpn_file_from_dict,
    customize_vpn_config_dict,
    customize_vpn_uri,
    decode_vpn_uri_to_json,
)

from .common import _format_protocol, _render_connections

# Kept in sync with bot/handlers/support.py::AMNEZIA_DOCS
_AMNEZIA_DOCS = "https://storage.googleapis.com/amnezia/docs?m-path=/"

router = Router()
logger = logging.getLogger(__name__)


async def _get_safe_device_name(session: AsyncSession, profile) -> str:
    server = await get_server_by_id(session, profile.server_id)
    server_name = server.name if server else "server"
    m = re.search(r'#(\d+)$', profile.device_name)
    slot_suffix = f"_{m.group(1)}" if m else ""
    return "".join(
        c for c in f"{server_name}{slot_suffix}" if c.isalnum() or c in (" ", "_", "-")
    ).strip().replace(" ", "_") or "client"


def has_usable_vpn_uri(profile) -> bool:
    """Check if profile has a syntactically valid vpn:// URI."""
    if not profile or getattr(profile, "is_active", False) is not True:
        return False
    raw = getattr(profile, "raw_config", None)
    if not raw or not getattr(profile, "peer_id", None):
        return False
    from utils.vpn_parser import is_valid_vpn_uri
    return is_valid_vpn_uri(raw)


def is_profile_ready_for_user(profile) -> bool:
    """Check if profile is fully provisioned with active status and valid vpn:// URI."""
    if not profile or getattr(profile, "provisioning_status", "") != "active":
        return False
    return has_usable_vpn_uri(profile)


def can_show_config_actions(profile) -> bool:
    """Check if Show Key and Download Conf buttons should be displayed."""
    if not has_usable_vpn_uri(profile):
        return False
    return getattr(profile, "provisioning_status", "") in ("active", "pending_update", "update_failed")


def can_show_amnezia_bridge(profile, server) -> bool:
    """Check if 1-click 'Open in Amnezia' button should be displayed."""
    if not can_show_config_actions(profile) or not server:
        return False
    return (
        getattr(server, "is_active", False) is True
        and getattr(server, "protocol", "") == AMNEZIA_PROTOCOL
        and AmneziaBridgeTokenService.is_enabled()
    )


ALLOWED_DELETE_STATES = frozenset({
    "active",
    "pending_update",
    "update_failed",
    "create_failed",
    "delete_failed",
})


def can_show_delete_action(profile) -> bool:
    """Check if device deletion is permissible in the current state (Fail-Closed)."""
    if not profile:
        return False
    return getattr(profile, "provisioning_status", "") in ALLOWED_DELETE_STATES


async def render_device_screen(
    bot,
    chat_id: int,
    profile,
    user: User,
    session: AsyncSession,
):
    server = await get_server_by_id(session, profile.server_id)
    flag = server.country_flag if server else texts.RUNTIME_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L60_1
    server_name = server.name if server else texts.RUNTIME_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L61_1
    protocol = _format_protocol(server.protocol if server else None)

    rendered = texts.DEVICE_MANAGE_HEADER.format(
        device_name=safe(profile.device_name),
        flag=flag,
        country_display=server_name,
        server_name=flag,
        protocol=protocol,
        traffic_total=format_traffic((getattr(profile, "traffic_down", 0) or 0) + (getattr(profile, "traffic_up", 0) or 0)),
        last_connected=(
            format_datetime(profile.last_connected)
            if getattr(profile, "last_connected", None)
            else texts.RUNTIME_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L73_1
        ),
    )

    status = getattr(profile, "provisioning_status", "")
    if status == "pending_create":
        rendered += "\n\n⏳ <b>Устройство создаётся на сервере...</b>"
    elif status == "pending_update":
        rendered += "\n\n🔄 <b>Конфигурация устройства обновляется...</b>"
    elif status == "update_failed":
        rendered += "\n\n⚠️ <b>Не удалось обновить конфигурацию на сервере (действует текущая версия).</b>"
    elif status == "create_failed":
        rendered += "\n\n❌ <b>Не удалось создать устройство на сервере.</b>"
    elif status == "create_cleanup_pending":
        rendered += "\n\n⚠️ <b>Идёт автоматическое восстановление после сбоя...</b>"
    elif status == "deleting":
        rendered += "\n\n🗑 <b>Устройство удаляется с сервера...</b>"
    elif status == "delete_failed":
        rendered += "\n\n⚠️ <b>Не удалось удалить устройство на сервере. Попробуйте повторить.</b>"

    has_access = await SubscriptionService.check_access(session, user.telegram_id)
    show_delete = can_show_delete_action(profile)

    if has_access:
        config_ready = can_show_config_actions(profile)
        amnezia_bridge_url = None
        if can_show_amnezia_bridge(profile, server):
            settings = get_settings()
            amnezia_bridge_url = AmneziaBridgeTokenService.build_bridge_url(
                domain=settings.DOMAIN,
                profile_id=profile.id,
                user_id=user.id,
            )

        keyboard = get_device_keyboard(
            profile.id,
            config_ready=config_ready,
            show_delete=show_delete,
            amnezia_bridge_url=amnezia_bridge_url,
        )
    else:
        rendered += texts.RUNTIME_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L87_1

        builder = InlineKeyboardBuilder()
        if show_delete:
            builder.button(text=texts.UI_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L93_1, callback_data=f"request_delete_device:{profile.id}")
        builder.button(text=texts.UI_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L94_1, callback_data="back_to_connections")
        builder.button(text=texts.UI_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L95_1, callback_data="back_to_main_menu")
        builder.adjust(1)
        keyboard = builder.as_markup()

    await render_hub(
        bot,
        chat_id,
        rendered,
        keyboard,
    )


@router.callback_query(F.data.startswith("manage_device:"))
async def manage_device(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer(show_alert=False)
    await state.clear()

    profile_id = parse_callback_id(callback.data, 1)
    if profile_id is None:
        await callback.answer(texts.UI_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L51_1, show_alert=True)
        return

    profile = await get_profile_by_id(session, profile_id)
    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer("Устройство не найдено или было удалено", show_alert=True)
        if db_user:
            await _render_connections(callback.message, db_user, session)
        return

    await render_device_screen(
        callback.bot,
        callback.message.chat.id,
        profile,
        db_user,
        session,
    )


def _get_device_config_keyboard(profile_id: int):
    # NOTE: this keyboard is shown after "Скачать файлом" and "Показать ключ".
    # - "Инструкция и помощь" uses device_help:{profile_id} (NOT support_help/menu_support)
    #   so the user stays in the device flow with a contextual back button to manage_device.
    # - Back button returns directly to the device card.
    #   DO NOT use texts.UI_BOT_KEYBOARDS_COMMON_L7_1 — that key does not exist in ui_texts.py.
    builder = InlineKeyboardBuilder()
    builder.button(text="📖 Инструкция и помощь", callback_data=f"device_help:{profile_id}")
    builder.button(text="← К устройству", callback_data=f"manage_device:{profile_id}")
    builder.adjust(1)
    return builder.as_markup()


@router.callback_query(F.data.startswith("device_help:"))
async def device_help(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    """Contextual help screen shown after 'Скачать файлом' / 'Показать ключ'.

    Intentionally uses manage_device:{profile_id} as the back button so the
    user returns to their device card, not to the generic support menu.
    DO NOT replace the back button with menu_support — that breaks the device flow.
    """
    await callback.answer(show_alert=False)

    profile_id = parse_callback_id(callback.data, 1)
    if profile_id is None:
        await callback.answer("Некорректный запрос", show_alert=True)
        return

    text = (
        "📖 <b>Инструкции и справка AmneziaVPN</b>\n\n"
        "В этом разделе вы найдёте руководства по подключению, "
        "скачиванию клиента и настройке сервиса.\n\n"
        "⚠️ <b>Правила и особенности работы:</b>\n"
        "• <b>Не рекомендуется использовать торренты/P2P.</b>\n"
        "• <b>Часть сайтов/сервисов может быть недоступна по решению провайдеров.</b>\n\n"
        "Выберите нужную тему ниже:"
    )

    builder = InlineKeyboardBuilder()
    suffix = f":device_{profile_id}"
    builder.button(text="📥 Скачать клиент Amnezia", callback_data=f"help_download{suffix}")
    builder.button(text="🍏 Инструкция iOS (для РФ)", callback_data=f"help_ios{suffix}")
    builder.button(text="💻 Инструкции Windows", callback_data=f"help_windows{suffix}")
    builder.button(text="🔀 Раздельное Туннелирование", callback_data=f"help_split{suffix}")
    builder.button(text="📚 Документация Amnezia", url=_AMNEZIA_DOCS)
    builder.button(text="← К устройству", callback_data=f"manage_device:{profile_id}")
    builder.adjust(1)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        builder.as_markup(),
        trigger_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith("show_config:"))
async def show_config(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer(show_alert=False)
    await state.clear()

    profile_id = parse_callback_id(callback.data, 1)
    if profile_id is None:
        await callback.answer(texts.UI_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L114_1, show_alert=True)
        return

    profile = await get_profile_by_id(session, profile_id)
    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    has_access = await SubscriptionService.check_access(session, db_user.telegram_id)
    if not has_access:
        await callback.answer(texts.DEVICE_ACCESS_INACTIVE, show_alert=True)
        return

    raw_config = profile.raw_config or ""
    if not can_show_config_actions(profile) or not raw_config:
        await callback.answer(texts.DEVICE_CONFIG_UNAVAILABLE, show_alert=True)
        return

    server = await get_server_by_id(session, profile.server_id)
    server_name = server.name if server else "server"
    m = re.search(r'#(\d+)$', profile.device_name)
    slot_suffix = f" #{m.group(1)}" if m else ""
    client_description = f"{server_name}{slot_suffix}"

    display_key = customize_vpn_uri(
        raw_config,
        description=client_description,
        dns1="8.8.8.8",
        dns2="8.8.4.4",
        mtu="1280",
    )

    if len(display_key) > TELEGRAM_MESSAGE_LIMIT - 300:
        safe_device_name = await _get_safe_device_name(session, profile)

        key_file = BufferedInputFile(
            display_key.encode("utf-8"),
            filename=f"{safe_device_name}_key.txt",
        )

        caption = texts.DEVICE_KEY_TOO_LONG_CAPTION.format(device_name=safe(profile.device_name))

        await send_hub_document(
            callback.bot,
            callback.message.chat.id,
            document=key_file,
            caption=caption,
            reply_markup=_get_device_config_keyboard(profile.id),
            parse_mode="HTML",
        )
        return

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.DEVICE_SHOW_KEY.format(
            device_name=safe(profile.device_name),
            raw_config=safe(display_key),
        ),
        _get_device_config_keyboard(profile.id),
        trigger_message_id=callback.message.message_id,
    )


@router.callback_query(F.data.startswith("download_conf:"))
async def download_conf(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()

    profile_id = parse_callback_id(callback.data, 1)
    if profile_id is None:
        await callback.answer(texts.UI_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L176_1, show_alert=True)
        return

    profile = await get_profile_by_id(session, profile_id)
    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    has_access = await SubscriptionService.check_access(session, db_user.telegram_id)
    if not has_access:
        await callback.answer(texts.DEVICE_ACCESS_INACTIVE, show_alert=True)
        return

    await callback.answer(texts.DEVICE_CONFIG_GENERATING, show_alert=False)

    safe_device_name = await _get_safe_device_name(session, profile)

    raw_config = profile.raw_config or ""
    if not can_show_config_actions(profile) or not raw_config:
        await render_hub(
            callback.bot, callback.message.chat.id,
            texts.DOWNLOAD_CONF_FALLBACK.format(device_name=safe(profile.device_name)),
            get_back_button(f"manage_device:{profile.id}"),
            trigger_message_id=callback.message.message_id,
        )
        return

    decoded = decode_vpn_uri_to_json(raw_config)
    if decoded is None:
        await render_hub(
            callback.bot, callback.message.chat.id,
            texts.DOWNLOAD_CONF_FALLBACK.format(device_name=safe(profile.device_name)),
            get_back_button(f"manage_device:{profile.id}"),
            trigger_message_id=callback.message.message_id,
        )
        return

    server = await get_server_by_id(session, profile.server_id)
    server_name = server.name if server else "server"
    m = re.search(r'#(\d+)$', profile.device_name)
    slot_suffix = f" #{m.group(1)}" if m else ""
    client_description = f"{server_name}{slot_suffix}"

    customized_data = customize_vpn_config_dict(
        decoded,
        description=client_description,
        dns1="8.8.8.8",
        dns2="8.8.4.4",
        mtu="1280",
    )

    vpn_content = build_vpn_file_from_dict(customized_data)
    conf_content = build_conf_file_from_dict(customized_data)

    if not vpn_content or not conf_content:
        await render_hub(
            callback.bot, callback.message.chat.id,
            texts.DOWNLOAD_CONF_FALLBACK.format(device_name=safe(profile.device_name)),
            get_back_button(f"manage_device:{profile.id}"),
            trigger_message_id=callback.message.message_id,
        )
        return

    vpn_file = BufferedInputFile(vpn_content.encode("utf-8"), filename=f"{safe_device_name}.vpn")
    conf_file = BufferedInputFile(conf_content.encode("utf-8"), filename=f"{safe_device_name}.conf")

    old_hub_ids = await get_hub_ids(callback.message.chat.id)

    try:
        await append_hub_document(
            callback.bot, callback.message.chat.id,
            document=vpn_file,
            caption=texts.DEVICE_CONFIG_VPN_CAPTION.format(device_name=safe(profile.device_name)),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("Failed to send .vpn file for profile %s: %s", profile.id, e)

    try:
        await append_hub_document(
            callback.bot, callback.message.chat.id,
            document=conf_file,
            caption=texts.DEVICE_CONFIG_CONF_CAPTION.format(device_name=safe(profile.device_name)),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("Failed to send .conf file for profile %s: %s", profile.id, e)

    try:
        await append_hub_message(
            callback.bot, callback.message.chat.id,
            text=texts.DEVICE_CONFIG_INSTRUCTION,
            reply_markup=_get_device_config_keyboard(profile.id),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("Failed to send instruction message for profile %s: %s", profile.id, e)
    finally:
        try:
            await delete_hub_ids(callback.bot, callback.message.chat.id, old_hub_ids)
        except Exception as e:
            logger.error("Failed to delete old hub messages for profile %s: %s", profile.id, e)
