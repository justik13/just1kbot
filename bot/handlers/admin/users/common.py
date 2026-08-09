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
from database.repositories.users_repo import get_user_referrals
from utils.datetime_helpers import is_expired, now_utc
from utils.formatters import format_days_left, format_user_card_text
from utils.telegram import render_hub
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
) -> tuple[str, InlineKeyboardBuilder]:
    from utils.formatters import format_admin_breadcrumbs
    filter_names = {
        "all": "Все",
        "new": "🆕 Новенькие (<7д)",
        "active": "⚡ С подпиской",
        "expired": "⏳ Без подписки",
        "problem": "🚫 Проблемные",
    }
    cur_filter_name = filter_names.get(filter_type, "Все")
    header = format_admin_breadcrumbs("👥 Пользователи", f"Фильтр: {cur_filter_name}")

    rendered = (
        f"{header}"
        f"👥 <b>Управление пользователями</b> (Стр. {page}/{total_pages}, всего: {total})\n\n"
    )

    builder = InlineKeyboardBuilder()

    # Фильтры
    filters = [
        ("all", "Все"),
        ("new", "🆕 Новые"),
        ("active", "⚡ Активные"),
        ("expired", "⏳ Истекшие"),
        ("problem", "🚫 Баны"),
    ]

    for f_code, f_name in filters:
        label = f"• {f_name} •" if f_code == filter_type else f_name
        builder.button(
            text=label,
            callback_data=f"admin_users_filter:{f_code}:1",
        )
    builder.adjust(3, 2)

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

    nav_buttons = []
    if page > 1:
        builder.button(
            text="◀️ Назад",
            callback_data=f"admin_users_filter:{filter_type}:{page - 1}",
        )
        nav_buttons.append(1)

    if page < total_pages:
        builder.button(
            text="Вперед ▶️",
            callback_data=f"admin_users_filter:{filter_type}:{page + 1}",
        )
        nav_buttons.append(1)

    builder.button(
        text="🔍 Поиск по @username / ID",
        callback_data="admin_users_search",
    )

    builder.button(
        text="🔙 В админ-меню",
        callback_data="admin_menu",
    )

    # Применение макета кнопок
    item_count = len(users)
    nav_row = len(nav_buttons)
    if nav_row > 0:
        builder.adjust(3, 2, *([1] * item_count), nav_row, 1, 1)
    else:
        builder.adjust(3, 2, *([1] * item_count), 1, 1)

    return rendered, builder



async def _render_user_card(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
):
    from database.repositories.account_ledger_repo import get_account_balance
    profiles = await get_user_profiles(session, user.id)

    referrals = await get_user_referrals(
        session,
        user.telegram_id,
    )
    balance = await get_account_balance(session, user_id=user.id)

    current_time = now_utc()

    rendered = format_user_card_text(
        user,
        profiles,
        referrals,
        current_time,
        real_balance=int(balance.real_available),
        bonus_balance=int(balance.bonus_available),
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

    referrals = await get_user_referrals(
        session,
        user.telegram_id,
    )
    balance = await get_account_balance(session, user_id=user.id)

    current_time = now_utc()

    rendered = format_user_card_text(
        user,
        profiles,
        referrals,
        current_time,
        real_balance=int(balance.real_available),
        bonus_balance=int(balance.bonus_available),
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

