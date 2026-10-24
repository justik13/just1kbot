import logging
import re
from config.constants import (
    AMNEZIA_PROTOCOLS,
    GRACE_PERIOD_HOURS,
)
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from database.models import User
from bot.keyboards import make_rich_nav_row
from database.repositories.profiles_repo import (
    PROFILE_QUOTA_EXCLUDED_STATUSES,
    get_user_profiles,
)
from services.subscription import SubscriptionService
from utils.datetime_helpers import now_utc
from utils.formatters import format_traffic
from utils.telegram import (
    make_rich_button,
    make_rich_button_row,
    render_hub,
    safe,
)

logger = logging.getLogger(__name__)

DEVICE_NAME_REGEX = re.compile(r"^[a-zA-Z\u0400-\u04FF0-9\s_#-]+$")

_PROTOCOL_DISPLAY = {p: "AmneziaWG" for p in AMNEZIA_PROTOCOLS}


def _format_protocol(raw_protocol: str | None) -> str:
    if not raw_protocol:
        return texts.PLACEHOLDER_DASH
    return _PROTOCOL_DISPLAY.get(raw_protocol, raw_protocol)


async def _get_effective_device_limit(
    session: AsyncSession,
    user: User,
) -> int:
    return await SubscriptionService.get_effective_device_limit(session, user)


def _get_grace_deletion_time(user: User):
    if not user.subscription_end:
        return None

    from utils.datetime_helpers import is_permanent_subscription
    if is_permanent_subscription(user.subscription_end):
        return None

    return user.subscription_end + timedelta(
        hours=GRACE_PERIOD_HOURS,
    )


def _format_grace_countdown(deletion_time) -> str:
    if not deletion_time:
        return texts.TIME_SOON_LABEL

    current_time = now_utc()
    delta = deletion_time - current_time

    if delta.total_seconds() <= 0:
        return texts.TIME_SOON_LABEL

    days = delta.days
    hours = delta.seconds // 3600

    if days > 0:
        return texts.CONNECTION_CONFIG_ESTIMATED_TIME_HOURS.format(v0=days, v1=hours)

    minutes = (delta.seconds % 3600) // 60
    return texts.CONNECTION_CONFIG_PROTOCOL_FORMAT.format(v0=hours, v1=minutes)


async def _render_maintenance(
    target,
    session: AsyncSession,
    *,
    back_to: str = "back_to_connections",
) -> None:
    from bot.handlers.shared_common import render_maintenance

    await render_maintenance(target, session, back_to=back_to)


