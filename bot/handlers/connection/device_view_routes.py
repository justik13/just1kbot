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
from bot.constants import AMNEZIA_PROTOCOL, TELEGRAM_MESSAGE_LIMIT
from bot.keyboards import (
    get_alt_connection_keyboard,
    get_back_button,
    get_device_keyboard,
)
from config.settings import get_settings
from database.models import User
from database.repositories.profiles_repo import (
    ALLOWED_DELETE_STATES,
    get_profile_by_id,
)
from database.repositories.servers_repo import get_server_by_id
from integrations.amnezia_bridge import (
    AmneziaBridgeTokenService,
)
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
):
    server = await get_server_by_id(session, profile.server_id)
    flag = server.country_flag if server else texts.RUNTIME_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L60_1
    server_name = server.name if server else texts.RUNTIME_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L61_1
    protocol = _format_protocol(server.protocol if server else None)

    country_display = f"{flag} {server_name}".strip() if flag else server_name

    rendered = texts.DEVICE_MANAGE_HEADER.format(
        device_name=safe(profile.device_name),
        country_display=safe(country_display),
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
        rendered += texts.UI_DEVICE_VIEW_ROUTES_USTROYSTVO_SOZDAETSYA_NA_SERVE_177
    elif status == "pending_update":
        rendered += texts.UI_DEVICE_VIEW_ROUTES_KONFIGURATSIYA_USTROYSTVA_OBNO_179
    elif status == "update_failed":
        rendered += texts.UI_DEVICE_VIEW_ROUTES_NE_UDALOS_OBNOVIT_KONFIGURATSI_181
    elif status == "create_failed":
        rendered += texts.UI_DEVICE_VIEW_ROUTES_NE_UDALOS_SOZDAT_USTROYSTVO_NA_183
    elif status == "create_cleanup_pending":
        rendered += texts.UI_DEVICE_VIEW_ROUTES_IDET_AVTOMATICHESKOE_VOSSTANOV_185
    elif status == "deleting":
        rendered += texts.UI_DEVICE_VIEW_ROUTES_USTROYSTVO_UDALYAETSYA_S_SERVE_187
    elif status == "delete_failed":
        rendered += texts.UI_DEVICE_VIEW_ROUTES_NE_UDALOS_UDALIT_USTROYSTVO_NA_189

    has_access = await SubscriptionService.check_access(session, user.telegram_id)
    show_delete = can_show_delete_action(profile)

    if has_access:
        config_ready = can_show_config_actions(profile)
        display_key = None
        raw_cfg = getattr(profile, "raw_config", None)
        if config_ready and raw_cfg:
            display_key = build_display_vpn_key(raw_cfg, profile, server)

        if display_key:
            copy_hint = texts.UI_DEVICE_VIEW_ROUTES_NAZHMITE_NA_MONOSHIRINNYY_KLYU_202
            key_block = (
                texts.UI_DEVICE_VIEW_ROUTES_KLYUCH_PODKLYUCHENIYA_204.format()+
                f"<blockquote expandable><code>{safe(display_key)}</code></blockquote>\n"+
                f"{copy_hint}"
            )
            if len(rendered) + len(key_block) <= 4000:
                rendered += key_block
            else:
                rendered += (
                    texts.UI_DEVICE_VIEW_ROUTES_KLYUCH_PODKLYUCHENIYA_212+
                    texts.UI_DEVICE_VIEW_ROUTES_KONFIGURATSIYA_DOSTUPNA_CHEREZ_213
                )

        if display_key:
            amnezia_howto = (
                texts.UI_DEVICE_VIEW_ROUTES_AMNEZIAVPN_DEFAULTVPN_SKOPIRUY_218+
                texts.UI_DEVICE_VIEW_ROUTES_OTKROYTE_PRILOZHENIE_NAZHMITE__219
            )
        else:
            amnezia_howto = (
                texts.UI_DEVICE_VIEW_ROUTES_AMNEZIAVPN_DEFAULTVPN_NAZHMITE_223+
                texts.UI_DEVICE_VIEW_ROUTES_CHTOBY_POLUCHIT_FAYL_KONFIGURA_224
            )
        guide_block = (
            texts.UI_DEVICE_VIEW_ROUTES_KAK_PODKLYUCHITSYA_I_PROVERIT__227+
            f"{amnezia_howto}\n"+
            texts.UI_DEVICE_VIEW_ROUTES_INCY_IOS_ANDROID_OTKROYTE_PODK_229+
            texts.UI_DEVICE_VIEW_ROUTES_KAK_PONYAT_CHTO_VSE_RABOTAET_230+
            texts.UI_DEVICE_VIEW_ROUTES_1_V_PRILOZHENII_STATUS_SMENITS_231+
            texts.UI_DEVICE_VIEW_ROUTES_2_V_STROKE_SOSTOYANIYA_POYAVIT_232+
            texts.UI_DEVICE_VIEW_ROUTES_3_NA_SAYTE_2IP_RU_STRANA_SMENI_233+
            texts.UI_DEVICE_VIEW_ROUTES_4_POPULYARNYE_SERVISY_I_ZARUBE_234
        )
        if len(rendered) + len(guide_block) <= 4000:
            rendered += guide_block

        btn_info_lines = [
            texts.UI_DEVICE_VIEW_ROUTES_KNOPKI_UPRAVLENIYA_240,
        ]
        if config_ready:
            btn_info_lines.append(texts.UI_DEVICE_VIEW_ROUTES_DRUGOY_SPOSOB_SKACHAT_FAYLOM_V_243)
        btn_info_lines.append(texts.UI_DEVICE_VIEW_ROUTES_PEREIMENOVAT_IZMENIT_NAZVANIE__244)
        btn_info_lines.append(texts.UI_DEVICE_VIEW_ROUTES_INSTRUKTSIYA_POSHAGOVOE_RUKOVO_245)
        if show_delete:
            btn_info_lines.append(texts.UI_DEVICE_VIEW_ROUTES_UDALIT_OTOZVAT_KLYUCH_I_OSVOBO_247)

        btn_info = "\n".join(btn_info_lines)
        if len(rendered) + len(btn_info) <= 4000:
            rendered += btn_info

        keyboard = get_device_keyboard(
            profile.id,
            config_ready=config_ready,
            show_delete=show_delete,
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
        await callback.answer(texts.UI_BOT_HANDLERS_CONNECTION_DEVICE_VIEW_ROUTES_L51_1, show_alert=True)
        return

    profile = await get_profile_by_id(session, profile_id)
    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer(texts.UI_DEVICE_VIEW_ROUTES_USTROYSTVO_NE_NAYDENO_ILI_BYLO_295, show_alert=True)
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
    builder.button(text=texts.UI_BOT_KEYBOARDS_DEVICE_BACK_TO_DEVICE, callback_data=f"manage_device:{profile_id}")
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
        await callback.answer(texts.UI_DEVICE_VIEW_ROUTES_NEKORREKTNYY_ZAPROS_337, show_alert=True)
        return
    profile = await get_profile_by_id(session, profile_id)
    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    text = (
        texts.UI_DEVICE_VIEW_ROUTES_INSTRUKTSII_I_SPRAVKA_AMNEZIAV_345+
        texts.UI_DEVICE_VIEW_ROUTES_V_ETOM_RAZDELE_VY_NAYDETE_RUKO_346+
        texts.UI_DEVICE_VIEW_ROUTES_SKACHIVANIYU_KLIENTA_I_NASTROY_347+
        texts.UI_DEVICE_VIEW_ROUTES_PRAVILA_I_OSOBENNOSTI_RABOTY_348+
        texts.UI_DEVICE_VIEW_ROUTES_NE_REKOMENDUETSYA_ISPOLZOVAT_T_349+
        texts.UI_DEVICE_VIEW_ROUTES_CHAST_SAYTOV_SERVISOV_MOZHET_B_350+
        texts.UI_DEVICE_VIEW_ROUTES_REKOMENDUEM_ISPOLZOVAT_PROTOKO_351+
        texts.UI_DEVICE_VIEW_ROUTES_VYBERITE_NUZHNUYU_TEMU_NIZHE_352
    )

    builder = InlineKeyboardBuilder()
    suffix = f":device_{profile_id}"
    builder.button(text=texts.BTN_SKACHAT_KLIENT_AMNEZIA, callback_data=f"help_download{suffix}")
    builder.button(text=texts.BTN_INSTRUKTSIYA_IOS_DLYA_RF, callback_data=f"help_ios{suffix}")
    builder.button(text=texts.BTN_INSTRUKTSII_WINDOWS, callback_data=f"help_windows{suffix}")
    builder.button(text=texts.BTN_RAZDELNOE_TUNNELIROVANIE, callback_data=f"help_split{suffix}")
    builder.button(text=texts.BTN_DOKUMENTATSIYA_AMNEZIA, url=_AMNEZIA_DOCS)
    builder.button(text=texts.UI_BOT_KEYBOARDS_DEVICE_BACK_TO_DEVICE, callback_data=f"manage_device:{profile_id}")
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

    amnezia_bridge_url = None
    if can_show_amnezia_bridge(profile, server):
        settings = get_settings()
        amnezia_bridge_url = AmneziaBridgeTokenService.build_bridge_url(
            domain=settings.DOMAIN,
            profile_id=profile.id,
            user_id=db_user.id,
        )

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
                    texts.UI_DEVICE_VIEW_ROUTES_1_SOKHRANITE_ODIN_IZ_PRIKREPLE_591+
                    texts.UI_DEVICE_VIEW_ROUTES_VPN_DLYA_PRILOZHENIYA_AMNEZIAV_592+
                    texts.UI_DEVICE_VIEW_ROUTES_CONF_DLYA_AMNEZIAWG_DEFAULTVPN_593
                )
            elif vpn_sent:
                files_info = (
                    texts.UI_DEVICE_VIEW_ROUTES_1_SOKHRANITE_PRIKREPLENNYY_FAY_597
                )
            elif conf_sent:
                files_info = (
                    texts.UI_DEVICE_VIEW_ROUTES_1_SOKHRANITE_PRIKREPLENNYY_FAY_601
                )
            else:
                escaped_key = safe(profile.raw_config or "")
                key_block = (
                    texts.UI_DEVICE_VIEW_ROUTES_KLYUCH_PODKLYUCHENIYA_606.format(escaped_key=escaped_key)
                    if len(escaped_key) <= 3500
                    else texts.UI_DEVICE_VIEW_ROUTES_KLYUCH_PODKLYUCHENIYA_DOSTUPEN_608
                )
                files_info = (
                    texts.UI_DEVICE_VIEW_ROUTES_NE_UDALOS_PRIKREPIT_FAYLY_KONF_611+
                    f"{key_block}"
                )

            bridge_hint = texts.UI_DEVICE_VIEW_ROUTES_3_LIBO_NAZHMITE_KNOPKU_OTKRYT__615 if amnezia_bridge_url else ""
            alt_guide_text = (
                texts.UI_DEVICE_VIEW_ROUTES_DRUGOY_SPOSOB_PODKLYUCHENIYA_617.format(safe_profile_device_name=safe(profile.device_name))+
                texts.UI_DEVICE_VIEW_ROUTES_ESLI_PRYAMAYA_VSTAVKA_KLYUCHA__618+
                f"{files_info}"+
                texts.UI_DEVICE_VIEW_ROUTES_2_OTKROYTE_PRILOZHENIE_I_VYBER_620+
                f"{bridge_hint}"
            )

            try:
                await _append_hub_message_unlocked(
                    callback.bot, callback.message.chat.id,
                    text=alt_guide_text,
                    reply_markup=get_alt_connection_keyboard(profile.id, amnezia_bridge_url),
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
