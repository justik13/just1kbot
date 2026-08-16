import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot import texts
from bot.keyboards.admin.users import get_admin_user_card_keyboard
from database.models import Tariff, User
from database.repositories.profiles_repo import get_user_profiles
from database.repositories.tariffs_repo import get_tariff_by_id
from database.repositories.users_repo import (
    get_user_by_telegram_id,
    get_user_referrals_count,
)
from utils.datetime_helpers import is_expired, now_utc
from utils.formatters import format_days_left, format_user_card_text
from utils.telegram import render_hub, safe
from utils.text_limits import truncate_button_text

logger = logging.getLogger(__name__)

USERS_PER_PAGE = 10

MANUAL_GRANT_ALLOWED_STATUSES = {
    "pending",
    "cancelled",
    "failed",
    "requires_manual_review",
}


def _validate_positive_int(text: str | None) -> int | None:
    if not text or not text.strip().isdigit():
        return None

    value = int(text.strip())

    MAX_DAYS = 36500

    if value < 1 or value > MAX_DAYS:
        return None

    return value


def _is_subscription_active(user: User) -> bool:
    if not user.subscription_end:
        return False

    return not is_expired(user.subscription_end)


def _format_time_left(subscription_end) -> str:
    if not subscription_end:
        return texts.RUNTIME_BOT_HANDLERS_ADMIN_USERS_COMMON_L55_1

    from utils.datetime_helpers import is_permanent_subscription
    if is_permanent_subscription(subscription_end):
        return texts.ADMIN_SUB_PERMANENT_LABEL

    current_time = now_utc()
    delta = subscription_end - current_time

    if delta.total_seconds() <= 0:
        return texts.RUNTIME_BOT_HANDLERS_ADMIN_USERS_COMMON_L64_1

    days = delta.days
    hours = delta.seconds // 3600

    if days >= 36500:
        return texts.ADMIN_SUB_PERMANENT_LABEL

    if days > 0:
        return texts.RUNTIME_BOT_HANDLERS_ADMIN_USERS_COMMON_L73_1.format(value_0=days, value_1=hours)

    minutes = (delta.seconds % 3600) // 60

    return texts.RUNTIME_BOT_HANDLERS_ADMIN_USERS_COMMON_L77_1.format(value_0=hours, value_1=minutes)


async def _get_active_tariffs(session: AsyncSession) -> list[Tariff]:
    result = await session.execute(
        select(Tariff).where(Tariff.is_active.is_(True)).order_by(Tariff.device_limit)
    )

    return list(result.scalars().all())


async def _get_tariff_groups(
    session: AsyncSession,
) -> dict[int, list[Tariff]]:
    tariffs = await _get_active_tariffs(session)

    groups: dict[int, list[Tariff]] = {}

    for tariff in tariffs:
        limit = tariff.device_limit

        if limit not in groups:
            groups[limit] = []

        groups[limit].append(tariff)

    return groups


def _get_representative_tariff(tariffs: list[Tariff]) -> Tariff:
    return min(
        tariffs,
        key=lambda t: t.duration_days,
    )


async def _get_user_with_profiles(
    session: AsyncSession,
    telegram_id: int,
):
    stmt = (
        select(User)
        .where(User.telegram_id == telegram_id)
        .options(selectinload(User.profiles))
    )

    result = await session.execute(stmt)

    return result.scalar_one_or_none()


