import logging
import re
from datetime import timedelta

from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.constants import AMNEZIA_PROTOCOL, GRACE_PERIOD_HOURS
from bot.keyboards import get_back_button
from database.models import User
from database.repositories.profiles_repo import (
    PROFILE_QUOTA_EXCLUDED_STATUSES,
    get_user_profiles,
)
from services.subscription import SubscriptionService
from utils.datetime_helpers import now_utc
from utils.formatters import format_datetime, format_traffic
from utils.telegram import render_hub, safe

logger = logging.getLogger(__name__)

DEVICE_NAME_REGEX = re.compile(r"^[a-zA-Z\u0400-\u04FF0-9\s_#-]+$")

_PROTOCOL_DISPLAY = {
    AMNEZIA_PROTOCOL: "AmneziaWG",
}


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
) -> tuple[str, InlineKeyboardBuilder]:
    visible_profiles_count = len(profiles)

    quota_profiles_count = len([
        p for p in profiles
        if getattr(p, "provisioning_status", "") not in PROFILE_QUOTA_EXCLUDED_STATUSES
    ])

    device_limit = await _get_effective_device_limit(
        session,
        user,
    )

    rendered = texts.CONNECTION_LIST_HEADER.format(
        count=quota_profiles_count,
        limit=device_limit,
    )

    if read_only:
        deletion_time = _get_grace_deletion_time(user)

        if deletion_time:
            countdown = _format_grace_countdown(deletion_time)
            rendered += texts.CONNECTION_EXPIRED_READ_ONLY.format(
                countdown=countdown,
            )
        else:
            rendered += texts.CONNECTION_EXPIRED_NO_GRACE

    builder = InlineKeyboardBuilder()

    if not read_only and quota_profiles_count < device_limit:
        builder.button(
            text=texts.CONNECTION_CONFIG_UNKNOWN_PROTOCOL,
            callback_data="add_device",
            style="success",
        )

    if visible_profiles_count == 0:
        rendered += texts.CONNECTION_EMPTY
    else:
        for profile in profiles:
            server = profile.server

            flag = server.country_flag if server else texts.EMOJI_GLOBE
            server_name = server.name if server else texts.LABEL_UNKNOWN_CAP
            raw_device_name = profile.device_name or texts.DEVICE_DEFAULT_NAME_TEMPLATE.format(slot=1)
            btn_text = f"{flag} {server_name} — {raw_device_name}"
            builder.button(
                text=btn_text,
                callback_data=f"manage_device:{profile.id}",
            )

            location_label = f"{flag} {safe(server_name)}"

            traffic_str = format_traffic((getattr(profile, "traffic_down", 0) or 0) + (getattr(profile, "traffic_up", 0) or 0))
            last_conn_str = format_datetime(profile.last_connected) if getattr(profile, "last_connected", None) else texts.CONNECTION_CONFIG_COMMON_NE_BYLO_AKTIVNOSTEY

            rendered += texts.CONNECTION_DEVICE_ROW_FORMAT.format(
                device_name=safe(profile.device_name),
                location=location_label,
                traffic=traffic_str,
                last_conn=last_conn_str,
            )
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
                rendered += texts.DEVICE_STATUS_LINE_FORMAT.format(v0=labels[profile.provisioning_status])

        rendered += texts.CONNECTION_CONFIG_COMMON_NAZHMITE_NA_DEVICE_BELOW_D

    builder.button(
        text=texts.CONNECTION_CONFIG_COMMON_STATUS_SERVEROV,
        url="https://stats.uptimerobot.com/de5q3DNc95",
    )
    builder.button(
        text=texts.BTN_MAIN_MENU_NAV,
        callback_data="back_to_main_menu",
    )

    builder.adjust(1)

    return rendered, builder


async def _render_connections(
    target,
    user: User,
    session: AsyncSession,
):
    if not user:
        await render_hub(
            target.bot,
            target.chat.id,
            texts.ERROR_USER_NOT_FOUND,
            get_back_button("back_to_main_menu"),
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
            rendered, builder = await _build_connections_screen(
                user,
                session,
                visible_profiles,
                read_only=True,
            )

            builder.button(
                text=texts.BTN_BUY_ACCESS,
                callback_data="menu_buy",
            )
            builder.adjust(1)

            await render_hub(
                target.bot,
                target.chat.id,
                rendered,
                builder.as_markup(),
            )
            return

        builder = InlineKeyboardBuilder()
        builder.button(
            text=texts.BTN_BUY_ACCESS,
            callback_data="menu_buy",
        )
        builder.button(
            text=texts.BTN_MAIN_MENU_NAV,
            callback_data="back_to_main_menu",
        )
        builder.adjust(1)

        await render_hub(
            target.bot,
            target.chat.id,
            texts.ERROR_NO_SUBSCRIPTION,
            builder.as_markup(),
        )
        return

    rendered, builder = await _build_connections_screen(
        user,
        session,
        visible_profiles,
        read_only=False,
    )

    await render_hub(
        target.bot,
        target.chat.id,
        rendered,
        builder.as_markup(),
        trigger_message_id=getattr(target, "message_id", None),
    )
