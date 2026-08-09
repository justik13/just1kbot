from datetime import timedelta
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import User
from utils.datetime_helpers import now_utc

ALLOWED_USER_UPDATE_FIELDS = {
    "username",
    "first_name",
    "subscription_end",
    "device_limit",
    "current_tariff_id",
    "is_banned",
    "is_bot_blocked",
    "referred_by",
    "referral_days",
    "device_creations_today",
    "last_creation_date",
    "last_payment_at",
    "notified_3d",
    "notified_1d",
    "notified_2h",
    "notified_expired",
    "notified_grace_12h",
    "notification_retry_count",
    "last_notification_attempt",
}


async def get_user_by_telegram_id(
    session: AsyncSession, telegram_id: int
) -> Optional[User]:
    stmt = select(User).where(
        User.telegram_id == telegram_id,
        User.is_deleted.is_(False),
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_user_by_telegram_id_any(
    session: AsyncSession, telegram_id: int
) -> Optional[User]:
    """
    Ищет пользователя включая soft-deleted.
    Используется для безопасного восстановления и предотвращения unique constraint.
    """
    stmt = select(User).where(User.telegram_id == telegram_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession,
    telegram_id: int,
    username: str = None,
    first_name: str = None,
    referred_by: int = None,
) -> User:
    user = User(
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
        referred_by=referred_by,
    )
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user


async def update_user(session: AsyncSession, user: User, **kwargs) -> User:
    for key, value in kwargs.items():
        if key not in ALLOWED_USER_UPDATE_FIELDS:
            continue
        setattr(user, key, value)
    await session.flush()
    await session.refresh(user)
    return user


async def extend_subscription(session: AsyncSession, user: User, days: int) -> User:
    now = now_utc()
    if user.subscription_end and user.subscription_end > now:
        current_end = user.subscription_end
    else:
        current_end = now

    if days >= 36500:
        from bot.constants import PERMANENT_END_DATE

        new_end = PERMANENT_END_DATE
    else:
        new_end = current_end + timedelta(days=days)

    return await update_user(session, user, subscription_end=new_end)


async def get_user_count(session: AsyncSession) -> int:
    stmt = select(func.count(User.id)).where(User.is_deleted.is_(False))
    result = await session.execute(stmt)
    return result.scalar_one()


async def get_active_subscriptions_count(session: AsyncSession) -> int:
    now = now_utc()
    stmt = select(func.count(User.id)).where(
        User.subscription_end > now,
        User.is_deleted.is_(False),
    )
    result = await session.execute(stmt)
    return result.scalar_one()


async def get_new_users_count_24h(session: AsyncSession) -> int:
    now = now_utc()
    stmt = select(func.count(User.id)).where(
        User.created_at > now - timedelta(hours=24),
        User.is_deleted.is_(False),
    )
    result = await session.execute(stmt)
    return result.scalar_one()


async def get_dashboard_stats(session: AsyncSession) -> dict:
    now = now_utc()
    stmt = select(
        func.count(User.id).label("total"),
        func.count(User.id).filter(User.subscription_end > now).label("active"),
        func.count(User.id)
        .filter(User.created_at > now - timedelta(hours=24))
        .label("new_24h"),
    ).where(User.is_deleted.is_(False))
    result = await session.execute(stmt)
    row = result.one()
    return {"total": row.total, "active": row.active, "new_24h": row.new_24h}


async def get_users_paginated(
    session: AsyncSession, page: int = 1, per_page: int = 10
) -> list[User]:
    offset = (page - 1) * per_page
    result = await session.execute(
        select(User)
        .where(User.is_deleted.is_(False))
        .order_by(User.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    return result.scalars().all()


async def get_users_paginated_with_profiles(
    session: AsyncSession, page: int = 1, per_page: int = 10
) -> list[User]:
    offset = (page - 1) * per_page
    stmt = (
        select(User)
        .where(User.is_deleted.is_(False))
        .options(selectinload(User.profiles))
        .order_by(User.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    result = await session.execute(stmt)
    return result.scalars().unique().all()


async def get_user_referrals(session: AsyncSession, telegram_id: int) -> list[User]:
    stmt = (
        select(User)
        .where(User.referred_by == telegram_id, User.is_deleted.is_(False))
        .order_by(User.created_at.desc())
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_user_with_referrals(
    session: AsyncSession, telegram_id: int
) -> tuple[Optional[User], list[User]]:
    stmt = (
        select(User)
        .options(selectinload(User.profiles))
        .where(User.telegram_id == telegram_id, User.is_deleted.is_(False))
    )
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    referrals: list[User] = []
    if user:
        referrals = await get_user_referrals(session, telegram_id)
    return user, referrals


async def mark_user_bot_blocked(session: AsyncSession, telegram_id: int) -> None:
    await session.execute(
        update(User).where(User.telegram_id == telegram_id).values(is_bot_blocked=True)
    )
    await session.flush()


async def mark_user_bot_unblocked(session: AsyncSession, telegram_id: int) -> bool:
    result = await session.execute(
        update(User)
        .where(User.telegram_id == telegram_id, User.is_bot_blocked.is_(True))
        .values(is_bot_blocked=False)
    )
    await session.flush()
    return result.rowcount > 0


async def count_users_with_tariff(session: AsyncSession, tariff_id: int) -> int:
    stmt = select(func.count(User.id)).where(
        User.current_tariff_id == tariff_id, User.is_deleted.is_(False)
    )
    result = await session.execute(stmt)
    return result.scalar_one() or 0


async def get_user_by_username(session: AsyncSession, username: str) -> Optional[User]:
    clean = username.lstrip("@").strip()
    stmt = (
        select(User)
        .where(User.username.ilike(clean), User.is_deleted.is_(False))
        .options(selectinload(User.profiles))
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def search_user_flexible(session: AsyncSession, query: str) -> Optional[User]:
    query_str = query.strip()
    if not query_str:
        return None

    # Попытка поиска по числовому Telegram ID или DB ID
    if query_str.isdigit():
        num_id = int(query_str)
        user = await get_user_by_telegram_id(session, num_id)
        if user:
            return user
        # Поиск по внутреннему DB id
        stmt = select(User).where(User.id == num_id, User.is_deleted.is_(False)).options(selectinload(User.profiles))
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        if user:
            return user

    # Поиск по @username
    return await get_user_by_username(session, query_str)


async def _apply_user_filter(stmt, filter_type: str):
    now = now_utc()
    if filter_type == "new":
        stmt = stmt.where(User.created_at >= now - timedelta(days=7))
    elif filter_type == "active":
        stmt = stmt.where(User.subscription_end > now)
    elif filter_type == "expired":
        stmt = stmt.where(User.subscription_end.is_not(None), User.subscription_end <= now)
    elif filter_type == "no_sub":
        stmt = stmt.where(User.subscription_end.is_(None))
    elif filter_type == "problem":
        stmt = stmt.where((User.is_banned.is_(True)) | (User.is_bot_blocked.is_(True)))
    return stmt


async def get_filtered_user_count(session: AsyncSession, filter_type: str = "all") -> int:
    stmt = select(func.count(User.id)).where(User.is_deleted.is_(False))
    stmt = await _apply_user_filter(stmt, filter_type)
    result = await session.execute(stmt)
    return result.scalar_one() or 0


async def get_filtered_users_paginated_with_profiles(
    session: AsyncSession, filter_type: str = "all", page: int = 1, per_page: int = 10
) -> list[User]:
    offset = (page - 1) * per_page
    stmt = (
        select(User)
        .where(User.is_deleted.is_(False))
        .options(selectinload(User.profiles))
        .order_by(User.created_at.desc())
    )
    stmt = await _apply_user_filter(stmt, filter_type)
    stmt = stmt.offset(offset).limit(per_page)
    result = await session.execute(stmt)
    return result.scalars().unique().all()

