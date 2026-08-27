import logging

from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button, get_tariff_showcase_keyboard
from database.repositories.profiles_repo import (
    get_user_profiles,
    get_user_profiles_count,
)
from database.repositories.tariffs_repo import (
    get_active_tariffs,
)
from utils.datetime_helpers import is_expired
from utils.formatters import format_datetime, format_days_left
from utils.tariff_names import get_tariff_display_name
from utils.telegram import render_hub

logger = logging.getLogger(__name__)


async def _is_subscription_active(user) -> bool:
    if not user or not user.subscription_end:
        return False
    return not is_expired(user.subscription_end)


# Shared helpers live in bot.handlers.shared_common; re-exported under the
# historical private names so existing relative imports keep working.
from bot.handlers.shared_common import (  # noqa: E402
    get_effective_device_limit as _get_effective_device_limit,
    render_maintenance as _render_maintenance_payment,
)


async def _render_maintenance(
    target,
    session: AsyncSession,
    *,
    back_to: str = "back_to_main_menu",
) -> None:
    await _render_maintenance_payment(target, session, back_to=back_to)


async def _check_tariff_change_allowed(
    session: AsyncSession,
    db_user,
    tariff,
) -> str | None:
    new_limit = getattr(tariff, "device_limit", 2)
    is_active = await _is_subscription_active(db_user)
    if is_active:
        current_tariff_id = getattr(db_user, "current_tariff_id", None)
        if current_tariff_id is None:
            return texts.PAYMENT_COMMON
        current_limit = await _get_effective_device_limit(session, db_user)
        if new_limit != current_limit:
            return texts.PAYMENT_CHANGE_TARIFF_TEMPORARILY_UNAVAILABLE
        profiles_count = await get_user_profiles_count(session, db_user.id)
        if profiles_count > new_limit:
            return texts.PAYMENT_DOWNGRADE_BLOCKED_PROFILES.format(
                profiles_count=profiles_count,
                new_limit=new_limit,
            )
    return None


async def _show_showcase(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    tariffs = await get_active_tariffs(session)
    if not tariffs:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.PAYMENT_NO_TARIFFS,
            get_back_button("back_to_main_menu"),
        )
        return
    grouped: dict[int, list] = {}
    for tariff in tariffs:
        limit = getattr(tariff, "device_limit", 2)
        if limit not in grouped:
            grouped[limit] = []
        grouped[limit].append(tariff)
    keyboard = get_tariff_showcase_keyboard(grouped)
    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.PAYMENT_SHOWCASE_HEADER,
        keyboard,
    )


async def _show_hub(
    callback: CallbackQuery, user, session: AsyncSession
) -> None:
    profiles = await get_user_profiles(session, user.id)
    device_limit = await _get_effective_device_limit(session, user)
    tariff_name = get_tariff_display_name(device_limit)
    text = texts.PAYMENT_HUB_HEADER.format(
        valid_until=format_datetime(user.subscription_end),
        days_left=format_days_left(user.subscription_end),
        tariff_name=tariff_name,
        devices_count=len(profiles),
        device_limit=device_limit,
    )

    tariffs = await get_active_tariffs(session)
    if tariffs:
        grouped: dict[int, list] = {}
        for t in tariffs:
            limit = getattr(t, "device_limit", 2)
            if limit not in grouped:
                grouped[limit] = []
            grouped[limit].append(t)

        tariff_lines = []
        for limit in sorted(grouped.keys()):
            min_price = min(int(t.price_rub) for t in grouped[limit])
            name = get_tariff_display_name(limit)
            tariff_lines.append(texts.PAYMENT_STATUS_COMMON_UST_OT.format(name=name, limit=limit, min_price=min_price))

        if tariff_lines:
            text += texts.PAYMENT_STATUS_COMMON_DOSTUPNYE_VARIANTY_TARIFOV + "\n".join(tariff_lines)

    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_PAYMENT_RENEW_SUBSCRIPTION, callback_data="payment_quick_renew"
    )
    builder.button(
        text=texts.BTN_PAYMENT_CHANGE_TARIFF, callback_data="payment_change_tariff"
    )
    builder.button(
        text=texts.BTN_PAYMENT_TO_MAIN_MENU, callback_data="back_to_main_menu"
    )
    builder.adjust(1, 1, 1)
    await render_hub(
        callback.bot, callback.message.chat.id, text, builder.as_markup()
    )
