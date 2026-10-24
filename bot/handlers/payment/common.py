import logging

from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from database.repositories.profiles_repo import (
    get_user_profiles,
    get_user_profiles_count,
)
from database.repositories.tariffs_repo import (
    get_active_tariffs,
)
from utils.datetime_helpers import is_expired
from bot.formatters import (
    format_days_left,
    format_subscription_date,
    get_tariff_display_name,
    get_tariff_group_name,
)
from bot.keyboards import make_rich_nav_row
from utils.telegram import (
    make_rich_button,
    make_rich_button_row,
    render_hub,
)

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
        from database.repositories import vless_subscription_repo
        profiles_count = await get_user_profiles_count(session, db_user.id)
        vless_count = await vless_subscription_repo.get_active_hwid_count(session, db_user.id)
        total_devices = profiles_count + vless_count
        if total_devices > new_limit:
            return texts.PAYMENT_DOWNGRADE_BLOCKED_PROFILES.format(
                profiles_count=total_devices,
                new_limit=new_limit,
            )
    return None


async def render_tariff_showcase(
    bot, chat_id: int, session: AsyncSession
) -> None:
    tariffs = await get_active_tariffs(session)
    if not tariffs:
        await render_hub(
            bot,
            chat_id,
            texts.PAYMENT_NO_TARIFFS,
            get_back_button("back_to_main_menu"),
            session=session,
        )
        return
    grouped: dict[int, list] = {}
    for tariff in tariffs:
        limit = getattr(tariff, "device_limit", 2)
        if limit not in grouped:
            grouped[limit] = []
        grouped[limit].append(tariff)
    rows = []
    for limit, group_tariffs in sorted(grouped.items()):
        name = get_tariff_display_name(limit)
        min_price = min(int(t.price_rub) for t in group_tariffs)
        rows.append(
            texts.PAYMENT_SHOWCASE_TABLE_ROW_TEMPLATE.format(
                name=name, limit=limit, min_price=min_price
            )
        )
    table = texts.PAYMENT_SHOWCASE_TABLE_TEMPLATE.format(rows="".join(rows))
    text = f"{texts.PAYMENT_SHOWCASE_HEADER}\n{table}"

    tariff_buttons = []
    for limit in sorted(grouped.keys()):
        group_name = get_tariff_group_name(limit)
        group_tariffs = grouped.get(limit) or []
        if group_tariffs:
            min_price = min(int(t.price_rub) for t in group_tariffs)
            btn_text = texts.BTN_TARIFF_SHOWCASE_ITEM.format(group_name=group_name, min_price=min_price)
        else:
            btn_text = group_name
        tariff_buttons.append(
            make_rich_button_row(
                make_rich_button(
                    btn_text,
                    callback_data=f"select_tariff_type:{limit}:showcase",
                    style="primary",
                )
            )
        )
    nav_row = make_rich_nav_row(include_menu=True)
    full_text = f"{text}\n" + "\n".join(tariff_buttons) + f"\n{nav_row}"
    await render_hub(
        bot,
        chat_id,
        full_text,
        reply_markup=None,
        session=session,
    )


async def _show_showcase(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    await render_tariff_showcase(callback.bot, callback.message.chat.id, session)


async def _show_hub(
    callback: CallbackQuery, user, session: AsyncSession
) -> None:
    from database.repositories import vless_subscription_repo
    from database.repositories.profiles_repo import PROFILE_QUOTA_EXCLUDED_STATUSES

    profiles = await get_user_profiles(session, user.id)
    quota_profiles = len([
        p for p in profiles
        if getattr(p, "provisioning_status", "") not in PROFILE_QUOTA_EXCLUDED_STATUSES
    ])
    vless_hwid_count = await vless_subscription_repo.get_active_hwid_count(session, user.id)
    total_devices = quota_profiles + vless_hwid_count
    device_limit = await _get_effective_device_limit(session, user)
    tariff_name = get_tariff_display_name(device_limit)
    text = texts.PAYMENT_HUB_HEADER.format(
        valid_until=format_subscription_date(user.subscription_end),
        days_left=format_days_left(user.subscription_end),
        tariff_name=tariff_name,
        devices_count=total_devices,
        device_limit=device_limit,
    )
    nav_row = make_rich_nav_row(include_menu=True)
    full_text = f"{text}\n{nav_row}"

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        full_text,
        reply_markup=None,
        session=session,
    )