async def _build_connections_screen(
    user: User,
    session: AsyncSession,
    profiles: list,
    *,
    read_only: bool = False,
) -> tuple[str, None]:
    quota_profiles_count = len([
        p for p in profiles
        if getattr(p, "provisioning_status", "") not in PROFILE_QUOTA_EXCLUDED_STATUSES
    ])

    from database.repositories import vless_subscription_repo
    from services.vless_subscription_service import VlessSubscriptionService

    try:
        sub = await vless_subscription_repo.get_subscription_by_user_id(session, user.id)
        if not sub and not read_only:
            sub = await vless_subscription_repo.get_or_create_subscription(session, user.id)
        token = getattr(sub, "token", None) if sub else None
        sub_url = VlessSubscriptionService.build_subscription_url(str(token)) if token else ""
    except Exception as exc:
        logger.warning("Failed to resolve VLESS subscription url for user %s: %s", user.id, exc)
        sub_url = ""

    try:
        vless_hwid_count = await vless_subscription_repo.get_active_hwid_count(session, user.id)
        if not isinstance(vless_hwid_count, int):
            vless_hwid_count = 0
    except Exception:
        vless_hwid_count = 0

    total_active_devices = quota_profiles_count + vless_hwid_count

    device_limit = await _get_effective_device_limit(
        session,
        user,
    )

    if read_only:
        deletion_time = _get_grace_deletion_time(user)
        if deletion_time:
            countdown = _format_grace_countdown(deletion_time)
            grace_notice = texts.CONNECTION_EXPIRED_READ_ONLY.format(
                countdown=countdown,
            )
        else:
            grace_notice = texts.CONNECTION_EXPIRED_NO_GRACE
        rendered = texts.CONNECTION_HUB_EXPIRED_TEXT.format(
            grace_notice=grace_notice,
        )
        rendered += "\n" + make_rich_button_row(
            make_rich_button(texts.BTN_BUY_ACCESS, callback_data="menu_buy", style="success")
        )
        rendered += "\n" + make_rich_nav_row(include_menu=True)
    else:
        from bot.formatters import format_subscription_date, get_tariff_display_name
        expiry_str = (
            format_subscription_date(user.subscription_end)
            if user.subscription_end
            else texts.TIME_FOREVER
        )
        tariff_name = None
        if getattr(user, "current_tariff_id", None):
            from database.repositories import tariffs_repo
            user_tariff = await tariffs_repo.get_tariff_by_id(session, user.current_tariff_id)
            if (
                user_tariff
                and getattr(user_tariff, "name", None)
                and (getattr(user_tariff, "service_type", None) or "standard") in ("standard", "awg")
            ):
                tariff_name = user_tariff.name
        if not tariff_name:
            tariff_name = get_tariff_display_name(device_limit)

        sub_button_row = (
            "\n"
            + make_rich_button_row(
                make_rich_button(
                    texts.BTN_SUB_CONNECT_INCY, url=sub_url, style="success"
                )
            )
            if sub_url
            else ""
        )
        rendered = texts.CONNECTION_HUB_ACTIVE_TEXT.format(
            expiry=expiry_str,
            tariff=tariff_name,
            active=total_active_devices,
            limit=device_limit,
            vless_count=vless_hwid_count,
            amnezia_count=quota_profiles_count,
            sub_url=sub_url,
            sub_button_row=sub_button_row,
        )

        action_rows = []

        amnezia_btn_text = (
            texts.BTN_AMNEZIA_DEVICES_COUNT.format(count=quota_profiles_count)
            if quota_profiles_count > 0
            else texts.BTN_AMNEZIA_DEVICES
        )
        action_rows.append(
            make_rich_button_row(
                make_rich_button(texts.BTN_INCY_INSTRUCTIONS, callback_data="vless_sub_feed_info", style="primary"),
                make_rich_button(amnezia_btn_text, callback_data="amnezia_devices", style="success"),
            )
        )

        if vless_hwid_count > 0:
            action_rows.append(
                make_rich_button_row(
                    make_rich_button(texts.BTN_VLESS_RESET_HWID, callback_data="vless_sub_reset", style="danger")
                )
            )

        action_rows.append(
            make_rich_button_row(
                make_rich_button(texts.CONNECTION_CONFIG_COMMON_STATUS_SERVEROV, url="https://status.just1k.pro/status/just1kbot"),
                make_rich_button(texts.BTN_MAIN_MENU_NAV, callback_data="back_to_main_menu"),
            )
        )
        rendered += "\n" + "\n".join(action_rows)

    return rendered, None


async def _build_amnezia_screen(
    user: User,
    session: AsyncSession,
    profiles: list,
    *,
    read_only: bool = False,
) -> tuple[str, None]:
    quota_profiles_count = len([
        p for p in profiles
        if getattr(p, "provisioning_status", "") not in PROFILE_QUOTA_EXCLUDED_STATUSES
    ])

    from database.repositories import vless_subscription_repo
    vless_hwid_count = await vless_subscription_repo.get_active_hwid_count(session, user.id)
    total_active_devices = quota_profiles_count + vless_hwid_count

    device_limit = await _get_effective_device_limit(
        session,
        user,
    )

    if not profiles:
        action_hint = (
            texts.AMNEZIA_SCREEN_HINT_ADD
            if (not read_only and total_active_devices < device_limit)
            else (texts.AMNEZIA_SCREEN_HINT_LIMIT if not read_only else "")
        )
        add_device_row = (
            texts.AMNEZIA_ADD_DEVICE_ROW
            if (not read_only and total_active_devices < device_limit)
            else ""
        )
        rendered = texts.AMNEZIA_SCREEN_EMPTY_TEXT.format(
            active=total_active_devices,
            limit=device_limit,
            vless_count=vless_hwid_count,
            action_hint=action_hint,
            add_device_row=add_device_row,
        )
        nav_row = make_rich_button_row(
            make_rich_button(texts.BTN_BACK_TO_CONNECTION, callback_data="back_to_connections"),
            make_rich_button(texts.BTN_MAIN_MENU_NAV, callback_data="back_to_main_menu"),
        )
        rendered += f"\n{nav_row}"
    else:
        row_items = []
        device_rows = []
        for profile in profiles:
            server = profile.server

            flag = server.country_flag if server else texts.EMOJI_GLOBE
            server_name = server.name if server else texts.LABEL_UNKNOWN_CAP
            raw_device_name = profile.device_name or texts.DEVICE_DEFAULT_NAME_TEMPLATE.format(slot=1)
            btn_text = f"{flag} {raw_device_name}"
            device_rows.append(
                make_rich_button_row(
                    make_rich_button(btn_text, callback_data=f"manage_device:{profile.id}", style="primary")
                )
            )

            location_label = f"{flag} {safe(server_name)}"

            from utils.traffic_helpers import get_archived_traffic_for_device

            archived_bytes = get_archived_traffic_for_device(
                getattr(user, "archived_device_traffic", None), profile.device_name
            )
            dev_bytes = (getattr(profile, "traffic_down", 0) or 0) + (getattr(profile, "traffic_up", 0) or 0) + archived_bytes
            traffic_str = format_traffic(dev_bytes)

            status_suffix = ""
            labels = {
                "pending_create": texts.DEVICE_STATUS_CREATING,
                "pending_update": texts.PROVISIONING_UPDATING,
                "deleting": texts.DEVICE_STATUS_DELETING,
                "create_failed": texts.PROVISIONING_CREATE_FAILED,
                "create_cleanup_pending": texts.DEVICE_STATUS_CLEANUP,
                "update_failed": texts.DEVICE_STATUS_UPDATE_ERROR,
                "delete_failed": texts.PROVISIONING_DELETE_FAILED,
            }
            if profile.provisioning_status in labels:
                status_suffix = f" ({labels[profile.provisioning_status]})"

            row_items.append(
                f"<tr><td>📱 {safe(profile.device_name)}{status_suffix}</td><td>{location_label}</td><td>{traffic_str}</td></tr>"
            )

        profiles_block = "\n" + texts.AMNEZIA_DEVICES_TABLE_TEMPLATE.format(rows="".join(row_items))

        rendered = texts.AMNEZIA_SCREEN_TEXT.format(
            active=total_active_devices,
            limit=device_limit,
            vless_count=vless_hwid_count,
            amnezia_count=quota_profiles_count,
            profiles_block=profiles_block,
        )

        action_rows = []
        if not read_only and total_active_devices < device_limit and profiles:
            action_rows.append(
                make_rich_button_row(
                    make_rich_button(texts.BTN_ADD_DEVICE, callback_data="add_device", style="success")
                )
            )

        action_rows.append(
            make_rich_button_row(
                make_rich_button(texts.BTN_BACK_TO_CONNECTION, callback_data="back_to_connections"),
                make_rich_button(texts.BTN_MAIN_MENU_NAV, callback_data="back_to_main_menu"),
            )
        )
        rendered += "\n" + "\n".join(device_rows) + "\n" + "\n".join(action_rows)

    return rendered, None


