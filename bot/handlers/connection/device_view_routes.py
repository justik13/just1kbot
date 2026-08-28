import asyncio
import logging
import re

from aiogram import F, Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.constants import TELEGRAM_MESSAGE_LIMIT
from bot.keyboards import (
    get_alt_connection_keyboard,
    get_back_button,
    get_device_keyboard,
)
from database.models import User
from database.repositories.profiles_repo import (
    ALLOWED_DELETE_STATES,
    get_profile_by_id,
)
from database.repositories.servers_repo import get_server_by_id
from services.subscription import SubscriptionService
from utils.callbacks import parse_callback_id
from utils.formatters import format_datetime, format_traffic
from utils.telegram import (
    _append_hub_document_unlocked,
    _append_hub_message_unlocked,
    _delete_hub_messages,
    _get_hub_render_lock,
    _hub_cache,
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

from .common import _render_connections

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


def can_show_delete_action(profile) -> bool:
    """Check if device deletion is permissible in the current state (Fail-Closed)."""
    if not profile:
        return False
    return getattr(profile, "provisioning_status", "") in ALLOWED_DELETE_STATES


_SLOT_SUFFIX_RE = re.compile(r'#(\d+)$')


def _client_description(profile, server) -> str:
    """Stable per-client description: '<ServerName> #<slot>' used in VPN configs."""
    server_name = getattr(server, "name", None) or "server"
    device_name = getattr(profile, "device_name", "") or ""
    m = _SLOT_SUFFIX_RE.search(device_name)
    slot_suffix = f" #{m.group(1)}" if m else ""
    return f"{server_name}{slot_suffix}"


def build_display_vpn_key(raw_config: str | None, profile, server) -> str | None:
    """Build standardized display VPN URI with server name, slot suffix, DNS, and MTU."""
    if not raw_config:
        return None
    try:
        return customize_vpn_uri(
            raw_config,
            description=_client_description(profile, server),
            dns1="8.8.8.8",
            dns2="8.8.4.4",
            mtu="1280",
        )
    except Exception as exc:
        logger.warning("Failed to format vpn uri for profile %s: %s", getattr(profile, "id", None), exc)
        return raw_config


async def render_device_screen(
    bot,
    chat_id: int,
    profile,
    user: User,
    session: AsyncSession,
    message_effect_id: str | None = None,
    notice: str | None = None,
):
    server = await get_server_by_id(session, profile.server_id)
    flag = server.country_flag if server else texts.EMOJI_GLOBE
    server_name = server.name if server else texts.LABEL_UNKNOWN_CAP

    country_display = f"{flag} {server_name}".strip() if flag else server_name

    header = texts.DEVICE_MANAGE_HEADER.format(
        device_name=safe(profile.device_name),
        country_display=safe(country_display),
        traffic_total=format_traffic((getattr(profile, "traffic_down", 0) or 0) + (getattr(profile, "traffic_up", 0) or 0)),
        last_connected=(
            format_datetime(profile.last_connected)
            if getattr(profile, "last_connected", None)
            else texts.DEVICE_DATA_NONE
        ),
    )

    rendered = f"{notice}\n\n{header}" if notice else header

    status = getattr(profile, "provisioning_status", "")
    if status == "pending_create":
        rendered += texts.CONNECTION_CONFIG_DEVICE_VIEW_DEVICE_SOZDAETSYA_NA_SERVE
    elif status == "pending_update":
        rendered += texts.CONNECTION_CONFIG_DEVICE_VIEW_CONFIG_DEVICES_OBNO
    elif status == "update_failed":
        rendered += texts.CONNECTION_CONFIG_DEVICE_VIEW_NE_UDALOS_OBNOVIT_KONFIGURATSI
    elif status == "create_failed":
        rendered += texts.CONNECTION_CONFIG_DEVICE_VIEW_NE_UDALOS_SOZDAT_DEVICE_NA
    elif status == "create_cleanup_pending":
        rendered += texts.CONNECTION_CONFIG_DEVICE_VIEW_IDET_AVTOMATICHESKOE_VOSSTANOV
    elif status == "deleting":
        rendered += texts.CONNECTION_CONFIG_DEVICE_VIEW_DEVICE_UDALYAETSYA_S_SERVE
    elif status == "delete_failed":
        rendered += texts.CONNECTION_CONFIG_DEVICE_VIEW_NE_UDALOS_DELETE_DEVICE_NA

    has_access = await SubscriptionService.check_access(session, user.telegram_id)
    show_delete = can_show_delete_action(profile)

    if has_access:
        config_ready = can_show_config_actions(profile)
        display_key = None
        raw_cfg = getattr(profile, "raw_config", None)
        if config_ready and raw_cfg:
            display_key = build_display_vpn_key(raw_cfg, profile, server)

        if display_key:
            copy_hint = texts.CONNECTION_CONFIG_DEVICE_VIEW_NAZHMITE_NA_MONOSHIRINNYY_KLYU
            key_block = (
                texts.CONNECTION_CONFIG_DEVICE_VIEW_KEY_PODKLYUCHENIYA.format()+
                texts.CONNECTION_CONFIG_DEVICE_VIEW_KEY_BLOCKQUOTE.format(key=safe(display_key))+
                f"{copy_hint}"
            )
            if len(rendered) + len(key_block) <= 4000:
                rendered += key_block
            else:
                rendered += (
                    texts.CONNECTION_CONFIG_DEVICE_VIEW_KEY_PODKLYUCHENIYA+
                    texts.CONNECTION_CONFIG_DEVICE_VIEW_CONFIG_DOSTUPNA_CHEREZ
                )

        if display_key:
            amnezia_howto = texts.CONNECTION_CONFIG_DEVICE_VIEW_AMNEZIAVPN_DEFAULTVPN_SKOPIRUY
            guide_block = (
                f"\n\n<blockquote expandable>{texts.CONNECTION_CONFIG_DEVICE_VIEW_KAK_PODKLYUCHITSYA_I_PROVERIT.strip()}\n"
                f"{amnezia_howto.strip()}</blockquote>\n\n"
                f"{texts.CONNECTION_CONFIG_DEVICE_VIEW_AMNEZIAVPN_DEFAULTVPN_NAZHMITE.strip()}"
            )
        else:
            guide_block = (
                f"\n\n{texts.CONNECTION_CONFIG_DEVICE_VIEW_AMNEZIAVPN_DEFAULTVPN_NAZHMITE.strip()}"
            )
        if len(rendered) + len(guide_block) <= 4000:
            rendered += guide_block

        keyboard = get_device_keyboard(
            profile.id,
            config_ready=config_ready,
            show_delete=show_delete,
        )
    else:
        rendered += texts.DEVICE_ACCESS_INACTIVE_NOTICE

        builder = InlineKeyboardBuilder()
        if show_delete:
            builder.button(text=texts.BTN_DELETE_DEVICE, callback_data=f"request_delete_device:{profile.id}")
        builder.button(text=texts.BTN_BACK_TO_DEVICES, callback_data="back_to_connections")
        builder.button(text=texts.BTN_MAIN_MENU_NAV, callback_data="back_to_main_menu")
        builder.adjust(1)
        keyboard = builder.as_markup()

    await render_hub(
        bot,
        chat_id,
        rendered,
        keyboard,
        message_effect_id=message_effect_id,
        force_new=bool(message_effect_id),
    )


@router.callback_query(F.data.startswith("manage_device:"))
async def manage_device(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()

    profile_id = parse_callback_id(callback.data, 1)
    if profile_id is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    profile = await get_profile_by_id(session, profile_id)
    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer(texts.CONNECTION_CONFIG_DEVICE_VIEW_DEVICE_NE_NAYDENO_ILI_BYLO, show_alert=True)
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
    await callback.answer(show_alert=False)


def _get_device_config_keyboard(profile_id: int):
    # NOTE: this keyboard is shown after "Скачать файлом" and "Показать ключ".
    # - "Инструкция и помощь" uses device_help:{profile_id} (NOT support_help/menu_support)
    #   so the user stays in the device flow with a contextual back button to manage_device.
    # - Back button returns directly to the device card.
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_INSTRUKTSIYA_I_POMOSCH, callback_data=f"device_help:{profile_id}")
    builder.button(text=texts.CONNECTION_DEVICES_DEVICE_BACK_TO_DEVICE, callback_data=f"manage_device:{profile_id}")
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
    profile_id = parse_callback_id(callback.data, 1)
    if profile_id is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return
    profile = await get_profile_by_id(session, profile_id)
    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    text = (
        texts.CONNECTION_CONFIG_DEVICE_VIEW_INSTRUKTSII_I_SPRAVKA_AMNEZIAV+
        texts.CONNECTION_CONFIG_DEVICE_VIEW_V_ETOM_RAZDELE_VY_NAYDETE_RUKO+
        texts.CONNECTION_CONFIG_DEVICE_VIEW_SKACHIVANIYU_KLIENTA_I_NASTROY+
        texts.CONNECTION_CONFIG_DEVICE_VIEW_PRAVILA_I_OSOBENNOSTI_RABOTY+
        texts.CONNECTION_CONFIG_DEVICE_VIEW_NE_REKOMENDUETSYA_ISPOLZOVAT_T+
        texts.CONNECTION_THIRD_PARTY_SERVICE_NOTICE+
        texts.CONNECTION_CONFIG_DEVICE_VIEW_REKOMENDUEM_ISPOLZOVAT_PROTOKO+
        texts.CONNECTION_CONFIG_DEVICE_VIEW_SELECT_NUZHNUYU_TEMU_BELOW
    )

    builder = InlineKeyboardBuilder()
    suffix = f":device_{profile_id}"
    builder.button(text=texts.BTN_DOWNLOAD_AMNEZIA, callback_data=f"help_download{suffix}")
    builder.button(text=texts.BTN_INSTRUCTION_IOS, callback_data=f"help_ios{suffix}")
    builder.button(text=texts.BTN_INSTRUCTION_WINDOWS, callback_data=f"help_windows{suffix}")
    builder.button(text=texts.BTN_SPLIT_TUNNELING, callback_data=f"help_split{suffix}")
    builder.button(text=texts.BTN_AMNEZIA_DOCS, url=_AMNEZIA_DOCS)
    builder.button(text=texts.CONNECTION_DEVICES_DEVICE_BACK_TO_DEVICE, callback_data=f"manage_device:{profile_id}")
    builder.adjust(1)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        builder.as_markup(),
        trigger_message_id=callback.message.message_id,
    )
    await callback.answer(show_alert=False)


