import logging
import re
from config.constants import (
    AMNEZIA_PROTOCOLS,
    GRACE_PERIOD_HOURS,
)
from datetime import timedelta

from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from config.settings import get_settings
from database.models import User
from services.subscription import SubscriptionService
from utils.datetime_helpers import now_utc
from utils.telegram import render_hub

logger = logging.getLogger(__name__)

DEVICE_NAME_REGEX = re.compile(r"^[a-zA-Z\u0400-\u04FF0-9\s_#-]+$")

_PROTOCOL_DISPLAY = {p: "Архивное подключение" for p in AMNEZIA_PROTOCOLS}


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
    profiles: list | None = None,
    *,
    read_only: bool = False,
) -> tuple[str, InlineKeyboardBuilder]:
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

    total_active_devices = vless_hwid_count

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

        rendered = texts.CONNECTION_HUB_ACTIVE_TEXT.format(
            expiry=expiry_str,
            tariff=tariff_name,
            active=total_active_devices,
            limit=device_limit,
            sub_url=sub_url,
        )

    builder = InlineKeyboardBuilder()

    has_sub_btn = bool(sub_url and 1 <= len(sub_url) <= 256)
    if not read_only:
        if has_sub_btn:
            builder.button(
                text=texts.BTN_SUB_CONNECT_INCY,
                url=sub_url,
                style="success",
            )
        builder.button(
            text=texts.BTN_INCY_INSTRUCTIONS,
            callback_data="vless_sub_feed_info",
        )
        if vless_hwid_count > 0:
            builder.button(
                text=texts.BTN_VLESS_RESET_HWID,
                callback_data="vless_sub_reset",
                style="danger",
            )

    try:
        status_url = get_settings().STATUS_PAGE_URL
    except Exception:
        status_url = "https://status.just1k.pro/status/just1kbot"

    builder.button(
        text=texts.CONNECTION_CONFIG_COMMON_STATUS_SERVEROV,
        url=status_url,
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

    if not has_access:
        if user.subscription_end:
            rendered, builder = await _build_connections_screen(
                user,
                session,
                read_only=True,
            )
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
        read_only=False,
    )

    await render_hub(
        target.bot,
        target.chat.id,
        rendered,
        builder.as_markup(),
        trigger_message_id=getattr(target, "message_id", None),
    )


async def _render_amnezia_devices(
    target,
    user: User,
    session: AsyncSession,
):
    await _render_connections(target, user, session)

