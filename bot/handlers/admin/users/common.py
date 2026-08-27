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
from database.repositories.profiles_repo import (
    PROFILE_QUOTA_EXCLUDED_STATUSES,
    get_user_profiles,
)
from database.repositories.tariffs_repo import get_tariff_by_id
from database.repositories.users_repo import (
    get_user_by_telegram_id,
    get_user_referrals_count,
)
from utils.datetime_helpers import is_expired, now_utc
from utils.formatters import format_datetime, format_days_left
from utils.telegram import render_hub
from utils.text_limits import truncate_button_text

logger = logging.getLogger(__name__)


def format_user_card_text(
    user,
    profiles: list,
    referrals,
    now,
    real_balance: int = 0,
    bonus_balance: int = 0,
    tariff_info: str = "—",
    referrer_info: str = "—",
) -> str:
    from datetime import timezone
    from utils.telegram import safe

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    has_access = user.subscription_end and user.subscription_end > now
    referrals_count = len(referrals) if isinstance(referrals, list) else int(referrals or 0)

    status_str = "🟢 Активен" if has_access else "🔴 Неактивен"
    ban_str = "🚫 Забанен" if user.is_banned else "—"

    return texts.ADMIN_USER_CARD.format(
        telegram_id=user.telegram_id,
        username=safe(user.username),
        first_name=safe(user.first_name),
        status=status_str,
        ban=ban_str,
        tariff_info=safe(tariff_info),
        referrer_info=safe(referrer_info),
        real_balance=real_balance,
        bonus_balance=bonus_balance,
        valid_until=format_datetime(user.subscription_end),
        days_left=format_days_left(user.subscription_end),
        devices_count=len(profiles),
        device_limit=user.device_limit or 0,
        referrals_count=referrals_count,
        created_at=format_datetime(user.created_at),
    )

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
    header = format_admin_breadcrumbs(texts.UI_COMMON_POLZOVATELI_164, texts.UI_COMMON_FILTR_164.format(f_name=cur_filter_name))

    rendered = (
        f"{header}"+
        texts.UI_COMMON_UPRAVLENIE_POLZOVATELYAMI_STR__168.format(page=page, total_pages=total_pages, total=total)
    )

    builder = InlineKeyboardBuilder()

    filters = [
        ("all", texts.UI_COMMON_VSE_174, "none"),
        ("new_7d", texts.UI_COMMON_NOVYE_7D_175, "none"),
        ("expiring_3d", texts.UI_COMMON_3_DNEY_176, "none"),
        ("active", texts.UI_COMMON_AKTIVNYE_177, "none"),
        ("expired", texts.UI_COMMON_BEZ_PODPISKI_178, "none"),
        ("banned", texts.UI_COMMON_ZABANENNYE_179, "none"),
    ]

    for f_code, f_name, f_param in filters:
        label = f"• {f_name} •" if f_code == filter_type else f_name
        builder.button(
            text=label,
            callback_data=f"admin_users_filter:{f_code}:{f_param}:1",
        )

    server_label = texts.UI_COMMON_PO_VPN_SERVERAM_ACTIVE_189 if filter_type == "server" else texts.UI_COMMON_PO_VPN_SERVERAM_189
    tariff_label = texts.UI_COMMON_PO_TARIFAM_ACTIVE_190 if filter_type == "tariff" else texts.UI_COMMON_PO_TARIFAM_190

    builder.button(text=server_label, callback_data="admin_users_filter_menu:server")
    builder.button(text=tariff_label, callback_data="admin_users_filter_menu:tariff")

    if not users:
        rendered += texts.UI_COMMON_POLZOVATELI_NE_NAYDENY_196
    else:
        current_time = now_utc()

        for user in users:
            status = (
                "🟢"
                if user.subscription_end and user.subscription_end > current_time
                else "🔴"
            )
            ban = texts.UI_COMMON_BAN_206 if user.is_banned else (texts.UI_COMMON_BLOK_BOTA_206 if user.is_bot_blocked else "")
            username = (
                f"@{user.username}" if user.username else f"ID: {user.telegram_id}"
            )
            days = format_days_left(user.subscription_end)
            profiles_count = (
                len([p for p in user.profiles if getattr(p, "provisioning_status", None) not in PROFILE_QUOTA_EXCLUDED_STATUSES])
                if user.profiles
                else 0
            )

            button_text = truncate_button_text(
                texts.UI_COMMON_USTR_218.format(status=status, ban=ban, username=username, days=days, profiles_count=profiles_count)
            )

            builder.button(
                text=button_text,
                callback_data=f"admin_user_card:{user.telegram_id}",
            )

    nav_buttons = 0
    if page > 1:
        builder.button(
            text=texts.UI_COMMON_NAZAD_229,
            callback_data=f"admin_users_filter:{filter_type}:{filter_param}:{page - 1}",
        )
        nav_buttons += 1

    if page < total_pages:
        builder.button(
            text=texts.UI_COMMON_VPERED_236,
            callback_data=f"admin_users_filter:{filter_type}:{filter_param}:{page + 1}",
        )
        nav_buttons += 1

    builder.button(
        text=texts.UI_COMMON_POISK_PO_USERNAME_ID_242,
        callback_data="admin_users_search",
    )

    builder.button(
        text=texts.UI_COMMON_V_ADMIN_MENYU_247,
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

    tariff_info = texts.UI_COMMON_NE_AKTIVIROVAN_265
    if user.current_tariff_id:
        tariff = await get_tariff_by_id(session, user.current_tariff_id)
        if tariff:
            tariff_info = texts.UI_COMMON_DO_USTR_269.format(tariff_name=tariff.name, tariff_device_limit=tariff.device_limit)
    elif user.device_limit:
        t_name = get_tariff_display_name(user.device_limit)
        tariff_info = texts.UI_COMMON_DO_USTR_272.format(t_name=t_name, user_device_limit=user.device_limit)

    referrer_info = texts.UI_COMMON_PRYAMOY_PEREKHOD_274
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