@router.callback_query(F.data.startswith("show_config:"))
async def show_config(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()

    profile_id = parse_callback_id(callback.data, 1)
    if profile_id is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
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
    display_key = build_display_vpn_key(raw_config, profile, server) or raw_config

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
        await callback.answer(show_alert=False)
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
    await callback.answer(show_alert=False)


@router.callback_query(F.data.startswith("alt_connection:") | F.data.startswith("download_conf:"))
async def alt_connection(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()

    profile_id = parse_callback_id(callback.data, 1)
    if profile_id is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
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
    try:
        await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action="upload_document")
    except Exception:
        pass

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

    try:
        decoded = decode_vpn_uri_to_json(raw_config)
        if decoded is None:
            raise ValueError("Failed to decode raw_config to JSON")

        server = await get_server_by_id(session, profile.server_id)
        client_description = _client_description(profile, server)

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
            raise ValueError("Empty vpn or conf file content")
    except Exception as exc:
        logger.warning("Failed to prepare alt connection files for profile %s: %s", profile.id, exc)
        await render_hub(
            callback.bot, callback.message.chat.id,
            texts.DOWNLOAD_CONF_FALLBACK.format(device_name=safe(profile.device_name)),
            get_back_button(f"manage_device:{profile.id}"),
            trigger_message_id=callback.message.message_id,
        )
        return

    vpn_file = BufferedInputFile(vpn_content.encode("utf-8"), filename=f"{safe_device_name}.vpn")
    conf_file = BufferedInputFile(conf_content.encode("utf-8"), filename=f"{safe_device_name}.conf")

    chat_lock = _get_hub_render_lock(callback.message.chat.id)

    async with chat_lock:
        old_hub_ids = await get_hub_ids(callback.message.chat.id, session=session)

        sent_doc_ids = []
        vpn_sent = False
        conf_sent = False
        guide_sent = False

        try:
            try:
                doc_msg_1 = await _append_hub_document_unlocked(
                    callback.bot, callback.message.chat.id,
                    document=vpn_file,
                    caption=texts.DEVICE_CONFIG_VPN_CAPTION.format(device_name=safe(profile.device_name)),
                    parse_mode="HTML",
                )
                sent_doc_ids.append(doc_msg_1)
                vpn_sent = True
            except (
                TelegramNetworkError,
                TelegramServerError,
                TelegramRetryAfter,
                TelegramForbiddenError,
                asyncio.TimeoutError,
            ) as e:
                # Ambiguous delivery or transient server/network/flood failure:
                # fail-closed to abort operation and preserve old hub.
                logger.warning(
                    "hub_orphan_suspected profile=%s context=alt_vpn: %s", profile.id, e,
                )
                raise
            except TelegramBadRequest as e:
                # Deterministic format/rejection error from Telegram:
                # graceful degradation to text/key instructions.
                logger.warning("Telegram rejected .vpn file for profile %s: %s", profile.id, e)
                # Continue without setting vpn_sent = True

            try:
                doc_msg_2 = await _append_hub_document_unlocked(
                    callback.bot, callback.message.chat.id,
                    document=conf_file,
                    caption=texts.DEVICE_CONFIG_CONF_CAPTION.format(device_name=safe(profile.device_name)),
                    parse_mode="HTML",
                )
                sent_doc_ids.append(doc_msg_2)
                conf_sent = True
            except (
                TelegramNetworkError,
                TelegramServerError,
                TelegramRetryAfter,
                TelegramForbiddenError,
                asyncio.TimeoutError,
            ) as e:
                logger.warning(
                    "hub_orphan_suspected profile=%s context=alt_conf: %s", profile.id, e,
                )
                raise
            except TelegramBadRequest as e:
                logger.warning("Telegram rejected .conf file for profile %s: %s", profile.id, e)
                # Continue without setting conf_sent = True

            if vpn_sent and conf_sent:
                files_info = (
                    texts.CONNECTION_CONFIG_DEVICE_VIEW_1_SOKHRANITE_ODIN_IZ_PRIKREPLE+
                    texts.CONNECTION_CONFIG_DEVICE_VIEW_VPN_FOR_PRILOZHENIYA_AMNEZIAV+
                    texts.CONNECTION_CONFIG_DEVICE_VIEW_CONF_FOR_AMNEZIAWG_DEFAULTVPN
                )
            elif vpn_sent:
                files_info = (
                    texts.CONNECTION_CONFIG_DEVICE_VIEW_1_SOKHRANITE_PRIKREPLENNYY_FAY
                )
            elif conf_sent:
                files_info = (
                    texts.CONNECTION_GUIDE_SAVE_CONF_FILE
                )
            else:
                escaped_key = safe(profile.raw_config or "")
                key_block = (
                    texts.CONNECTION_KEY_BLOCK_FORMAT.format(escaped_key=escaped_key)
                    if len(escaped_key) <= 3500
                    else texts.CONNECTION_CONFIG_DEVICE_VIEW_KEY_PODKLYUCHENIYA_DOSTUPEN
                )
                files_info = (
                    texts.CONNECTION_CONFIG_DEVICE_VIEW_NE_UDALOS_PRIKREPIT_FAYLY_KONF+
                    f"{key_block}"
                )

            alt_guide_text = (
                texts.CONNECTION_CONFIG_DEVICE_VIEW_DRUGOY_SPOSOB_PODKLYUCHENIYA.format(safe_profile_device_name=safe(profile.device_name))+
                texts.CONNECTION_CONFIG_DEVICE_VIEW_ESLI_PRYAMAYA_VSTAVKA_KEY+
                f"{files_info}"+
                texts.CONNECTION_CONFIG_DEVICE_VIEW_2_OTKROYTE_PRILOZHENIE_I_VYBER
            )

            try:
                await _append_hub_message_unlocked(
                    callback.bot, callback.message.chat.id,
                    text=alt_guide_text,
                    reply_markup=get_alt_connection_keyboard(profile.id),
                    parse_mode="HTML",
                )
                guide_sent = True
            except Exception as e:
                logger.error("Failed to send instruction message for profile %s: %s", profile.id, e)

            if guide_sent:
                if old_hub_ids:
                    try:
                        failed_old = await asyncio.shield(_delete_hub_messages(callback.bot, callback.message.chat.id, old_hub_ids))
                    except Exception as e:
                        # Durable cleanup uncertain: invalidate cache so the next
                        # render re-reads DB truth instead of hiding stale rows.
                        _hub_cache.pop(callback.message.chat.id, None)
                        failed_old = list(old_hub_ids)
                        logger.error("Failed to delete old hub messages for profile %s: %s", profile.id, e)
                    if failed_old:
                        # Keep undeletable ids visible to the next render (cache mirrors DB truth).
                        entry = _hub_cache.setdefault(
                            callback.message.chat.id, {"ids": [], "effect_msg_id": None}
                        )
                        for fid in failed_old:
                            if fid not in entry["ids"]:
                                entry["ids"].append(fid)
            else:
                if sent_doc_ids:
                    try:
                        await asyncio.shield(_delete_hub_messages(callback.bot, callback.message.chat.id, sent_doc_ids))
                    except Exception as clean_exc:
                        logger.error("Failed to cleanup partial documents for profile %s: %s", profile.id, clean_exc)
        except BaseException as root_exc:
            if sent_doc_ids and not guide_sent:
                try:
                    await asyncio.shield(_delete_hub_messages(callback.bot, callback.message.chat.id, sent_doc_ids))
                except Exception:
                    pass
            raise root_exc
