import logging
import re

from config.constants import (
    DEFAULT_AWG_DNS1,
    DEFAULT_AWG_DNS2,
    DEFAULT_AWG_MTU,
)
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import (
    get_device_keyboard,
)
from database.models import User
from database.repositories.profiles_repo import (
    ALLOWED_DELETE_STATES,
)
from database.repositories.servers_repo import get_server_by_id
from services.subscription import SubscriptionService
from utils.formatters import format_datetime, format_traffic
from utils.telegram import (
    render_hub,
    safe,
)
from utils.vpn_parser import (
    customize_vpn_uri,
)

from .common import _render_connections

router = Router()
logger = logging.getLogger(__name__)


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
            dns1=DEFAULT_AWG_DNS1,
            dns2=DEFAULT_AWG_DNS2,
            mtu=DEFAULT_AWG_MTU,
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

    dev_traffic = (getattr(profile, "traffic_down", 0) or 0) + (getattr(profile, "traffic_up", 0) or 0)
    header = texts.DEVICE_MANAGE_HEADER.format(
        device_name=safe(profile.device_name),
        country_display=safe(country_display),
        traffic_total=format_traffic(dev_traffic),
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
        builder.button(text=texts.BTN_BACK_TO_DEVICES, callback_data="back_to_amnezia_devices")
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
    await callback.answer(texts.ALERT_AMNEZIA_DECOMMISSIONED, show_alert=True)
    if db_user:
        await _render_connections(callback.message, db_user, session)


@router.callback_query(F.data.startswith("device_help:"))
async def device_help(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()
    await callback.answer(texts.ALERT_AMNEZIA_DECOMMISSIONED, show_alert=True)
    if db_user:
        await _render_connections(callback.message, db_user, session)


@router.callback_query(F.data.startswith("alt_connection:") | F.data.startswith("download_conf:"))
async def alt_connection(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()
    await callback.answer(texts.ALERT_AMNEZIA_DECOMMISSIONED, show_alert=True)
    if db_user:
        await _render_connections(callback.message, db_user, session)


async def render_vless_sub_screen(
    bot,
    chat_id: int,
    user: User,
    session: AsyncSession,
) -> None:
    import html
    from database.repositories import vless_subscription_repo
    from services.vless_subscription_service import VlessSubscriptionService

    sub = await vless_subscription_repo.get_or_create_subscription(session, user.id)
    token = getattr(sub, "token", None) if sub else None
    try:
        sub_url = VlessSubscriptionService.build_subscription_url(str(token)) if token else ""
    except Exception as exc:
        logger.warning("Failed to resolve VLESS subscription url for user %s: %s", user.id, exc)
        sub_url = ""

    text = texts.VLESS_SUB_SCREEN_TEXT.format(
        sub_url=html.escape(sub_url) if sub_url else "",
    )

    builder = InlineKeyboardBuilder()
    if sub_url and 1 <= len(sub_url) <= 256:
        builder.button(
            text=texts.BTN_SUB_CONNECT_INCY,
            url=sub_url,
            style="success",
        )
    builder.button(
        text=texts.BTN_BACK_TO_CONNECTION,
        callback_data="back_to_connections",
    )
    builder.button(
        text=texts.BTN_MAIN_MENU_NAV,
        callback_data="back_to_main_menu",
    )
    builder.adjust(1)

    await render_hub(
        bot,
        chat_id,
        text,
        builder.as_markup(),
    )


@router.callback_query(F.data == "vless_sub_feed_info")
async def vless_sub_feed_info(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()

    if not db_user:
        try:
            await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        except Exception:
            pass
        return

    has_access = SubscriptionService.check_vpn_access(db_user)
    if not has_access:
        try:
            await callback.answer(texts.DEVICE_ACCESS_INACTIVE, show_alert=True)
        except Exception:
            pass
        return

    await callback.answer()

    from services.vless_subscription_service import VlessSubscriptionService

    VlessSubscriptionService.ensure_synced_background(db_user.id, is_active=True, session=session)

    await render_vless_sub_screen(
        callback.bot,
        callback.message.chat.id,
        db_user,
        session,
    )


@router.callback_query(F.data == "vless_sub_reset")
async def vless_sub_reset(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()

    from services.maintenance_service import MaintenanceService
    if not await MaintenanceService.can_user_perform_action(session, callback.from_user.id):
        try:
            await callback.answer(texts.MAINTENANCE_DEFAULT_MESSAGE, show_alert=True)
        except Exception:
            pass
        return

    if not db_user:
        try:
            await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        except Exception:
            pass
        return

    if not SubscriptionService.check_vpn_access(db_user):
        try:
            await callback.answer(texts.ERROR_NO_SUBSCRIPTION, show_alert=True)
        except Exception:
            pass
        return

    from database.repositories import vless_subscription_repo

    sub = await vless_subscription_repo.get_subscription_by_user_id(session, db_user.id)
    if not sub or not sub.active_hwids:
        try:
            await callback.answer(texts.VLESS_RESET_EMPTY, show_alert=True)
        except Exception:
            pass
        return

    await callback.answer()
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_VLESS_RESET_CONFIRM,
        callback_data="vless_sub_reset_confirm",
        style="danger",
    )
    builder.button(
        text=texts.BTN_CANCEL_ACTION,
        callback_data="back_to_connections",
    )
    builder.adjust(1)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.VLESS_RESET_CONFIRM_TEXT,
        builder.as_markup(),
    )


@router.callback_query(F.data == "vless_sub_reset_confirm")
async def vless_sub_reset_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()

    from services.maintenance_service import MaintenanceService
    if not await MaintenanceService.can_user_perform_action(session, callback.from_user.id):
        try:
            await callback.answer(texts.MAINTENANCE_DEFAULT_MESSAGE, show_alert=True)
        except Exception:
            pass
        return

    if not db_user:
        try:
            await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        except Exception:
            pass
        return

    if not SubscriptionService.check_vpn_access(db_user):
        try:
            await callback.answer(texts.ERROR_NO_SUBSCRIPTION, show_alert=True)
        except Exception:
            pass
        return

    from database.repositories import vless_subscription_repo
    from services.vless_subscription_service import VlessSubscriptionService

    sub = await vless_subscription_repo.get_subscription_by_user_id(session, db_user.id)
    if not sub or not sub.active_hwids:
        try:
            await callback.answer(texts.VLESS_RESET_EMPTY, show_alert=True)
        except Exception:
            pass
        return

    old_uuid, _ = await vless_subscription_repo.reset_hwids(session, sub.id)
    if old_uuid:
        VlessSubscriptionService.deprovision_background(
            old_uuid, version=sub.version, sub_id=sub.id, session=session
        )
        VlessSubscriptionService.ensure_synced_background(db_user.id, is_active=True, session=session)

    try:
        await callback.answer(texts.VLESS_RESET_SUCCESS, show_alert=True)
    except Exception:
        pass

    await _render_connections(
        callback.message,
        db_user,
        session,
    )
