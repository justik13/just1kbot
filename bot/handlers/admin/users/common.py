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
) -> tuple[str, InlineKeyboardBuilder]:
    rendered = texts.ADMIN_USERS_HEADER.format(
        page=page,
        total_pages=total_pages,
        total=total,
    )

    builder = InlineKeyboardBuilder()

    if not users:
        rendered += texts.ADMIN_USERS_EMPTY
    else:
        current_time = now_utc()

        for user in users:
            status = (
                texts.RUNTIME_BOT_HANDLERS_ADMIN_USERS_COMMON_L149_1
                if user.subscription_end and user.subscription_end > current_time
                else texts.RUNTIME_BOT_HANDLERS_ADMIN_USERS_COMMON_L151_1
            )

            ban = texts.RUNTIME_BOT_HANDLERS_ADMIN_USERS_COMMON_L154_1 if user.is_banned else ""

            username = (
                f"@{user.username}" if user.username else texts.ADMIN_PAYMENT_USER_ID_COMPACT.format(user_id=user.telegram_id)
            )

            days = format_days_left(user.subscription_end)

            profiles_count = len(user.profiles) if user.profiles else 0

            button_text = truncate_button_text(
                texts.RUNTIME_BOT_HANDLERS_ADMIN_USERS_COMMON_L165_1.format(value_0=status, value_1=ban, value_2=username, value_3=days, value_4=profiles_count)
            )

            builder.button(
                text=button_text,
                callback_data=f"admin_user_card:{user.telegram_id}",
            )

    if page > 1:
        builder.button(
            text=texts.UI_BOT_HANDLERS_ADMIN_USERS_COMMON_L175_1,
            callback_data=f"admin_users_page:{page - 1}",
        )

    if page < total_pages:
        builder.button(
            text=texts.UI_BOT_HANDLERS_ADMIN_USERS_COMMON_L181_1,
            callback_data=f"admin_users_page:{page + 1}",
        )

    builder.button(
        text=texts.UI_BOT_HANDLERS_ADMIN_USERS_COMMON_L186_1,
        callback_data="admin_users_search",
    )

    builder.button(
        text=texts.UI_BOT_HANDLERS_ADMIN_USERS_COMMON_L191_1,
        callback_data="admin_menu",
    )

    builder.adjust(1)

    return rendered, builder


async def _render_user_card(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
):
    from database.repositories.account_ledger_repo import get_account_balance
    profiles = user.profiles if user.profiles else []

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

