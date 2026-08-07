import logging
import re
from datetime import timedelta

from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from database.models import User
from database.repositories.profiles_repo import (
    get_user_profiles,
    get_user_profiles_count,
)
from database.repositories.tariffs_repo import get_tariff_by_id
from services.maintenance_service import MaintenanceService
from services.subscription import SubscriptionService
from utils.datetime_helpers import now_utc
from utils.telegram import render_hub, safe
from bot.constants import GRACE_PERIOD_HOURS

logger = logging.getLogger(__name__)

DEVICE_NAME_REGEX = re.compile(r"^[a-zA-Zа-яА-ЯёЁ0-9\s_-]+$")

_PROTOCOL_DISPLAY = {
    "amneziawg2": "AmneziaWG",
}


def _format_protocol(raw_protocol: str | None) -> str:
    if not raw_protocol:
        return texts.RUNTIME_BOT_HANDLERS_CONNECTION_COMMON_L37_1
    return _PROTOCOL_DISPLAY.get(raw_protocol, raw_protocol)


async def _get_effective_device_limit(
    user: User,
    session: AsyncSession,
) -> int:
    if user.current_tariff_id:
        tariff = await get_tariff_by_id(
            session,
            user.current_tariff_id,
        )
        if tariff:
            return tariff.device_limit
    return user.device_limit or 0


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
        return texts.RUNTIME_BOT_HANDLERS_CONNECTION_COMMON_L69_1

    current_time = now_utc()
    delta = deletion_time - current_time

    if delta.total_seconds() <= 0:
        return texts.RUNTIME_BOT_HANDLERS_CONNECTION_COMMON_L75_1

    days = delta.days
    hours = delta.seconds // 3600

    if days > 0:
        return texts.RUNTIME_BOT_HANDLERS_CONNECTION_COMMON_L81_1.format(value_0=days, value_1=hours)

    minutes = (delta.seconds % 3600) // 60
    return texts.RUNTIME_BOT_HANDLERS_CONNECTION_COMMON_L84_1.format(value_0=hours, value_1=minutes)


async def _render_maintenance(
    target,
    session: AsyncSession,
    *,
    back_to: str = "back_to_connections",
) -> None:
    if target is None:
        return
    bot = getattr(target, "bot", None)
    chat = getattr(target, "chat", None)
    chat_id = chat.id if chat else None
    if (bot is None or chat_id is None) and isinstance(target, CallbackQuery):
        bot = target.bot
        chat_id = target.message.chat.id if target.message else None
    if bot is None or chat_id is None:
        return
    message = await MaintenanceService.get_message(session)
    await render_hub(
        bot,
        chat_id,
        message,
        get_back_button(back_to),
    )


async def _build_connections_screen(
    user: User,
    session: AsyncSession,
    *,
    read_only: bool = False,
) -> tuple[str, InlineKeyboardBuilder]:
    profiles = await get_user_profiles(session, user.id)
    profiles_count = len(profiles)

    device_limit = await _get_effective_device_limit(
        user,
        session,
    )

    rendered = texts.CONNECTION_LIST_HEADER.format(
        count=profiles_count,
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

    if profiles_count == 0:
        rendered += texts.CONNECTION_EMPTY
    else:
        for profile in profiles:
            server = profile.server

            flag = server.country_flag if server else texts.RUNTIME_BOT_HANDLERS_CONNECTION_COMMON_L140_1
            server_name = server.name if server else texts.RUNTIME_BOT_HANDLERS_CONNECTION_COMMON_L141_1

            btn_text = f"{flag} {safe(profile.device_name)}"
            builder.button(
                text=btn_text,
                callback_data=f"manage_device:{profile.id}",
            )

            rendered += f"\n• 📱 <b>{safe(profile.device_name)}</b> ({flag} {safe(server_name)})"
            labels = {
                "pending_create": texts.RUNTIME_BOT_HANDLERS_CONNECTION_COMMON_L171_1, "pending_update": texts.PROVISIONING_UPDATING,
                "deleting": texts.RUNTIME_BOT_HANDLERS_CONNECTION_COMMON_L172_1, "create_failed": texts.PROVISIONING_CREATE_FAILED,
                "create_cleanup_pending": texts.RUNTIME_BOT_HANDLERS_CONNECTION_COMMON_L173_1,
                "update_failed": texts.RUNTIME_BOT_HANDLERS_CONNECTION_COMMON_L174_1, "delete_failed": texts.PROVISIONING_DELETE_FAILED,
            }
            if profile.provisioning_status in labels:
                rendered += texts.RUNTIME_BOT_HANDLERS_CONNECTION_COMMON_L177_1.format(value_0=labels[profile.provisioning_status])

        rendered += "\n\n<i>Нажмите на устройство ниже для управления и получения ключа:</i>"

    if not read_only and profiles_count < device_limit:
        builder.button(
            text=texts.UI_BOT_HANDLERS_CONNECTION_COMMON_L181_1,
            callback_data="add_device",
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

    profiles_count = await get_user_profiles_count(
        session,
        user.id,
    )

    if not has_access:
        if profiles_count > 0:
            rendered, builder = await _build_connections_screen(
                user,
                session,
                read_only=True,
            )

            builder.button(
                text=texts.UI_BOT_HANDLERS_CONNECTION_COMMON_L223_1,
                callback_data="menu_buy",
            )
            builder.button(
                text=texts.UI_BOT_HANDLERS_CONNECTION_COMMON_L227_1,
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
            text=texts.UI_BOT_HANDLERS_CONNECTION_COMMON_L242_1,
            callback_data="menu_buy",
        )
        builder.button(
            text=texts.UI_BOT_HANDLERS_CONNECTION_COMMON_L246_1,
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

    builder.button(
        text=texts.UI_BOT_HANDLERS_CONNECTION_COMMON_L266_1,
        callback_data="back_to_main_menu",
    )
    builder.adjust(1)

    await render_hub(
        target.bot,
        target.chat.id,
        rendered,
        builder.as_markup(),
    )
