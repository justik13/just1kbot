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

    return texts.RUNTIME_BOT_HANDLERS_CONNECTION_COMMON_L84_1.format(value_0=hours)


async def _render_maintenance(
    message: CallbackQuery,
    session: AsyncSession,
    *,
    back_to: str,
):
    maintenance = await MaintenanceService.get_current(session)
    if maintenance and maintenance.is_active:
        builder = InlineKeyboardBuilder()
        builder.button(
            text=texts.UI_BOT_HANDLERS_CONNECTION_COMMON_L95_1,
            callback_data=back_to,
        )
        builder.adjust(1)
        await render_hub(
            message.bot,
            message.chat.id,
            texts.MAINTENANCE_MESSAGE.format(
                reason=safe(maintenance.reason or texts.MAINTENANCE_DEFAULT_REASON)
            ),
            builder.as_markup(),
            trigger_message_id=message.message_id,
        )


async def _render_connections(
    message,
    user: User,
    session: AsyncSession,
):
    profiles = await get_user_profiles(session, user.id)
    if not profiles:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.CONNECTIONS_EMPTY,
            get_back_button("back_to_main_menu"),
            trigger_message_id=message.message_id,
        )
        return

    builder = InlineKeyboardBuilder()
    for profile in profiles:
        builder.button(
            text=texts.UI_BOT_HANDLERS_CONNECTION_COMMON_L122_1.format(value_0=profile.device_name),
            callback_data=f"manage_device:{profile.id}",
        )

    builder.button(
        text=texts.UI_BOT_HANDLERS_CONNECTION_COMMON_L127_1,
        callback_data="add_device",
    )
    builder.button(
        text=texts.UI_BOT_HANDLERS_CONNECTION_COMMON_L131_1,
        callback_data="back_to_main_menu",
    )
    builder.adjust(1)

    await render_hub(
        message.bot,
        message.chat.id,
        texts.CONNECTIONS_HEADER,
        builder.as_markup(),
        trigger_message_id=message.message_id,
    )
