import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot import texts
from bot.keyboards.admin.users import get_admin_user_card_keyboard
from database.models import Server, Tariff, User
from database.repositories import white_internet_repo
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
from utils.formatters import format_datetime, format_traffic
from bot.formatters import format_days_left
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
    white_internet_info: str | None = None,
) -> str:
    from datetime import timezone
    from utils.telegram import safe

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    has_access = user.subscription_end and user.subscription_end > now
    referrals_count = len(referrals) if isinstance(referrals, list) else int(referrals or 0)

    status_str = texts.STATUS_ACTIVE_BADGE if has_access else texts.STATUS_INACTIVE_BADGE
    ban_str = texts.STATUS_BANNED_BADGE if user.is_banned else texts.STATUS_NOT_BANNED_BADGE

    card_text = texts.ADMIN_USER_CARD.format(
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
    if white_internet_info:
        card_text = f"{card_text}\n\n{white_internet_info}"
    return card_text

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
        return texts.PLACEHOLDER_DASH

    from utils.datetime_helpers import is_permanent_subscription
    if is_permanent_subscription(subscription_end):
        return texts.ADMIN_SUB_PERMANENT_LABEL

    current_time = now_utc()
    delta = subscription_end - current_time

    if delta.total_seconds() <= 0:
        return texts.STATUS_EXPIRED_LABEL

    days = delta.days
    hours = delta.seconds // 3600

    if days >= 36500:
        return texts.ADMIN_SUB_PERMANENT_LABEL

    if days > 0:
        return texts.TIME_DAYS_HOURS_FORMAT.format(days=days, hours=hours)

    minutes = (delta.seconds % 3600) // 60

    return texts.TIME_HOURS_MINUTES_FORMAT.format(hours=hours, minutes=minutes)


async def _get_active_tariffs(session: AsyncSession) -> list[Tariff]:
    result = await session.execute(
        select(Tariff)
        .where(Tariff.is_active.is_(True), Tariff.service_type == "awg")
        .order_by(Tariff.device_limit)
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
    users: list[User],
    page: int,
    total_pages: int,
    total: int,
    filter_type: str = "all",
    filter_param: str = "none",
    session: AsyncSession | None = None,
) -> tuple[str, InlineKeyboardBuilder]:
    from bot.formatters import format_admin_breadcrumbs

    raw_label = texts.ADMIN_USER_FILTER_LABELS.get(filter_type, filter_type)
    if "{filter_param}" in raw_label:
        cur_filter_name = raw_label.format(filter_param=filter_param)
    else:
        cur_filter_name = raw_label

    if filter_type == "tariff" and filter_param != "none" and str(filter_param).isdigit():
        from bot.formatters import get_tariff_group_name
        cur_filter_name = get_tariff_group_name(int(filter_param))
    header = format_admin_breadcrumbs(texts.BTN_USERS, texts.COMMON_FILTR.format(f_name=cur_filter_name))

    rendered = (
        f"{header}"+
        texts.COMMON_MANAGE_POLZOVATELYAMI_STR.format(page=page, total_pages=total_pages, total=total)
    )

    builder = InlineKeyboardBuilder()

    filter_counts: dict[str, int] = {}
    if session is not None:
        from database.repositories.users_repo import get_user_filter_counts
        try:
            filter_counts = await get_user_filter_counts(session)
        except Exception as e:
            logger.warning("Failed to get user filter counts: %s", e)

    def _cnt_label(fmt_str: str, f_name: str, key: str) -> str:
        cnt = filter_counts.get(key)
        if cnt is None:
            return f_name
        return fmt_str.format(f_name=f_name, count=cnt)

    filters = [
        ("all", _cnt_label(texts.ADMIN_USER_FILTER_ALL_COUNT, texts.COMMON_VSE, "all"), "none"),
        ("new_7d", _cnt_label(texts.ADMIN_USER_FILTER_NEW_7D_COUNT, texts.COMMON_NOVYE_7D, "new_7d"), "none"),
        ("active", _cnt_label(texts.ADMIN_USER_FILTER_ACTIVE_COUNT, texts.COMMON_AKTIVNYE, "active"), "none"),
        ("expiring_3d", _cnt_label(texts.ADMIN_USER_FILTER_EXPIRING_COUNT, texts.COMMON_3_DAYS, "expiring_3d"), "none"),
        ("expired", _cnt_label(texts.ADMIN_USER_FILTER_EXPIRED_COUNT, texts.COMMON_BEZ_SUBSCRIPTION, "expired"), "none"),
        ("banned", _cnt_label(texts.ADMIN_USER_FILTER_BANNED_COUNT, texts.COMMON_ZABANENNYE, "banned"), "none"),
    ]

    for f_code, f_name, f_param in filters:
        label = f"• {f_name} •" if f_code == filter_type else f_name
        builder.button(
            text=label,
            callback_data=f"admin_users_filter:{f_code}:{f_param}:1",
        )

    server_label = texts.BTN_FILTER_BY_SERVER_ACTIVE if filter_type == "server" else texts.BTN_FILTER_BY_SERVER
    tariff_label = texts.BTN_FILTER_BY_TARIFF_ACTIVE if filter_type == "tariff" else texts.BTN_FILTER_BY_TARIFF

    builder.button(text=server_label, callback_data="admin_users_filter_menu:server")
    builder.button(text=tariff_label, callback_data="admin_users_filter_menu:tariff")

    has_reset = False
    if filter_type not in ("all", "new_7d", "active", "expiring_3d", "expired", "banned"):
        builder.button(text=texts.ADMIN_SERVER_BTN_RESET_FILTER, callback_data="admin_users_filter:all:none:1")
        has_reset = True

    if not users:
        rendered += texts.ADMIN_USERS_LIST_EMPTY_NOTICE
    else:
        current_time = now_utc()

        for user in users:
            status = (
                "🟢"
                if user.subscription_end and user.subscription_end > current_time
                else "🔴"
            )
            ban = texts.COMMON_BAN if user.is_banned else (texts.COMMON_BLOK_BOTA if user.is_bot_blocked else "")
            username = (
                f"@{user.username}" if user.username else texts.ADMIN_USER_ID_FORMAT.format(telegram_id=user.telegram_id)
            )
            days = format_days_left(user.subscription_end)
            profiles_count = (
                len([p for p in user.profiles if getattr(p, "provisioning_status", None) not in PROFILE_QUOTA_EXCLUDED_STATUSES])
                if user.profiles
                else 0
            )

            button_text = truncate_button_text(
                texts.COMMON_USTR.format(status=status, ban=ban, username=username, days=days, profiles_count=profiles_count)
            )

            builder.button(
                text=button_text,
                callback_data=f"admin_user_card:{user.telegram_id}:users:{filter_type}:{filter_param}:{page}",
            )

    nav_buttons = 0
    if page > 1:
        builder.button(
            text=texts.BTN_BACK,
            callback_data=f"admin_users_filter:{filter_type}:{filter_param}:{page - 1}",
        )
        nav_buttons += 1

    if page < total_pages:
        builder.button(
            text=texts.BTN_PAGINATION_NEXT,
            callback_data=f"admin_users_filter:{filter_type}:{filter_param}:{page + 1}",
        )
        nav_buttons += 1

    builder.button(
        text=texts.COMMON_SEARCH_PO_USERNAME_ID,
        callback_data="admin_users_search",
    )

    builder.button(
        text=texts.BTN_ADMIN_MENU,
        callback_data="admin_menu",
    )

    item_count = len(users) if users else 0
    filter_pattern = [2, 2, 2, 2] if not has_reset else [2, 2, 2, 2, 1]
    adjust_pattern = filter_pattern + ([1] * item_count)
    if nav_buttons > 0:
        adjust_pattern.append(nav_buttons)
    adjust_pattern.extend([1, 1])

    builder.adjust(*adjust_pattern)

    return rendered, builder


async def _get_user_card_details(session: AsyncSession, user: User) -> tuple[str, str]:
    from bot.formatters import get_tariff_display_name

    tariff_info = texts.COMMON_NE_AKTIVIROVAN
    if user.current_tariff_id:
        tariff = await get_tariff_by_id(session, user.current_tariff_id)
        if tariff:
            tariff_info = texts.COMMON_DO_USTR.format(tariff_name=tariff.name, tariff_device_limit=tariff.device_limit)
    elif user.device_limit:
        t_name = get_tariff_display_name(user.device_limit)
        tariff_info = texts.ADMIN_DEVICE_LIMIT_SLOT_LABEL.format(t_name=t_name, user_device_limit=user.device_limit)

    referrer_info = texts.COMMON_PRYAMOY_PEREKHOD
    if user.referred_by:
        referrer = await get_user_by_telegram_id(session, user.referred_by)
        if referrer:
            r_name = referrer.first_name if referrer.first_name else ""
            r_username = f" (@{referrer.username})" if referrer.username else ""
            referrer_info = f"{r_name}{r_username} (ID: {referrer.telegram_id})"
        else:
            referrer_info = f"ID: {user.referred_by}"

    return tariff_info, referrer_info


async def _get_white_internet_card_info(session: AsyncSession, user_id: int) -> str | None:
    sub = await white_internet_repo.get_subscription_by_user_id(session, user_id)
    if not sub:
        return None

    origin_name = "—"
    if sub.origin_node_id:
        origin_server = await session.get(Server, sub.origin_node_id)
        if origin_server:
            origin_name = origin_server.name
        else:
            origin_name = f"#{sub.origin_node_id}"

    used_str = format_traffic(sub.traffic_used_bytes or 0)
    total_str = format_traffic(sub.traffic_limit_bytes)
    expires_str = format_datetime(sub.expires_at) if sub.expires_at else "—"

    status_badge_map = {
        "ACTIVE": texts.ADMIN_USER_CARD_WL_BADGE_ACTIVE,
        "PENDING": texts.ADMIN_USER_CARD_WL_BADGE_PENDING,
        "EXHAUSTED": texts.ADMIN_USER_CARD_WL_BADGE_EXHAUSTED,
        "EXPIRED": texts.ADMIN_USER_CARD_WL_BADGE_EXPIRED,
        "DISABLED": texts.ADMIN_USER_CARD_WL_BADGE_DISABLED,
    }
    status_badge = status_badge_map.get(sub.status, sub.status)

    return texts.ADMIN_USER_CARD_WHITE_INTERNET_BLOCK.format(
        status_badge=status_badge,
        used_str=used_str,
        total_str=total_str,
        expires_str=expires_str,
        origin_name=origin_name,
    )


async def _render_user_card(
    callback: CallbackQuery,
    user: User,
    session: AsyncSession,
    back_callback: str = "admin_users",
):
    from database.repositories.account_ledger_repo import get_account_balance
    profiles = await get_user_profiles(session, user.id)

    referrals_count = await get_user_referrals_count(
        session,
        user.telegram_id,
    )
    balance = await get_account_balance(session, user_id=user.id)
    tariff_info, referrer_info = await _get_user_card_details(session, user)
    wl_info = await _get_white_internet_card_info(session, user.id)

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
        white_internet_info=wl_info,
    )

    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=get_admin_user_card_keyboard(
                user.telegram_id,
                user.is_banned,
                back_callback=back_callback,
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
    wl_info = await _get_white_internet_card_info(session, user.id)

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
        white_internet_info=wl_info,
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