async def _render_connections(
    target,
    user: User,
    session: AsyncSession,
):
    if not user:
        await render_hub(
            target.bot,
            target.chat.id,
            f"{texts.ERROR_USER_NOT_FOUND}\n{make_rich_nav_row('back_to_main_menu', include_menu=False)}",
            reply_markup=None,
        )
        return

    has_access = await SubscriptionService.check_access(
        session,
        user.telegram_id,
    )

    visible_profiles = await get_user_profiles(
        session,
        user.id,
    )
    visible_profiles_count = len(visible_profiles)

    if not has_access:
        if visible_profiles_count > 0:
            rendered, _ = await _build_amnezia_screen(
                user,
                session,
                visible_profiles,
                read_only=True,
            )
            buy_btn = make_rich_button(texts.BTN_BUY_ACCESS, callback_data="menu_buy", style="success")
            nav_row = make_rich_nav_row(include_menu=True)
            rendered += f"\n{make_rich_button_row(buy_btn)}\n{nav_row}"
            await render_hub(
                target.bot,
                target.chat.id,
                rendered,
                reply_markup=None,
            )
            return

        buy_btn = make_rich_button(texts.BTN_BUY_ACCESS, callback_data="menu_buy", style="success")
        nav_row = make_rich_nav_row(include_menu=True)
        rendered = texts.ERROR_NO_SUBSCRIPTION + f"\n{make_rich_button_row(buy_btn)}\n{nav_row}"
        await render_hub(
            target.bot,
            target.chat.id,
            rendered,
            reply_markup=None,
        )
        return

    rendered, _ = await _build_connections_screen(
        user,
        session,
        visible_profiles,
        read_only=False,
    )

    await render_hub(
        target.bot,
        target.chat.id,
        rendered,
        reply_markup=None,
        trigger_message_id=getattr(target, "message_id", None),
    )


async def _render_amnezia_devices(
    target,
    user: User,
    session: AsyncSession,
):
    if not user:
        await render_hub(
            target.bot,
            target.chat.id,
            f"{texts.ERROR_USER_NOT_FOUND}\n{make_rich_nav_row('back_to_main_menu', include_menu=False)}",
            reply_markup=None,
        )
        return

    has_access = await SubscriptionService.check_access(
        session,
        user.telegram_id,
    )
    visible_profiles = await get_user_profiles(
        session,
        user.id,
    )

    rendered, _ = await _build_amnezia_screen(
        user,
        session,
        visible_profiles,
        read_only=not has_access,
    )

    await render_hub(
        target.bot,
        target.chat.id,
        rendered,
        reply_markup=None,
        trigger_message_id=getattr(target, "message_id", None),
    )