async def _build_users_list_text_and_kb(
    users,
    page: int,
    total_pages: int,
    total: int,
    filter_type: str = "all",
    filter_param: str = "none",
) -> tuple[str, InlineKeyboardBuilder]:
    from utils.formatters import format_admin_breadcrumbs

    filter_labels = {
        "all": "Все",
        "new": "🆕 Новые (7д)",
        "new_24h": "🆕 Новые (7д)",
        "new_7d": "🆕 Новые (7д)",
        "expiring_3d": "⏳ < 3 дней",
        "active": "⚡ С подпиской",
        "expired": "🔴 Без подписки",
        "no_sub": "🔴 Без подписки",
        "banned": "🚫 Забаненные",
        "problem": "🚫 Забаненные",
        "server": f"Сервер #{filter_param}",
        "tariff": f"Тариф #{filter_param}",
    }
    cur_filter_name = filter_labels.get(filter_type, filter_type)
    if filter_type == "tariff" and filter_param != "none" and str(filter_param).isdigit():
        from utils.tariff_names import get_tariff_group_name
        cur_filter_name = get_tariff_group_name(int(filter_param))
    header = format_admin_breadcrumbs("👥 Пользователи", f"Фильтр: {cur_filter_name}")

    rendered = (
        f"{header}"
        f"👥 <b>Управление пользователями</b> (Стр. {page}/{total_pages}, всего: {total})\n\n"
    )

    builder = InlineKeyboardBuilder()

    filters = [
        ("all", "Все", "none"),
        ("new_7d", "🆕 Новые (7д)", "none"),
        ("expiring_3d", "⏳ < 3 дней", "none"),
        ("active", "⚡ Активные", "none"),
        ("expired", "🔴 Без подписки", "none"),
        ("banned", "🚫 Забаненные", "none"),
    ]

    for f_code, f_name, f_param in filters:
        label = f"• {f_name} •" if f_code == filter_type else f_name
        builder.button(
            text=label,
            callback_data=f"admin_users_filter:{f_code}:{f_param}:1",
        )

    server_label = "• 🖥 По VPN серверам •" if filter_type == "server" else "🖥 По VPN серверам"
    tariff_label = "• 💎 По тарифам •" if filter_type == "tariff" else "💎 По тарифам"

    builder.button(text=server_label, callback_data="admin_users_filter_menu:server")
    builder.button(text=tariff_label, callback_data="admin_users_filter_menu:tariff")

    if not users:
        rendered += "<i>Пользователи не найдены.</i>"
    else:
        current_time = now_utc()

        for user in users:
            status = (
                "🟢"
                if user.subscription_end and user.subscription_end > current_time
                else "🔴"
            )
            ban = " [БАН]" if user.is_banned else (" [Блок бота]" if user.is_bot_blocked else "")
            username = (
                f"@{user.username}" if user.username else f"ID: {user.telegram_id}"
            )
            days = format_days_left(user.subscription_end)
            profiles_count = len([p for p in user.profiles if getattr(p, "provisioning_status", None) not in ("deleting", "create_cleanup_pending")]) if user.profiles else 0

            button_text = truncate_button_text(
                f"{status}{ban} {username} | {days} | {profiles_count} устр."
            )

            builder.button(
                text=button_text,
                callback_data=f"admin_user_card:{user.telegram_id}",
            )

    nav_buttons = 0
    if page > 1:
        builder.button(
            text="◀️ Назад",
            callback_data=f"admin_users_filter:{filter_type}:{filter_param}:{page - 1}",
        )
        nav_buttons += 1

    if page < total_pages:
        builder.button(
            text="Вперед ▶️",
            callback_data=f"admin_users_filter:{filter_type}:{filter_param}:{page + 1}",
        )
        nav_buttons += 1

    builder.button(
        text="🔍 Поиск по @username / ID",
        callback_data="admin_users_search",
    )

    builder.button(
        text="🔙 В админ-меню",
        callback_data="admin_menu",
    )

    item_count = len(users) if users else 0
    adjust_pattern = [3, 3, 2] + ([1] * item_count)
    if nav_buttons > 0:
        adjust_pattern.append(nav_buttons)
    adjust_pattern.extend([1, 1])

    builder.adjust(*adjust_pattern)

    return rendered, builder


async def _get_user_card_details(session: AsyncSession, user: User) -> tuple[str, str]:
    from utils.tariff_names import get_tariff_display_name

    tariff_info = "Не активирован"
    if user.current_tariff_id:
        tariff = await get_tariff_by_id(session, user.current_tariff_id)
        if tariff:
            tariff_info = f"{tariff.name} (до {tariff.device_limit} устр.)"
    elif user.device_limit:
        t_name = get_tariff_display_name(user.device_limit)
        tariff_info = f"{t_name} (до {user.device_limit} устр.)"

    referrer_info = "Прямой переход"
    if user.referred_by:
        referrer = await get_user_by_telegram_id(session, user.referred_by)
        if referrer:
            r_name = referrer.first_name if referrer.first_name else ""
            r_username = f" (@{referrer.username})" if referrer.username else ""
            referrer_info = f"{r_name}{r_username} (ID: {referrer.telegram_id})"
        else:
            referrer_info = f"ID: {user.referred_by}"

    return tariff_info, referrer_info


async def _render_user_card(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
):
    from database.repositories.account_ledger_repo import get_account_balance
    profiles = await get_user_profiles(session, user.id)

    referrals_count = await get_user_referrals_count(
        session,
        user.telegram_id,
    )
    balance = await get_account_balance(session, user_id=user.id)
    tariff_info, referrer_info = await _get_user_card_details(session, user)

    current_time = now_utc()

    rendered = format_user_card_text(
        user,
        profiles,
        referrals_count,
        current_time,
        real_balance=int(balance.real_available),
        bonus_balance=int(balance.bonus_available),
        tariff_info=tariff_info,
        referrer_info=referrer_info,
    )

    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=get_admin_user_card_keyboard(
                user.telegram_id,
                user.is_banned,
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"_render_user_card edit_text failed: {e}")


async def _show_user_card_edit(
    message,
    user,
    session: AsyncSession,
    notice: str | None = None,
):
    from database.repositories.account_ledger_repo import get_account_balance
    profiles = await get_user_profiles(session, user.id)

    referrals_count = await get_user_referrals_count(
        session,
        user.telegram_id,
    )
    balance = await get_account_balance(session, user_id=user.id)
    tariff_info, referrer_info = await _get_user_card_details(session, user)

    current_time = now_utc()

    rendered = format_user_card_text(
        user,
        profiles,
        referrals_count,
        current_time,
        real_balance=int(balance.real_available),
        bonus_balance=int(balance.bonus_available),
        tariff_info=tariff_info,
        referrer_info=referrer_info,
    )

    if notice:
        rendered = f"{notice}\n\n{rendered}"

    trigger_message_id = getattr(message, "message_id", None)

    await render_hub(
        message.bot,
        message.chat.id,
        rendered,
        get_admin_user_card_keyboard(
            user.telegram_id,
            user.is_banned,
        ),
        trigger_message_id=trigger_message_id,
    )
