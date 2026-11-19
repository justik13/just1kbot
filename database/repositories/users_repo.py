from datetime import timedelta

from sqlalchemy import and_, case, false, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config.constants import (
    REFERRAL_ACTIVE_MIN_TOPUP_RUB,
)
from config.enums import WhiteInternetProvisioningStatus, WhiteInternetStatus
from database.models import Order, User, UserDevice, VlessSubscription, WhiteInternetSubscription
from utils.datetime_helpers import now_utc

ALLOWED_USER_UPDATE_FIELDS = {
    "username",
    "first_name",
    "is_bot_blocked",
    "referred_by",
    "last_payment_at",
    "notification_retry_count",
    "last_notification_attempt",
}


MAX_INT32 = 2_147_483_647
MAX_INT64 = 9_223_372_036_854_775_807


async def get_user_by_telegram_id(
    session: AsyncSession, telegram_id: int
) -> User | None:
    if not isinstance(telegram_id, int) or telegram_id < 1 or telegram_id > MAX_INT64:
        return None
    stmt = select(User).where(
        User.telegram_id == telegram_id,
        User.is_deleted.is_(False),
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_users_by_telegram_ids(
    session: AsyncSession, telegram_ids: list[int]
) -> dict[int, User]:
    if not telegram_ids:
        return {}
    valid_ids = [tid for tid in telegram_ids if isinstance(tid, int) and 1 <= tid <= MAX_INT64]
    if not valid_ids:
        return {}
    stmt = select(User).where(
        User.telegram_id.in_(valid_ids),
        User.is_deleted.is_(False),
    )
    result = await session.execute(stmt)
    users = result.scalars().all()
    return {u.telegram_id: u for u in users}


async def get_user_by_telegram_id_any(
    session: AsyncSession, telegram_id: int
) -> User | None:
    """
    Ищет пользователя включая soft-deleted.
    Используется для безопасного восстановления и предотвращения unique constraint.
    """
    if not isinstance(telegram_id, int) or telegram_id < 1 or telegram_id > MAX_INT64:
        return None
    stmt = select(User).where(User.telegram_id == telegram_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_user_by_id(
    session: AsyncSession, user_id: int
) -> User | None:
    """Retrieve user by database primary key ID."""
    if not isinstance(user_id, int) or user_id < 1 or user_id > MAX_INT32:
        return None
    stmt = select(User).where(User.id == user_id)
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


async def get_user_count(session: AsyncSession) -> int:
    stmt = select(func.count(User.id)).where(User.is_deleted.is_(False))
    result = await session.execute(stmt)
    return result.scalar_one()


async def get_active_subscriptions_count(session: AsyncSession) -> int:
    now = now_utc()
    stmt = (
        select(func.count(User.id))
        .join(VlessSubscription, VlessSubscription.user_id == User.id)
        .where(
            VlessSubscription.expires_at > now,
            User.is_deleted.is_(False),
        )
    )
    result = await session.execute(stmt)
    return result.scalar_one()


async def get_dashboard_stats(session: AsyncSession) -> dict:
    now = now_utc()
    current_cycle = now.strftime("%Y-%m")
    active_std_user_ids = select(VlessSubscription.user_id).where(
        VlessSubscription.expires_at > now,
    )
    active_wi_sub_user_ids = select(WhiteInternetSubscription.user_id).where(
        WhiteInternetSubscription.status.in_(
            (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.EXHAUSTED)
        ),
        WhiteInternetSubscription.expires_at > now,
    )
    stmt = select(
        func.count(User.id).label("total"),
        func.count(User.id).filter(User.id.in_(active_std_user_ids)).label("active"),
        func.count(User.id)
        .filter(User.created_at > now - timedelta(hours=24))
        .label("new_24h"),
        func.coalesce(func.sum(User.total_traffic_bytes), 0).label("total_traffic_bytes"),
        func.coalesce(func.sum(User.total_wi_traffic_bytes), 0).label("total_wi_traffic_bytes"),
        func.coalesce(
            func.avg(User.total_traffic_bytes).filter(User.id.in_(active_std_user_ids)),
            0,
        ).label("avg_traffic_bytes_active"),
        func.coalesce(
            func.avg(
                case(
                    (User.traffic_cycle == current_cycle, User.monthly_vless_bytes),
                    else_=0,
                )
            ).filter(User.id.in_(active_std_user_ids)),
            0,
        ).label("avg_monthly_vless_bytes"),
        func.coalesce(
            func.avg(
                case(
                    (User.traffic_cycle == current_cycle, User.monthly_wi_bytes),
                    else_=0,
                )
            ).filter(
                or_(
                    User.id.in_(active_wi_sub_user_ids),
                    and_(User.traffic_cycle == current_cycle, User.monthly_wi_bytes > 0),
                )
            ),
            0,
        ).label("avg_monthly_wi_bytes"),
    ).where(User.is_deleted.is_(False))
    result = await session.execute(stmt)
    row = result.one()
    return {
        "total": row.total,
        "active": row.active,
        "new_24h": row.new_24h,
        "total_traffic_bytes": int(row.total_traffic_bytes or 0),
        "total_wi_traffic_bytes": int(row.total_wi_traffic_bytes or 0),
        "avg_traffic_bytes_active": int(row.avg_traffic_bytes_active or 0),
        "avg_monthly_vless_bytes": int(row.avg_monthly_vless_bytes or 0),
        "avg_monthly_wi_bytes": int(row.avg_monthly_wi_bytes or 0),
    }


async def get_user_referrals_count(session: AsyncSession, telegram_id: int) -> int:
    if not isinstance(telegram_id, int) or telegram_id < 1 or telegram_id > MAX_INT64:
        return 0
    stmt = (
        select(func.count(User.id))
        .where(User.referred_by == telegram_id, User.is_deleted.is_(False))
    )
    result = await session.scalar(stmt)
    return result or 0


async def get_user_referrals_paginated(
    session: AsyncSession,
    telegram_id: int,
    page: int = 1,
    per_page: int = 10,
) -> tuple[list[User], int, int]:
    """Return (items, total_count, normalized_page)."""
    if not isinstance(telegram_id, int) or telegram_id < 1 or telegram_id > MAX_INT64:
        return [], 0, 1
    count = await get_user_referrals_count(session, telegram_id)
    if count == 0:
        return [], 0, 1

    per_page = min(max(1, per_page), 100)
    total_pages = max(1, (count + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page

    stmt = (
        select(User)
        .where(User.referred_by == telegram_id, User.is_deleted.is_(False))
        .order_by(User.created_at.desc(), User.id.desc())
        .offset(offset)
        .limit(per_page)
    )
    result = await session.execute(stmt)
    return result.scalars().all(), count, page


def _referral_paid_activity_condition(referral) -> object:
    """Qualifying activity: real-money order or legacy real top-up, ≥ threshold.

    Two branches, both require amount ≥ REFERRAL_ACTIVE_MIN_TOPUP_RUB:

    1. qualifying_order — any paid order with:
       - service_type == 'topup' (standard balance top-up flow), OR
       - payment_method != 'wallet' (direct tariff/service purchase via real external gateway).
       Wallet orders for non-topup services (including those paid from admin-gifted
       compensation balance) are intentionally excluded: gifted roubles must not
       activate referrals.

    2. real_topup — legacy `payments` rows (pre-orders-table era): succeeded +
       fulfilled + credited. Pure admin_adjustment freebies never have a
       `payments` row and therefore do not count.
    """
    qualifying_order = (
        select(Order.id)
        .where(
            Order.user_id == referral.id,
            Order.status == "paid",
            Order.amount_rub >= REFERRAL_ACTIVE_MIN_TOPUP_RUB,
            or_(
                Order.service_type == "topup",
                Order.payment_method != "wallet",
            ),
        )
        .exists()
    )
    return qualifying_order


async def get_user_active_referrals_count(
    session: AsyncSession, telegram_id: int
) -> int:
    """Return count of referred users with qualifying paid activity (≥ threshold).

    Each referral counts at most once: repeat payments earn the referrer a
    percentage but never increment this counter.
    """
    if not isinstance(telegram_id, int) or telegram_id < 1 or telegram_id > MAX_INT64:
        return 0
    stmt = (
        select(func.count(User.id)).where(
            User.referred_by == telegram_id,
            User.is_deleted.is_(False),
            _referral_paid_activity_condition(User),
        )
    )
    result = await session.scalar(stmt)
    return int(result or 0)


async def is_eligible_for_referral_first_discount(
    session: AsyncSession, user_id: int
) -> bool:
    """Check if user was referred by someone and hasn't paid for any tariff yet.

    Balance top-ups neither grant the discount nor burn eligibility for it:
    the 25% welcome discount applies to the first tariff order only.
    """
    user = await session.get(User, user_id)
    if (
        not user
        or not hasattr(user, "referred_by")
        or user.referred_by is None
        or user.referred_by == getattr(user, "telegram_id", None)
    ):
        return False

    paid_orders = await session.scalar(
        select(func.count(Order.id)).where(
            Order.user_id == user_id,
            Order.status == "paid",
            Order.amount_rub > 0,
            Order.service_type.in_(("standard", "white_internet")),
        )
    )
    if not isinstance(paid_orders, (int, float)):
        if paid_orders is not None:
            return False
        paid_orders = 0
    return int(paid_orders) == 0


async def mark_user_bot_blocked(session: AsyncSession, telegram_id: int) -> None:
    if not isinstance(telegram_id, int) or telegram_id < 1 or telegram_id > MAX_INT64:
        return
    await session.execute(
        update(User).where(User.telegram_id == telegram_id).values(is_bot_blocked=True)
    )
    await session.flush()


async def mark_user_bot_unblocked(session: AsyncSession, telegram_id: int) -> bool:
    if not isinstance(telegram_id, int) or telegram_id < 1 or telegram_id > MAX_INT64:
        return False
    result = await session.execute(
        update(User)
        .where(User.telegram_id == telegram_id, User.is_bot_blocked.is_(True))
        .values(is_bot_blocked=False)
    )
    await session.flush()
    return result.rowcount > 0


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    clean = username.lstrip("@").strip()
    if not clean:
        return None
    stmt = (
        select(User)
        .where(
            func.lower(User.username) == clean.lower(),
            User.is_deleted.is_(False),
        )
        .options(selectinload(User.vless_subscription))
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def search_user_flexible(session: AsyncSession, query: str) -> User | None:
    query_str = query.strip()
    if not query_str:
        return None

    # 1. Numeric ID (Telegram ID or internal User ID)
    if query_str.isdigit():
        num_id = int(query_str)
        if 1 <= num_id <= MAX_INT64:
            user = await get_user_by_telegram_id(session, num_id)
            if user:
                return user
        if 1 <= num_id <= MAX_INT32:
            stmt = (
                select(User)
                .where(User.id == num_id, User.is_deleted.is_(False))
                .options(selectinload(User.vless_subscription))
            )
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if user:
                return user
        return await get_user_by_username(session, query_str)

    # 2. Username exact case-insensitive lookup (B-tree index)
    user = await get_user_by_username(session, query_str)
    if user:
        return user

    # 3. Canonical client UUID or Order UUID (36 chars: 8-4-4-4-12)
    if len(query_str) == 36 and query_str.count("-") == 4:
        vless_user_id = await session.scalar(
            select(VlessSubscription.user_id)
            .where(VlessSubscription.uuid == query_str)
            .limit(1)
        )
        if vless_user_id:
            stmt = (
                select(User)
                .where(User.id == vless_user_id, User.is_deleted.is_(False))
                .options(selectinload(User.vless_subscription))
            )
            return (await session.execute(stmt)).scalar_one_or_none()

        sub_user_id = await session.scalar(
            select(WhiteInternetSubscription.user_id)
            .where(WhiteInternetSubscription.uuid == query_str)
            .limit(1)
        )
        if sub_user_id:
            stmt = (
                select(User)
                .where(User.id == sub_user_id, User.is_deleted.is_(False))
                .options(selectinload(User.vless_subscription))
            )
            return (await session.execute(stmt)).scalar_one_or_none()

        try:
            import uuid
            order_uuid = uuid.UUID(query_str)
            order_user_id = await session.scalar(
                select(Order.user_id)
                .where(Order.id == order_uuid)
                .limit(1)
            )
            if order_user_id:
                stmt = (
                    select(User)
                    .where(User.id == order_user_id, User.is_deleted.is_(False))
                    .options(selectinload(User.vless_subscription))
                )
                return (await session.execute(stmt)).scalar_one_or_none()
        except (ValueError, TypeError):
            pass

    # 4. Subscription token lookup (16..64 chars without spaces)
    if 16 <= len(query_str) <= 64 and " " not in query_str:
        vless_token_user_id = await session.scalar(
            select(VlessSubscription.user_id)
            .where(VlessSubscription.token == query_str)
            .limit(1)
        )
        if vless_token_user_id:
            stmt = (
                select(User)
                .where(User.id == vless_token_user_id, User.is_deleted.is_(False))
                .options(selectinload(User.vless_subscription))
            )
            return (await session.execute(stmt)).scalar_one_or_none()

        wi_token_user_id = await session.scalar(
            select(WhiteInternetSubscription.user_id)
            .where(WhiteInternetSubscription.token == query_str)
            .limit(1)
        )
        if wi_token_user_id:
            stmt = (
                select(User)
                .where(User.id == wi_token_user_id, User.is_deleted.is_(False))
                .options(selectinload(User.vless_subscription))
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    # 5. Order external_id
    order_user_id = await session.scalar(
        select(Order.user_id)
        .where(Order.external_id == query_str)
        .limit(1)
    )
    if order_user_id:
        stmt = (
            select(User)
            .where(User.id == order_user_id, User.is_deleted.is_(False))
            .options(selectinload(User.vless_subscription))
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    # 6. HWID lookup (authoritative user_devices registry)
    if len(query_str) >= 8:
        device_user_id = await session.scalar(
            select(UserDevice.user_id)
            .where(UserDevice.hwid == query_str)
            .limit(1)
        )
        if device_user_id:
            stmt = (
                select(User)
                .where(User.id == device_user_id, User.is_deleted.is_(False))
                .options(selectinload(User.vless_subscription))
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    return None


def get_effective_wi_active_condition(now=None):
    """Condition matching users with an active White Internet subscription."""
    if now is None:
        now = now_utc()
    xray_active_subq = select(WhiteInternetSubscription.user_id).where(
        WhiteInternetSubscription.status.in_([
            WhiteInternetStatus.ACTIVE,
            WhiteInternetStatus.PENDING,
            WhiteInternetStatus.EXHAUSTED,
        ]),
        WhiteInternetSubscription.expires_at > now,
        WhiteInternetSubscription.provisioning_status != WhiteInternetProvisioningStatus.PENDING_DELETE,
    )
    return User.id.in_(xray_active_subq)


def _std_active_subq(now=None):
    if now is None:
        now = now_utc()
    return select(VlessSubscription.user_id).where(VlessSubscription.expires_at > now)


def get_effective_active_condition(now=None):
    if now is None:
        now = now_utc()
    return or_(
        User.id.in_(_std_active_subq(now)),
        get_effective_wi_active_condition(now),
    )


def get_effective_expiring_3d_condition(now=None):
    if now is None:
        now = now_utc()
    limit_3d = now + timedelta(days=3)
    std_expiring = User.id.in_(
        select(VlessSubscription.user_id).where(
            VlessSubscription.expires_at > now,
            VlessSubscription.expires_at <= limit_3d,
        )
    )
    std_longer = User.id.in_(
        select(VlessSubscription.user_id).where(VlessSubscription.expires_at > limit_3d)
    )
    xray_expiring = User.id.in_(
        select(WhiteInternetSubscription.user_id).where(
            WhiteInternetSubscription.status.in_([
                WhiteInternetStatus.ACTIVE,
                WhiteInternetStatus.PENDING,
                WhiteInternetStatus.EXHAUSTED,
            ]),
            WhiteInternetSubscription.expires_at > now,
            WhiteInternetSubscription.expires_at <= limit_3d,
            WhiteInternetSubscription.provisioning_status != WhiteInternetProvisioningStatus.PENDING_DELETE,
        )
    )
    xray_longer = User.id.in_(
        select(WhiteInternetSubscription.user_id).where(
            WhiteInternetSubscription.status.in_([
                WhiteInternetStatus.ACTIVE,
                WhiteInternetStatus.PENDING,
                WhiteInternetStatus.EXHAUSTED,
            ]),
            WhiteInternetSubscription.expires_at > limit_3d,
            WhiteInternetSubscription.provisioning_status != WhiteInternetProvisioningStatus.PENDING_DELETE,
        )
    )
    return and_(
        or_(std_expiring, xray_expiring),
        ~std_longer,
        ~xray_longer,
    )


def get_effective_inactive_condition(now=None):
    if now is None:
        now = now_utc()
    xray_active_subq = select(WhiteInternetSubscription.user_id).where(
        WhiteInternetSubscription.status.in_([
            WhiteInternetStatus.ACTIVE,
            WhiteInternetStatus.PENDING,
            WhiteInternetStatus.EXHAUSTED,
        ]),
        WhiteInternetSubscription.expires_at > now,
        WhiteInternetSubscription.provisioning_status != WhiteInternetProvisioningStatus.PENDING_DELETE,
    )
    return and_(
        ~User.id.in_(_std_active_subq(now)),
        ~User.id.in_(xray_active_subq),
    )


def get_effective_expired_condition(now=None):
    if now is None:
        now = now_utc()
    inactive_cond = get_effective_inactive_condition(now)
    had_std = User.id.in_(
        select(VlessSubscription.user_id).where(VlessSubscription.expires_at.is_not(None))
    )
    had_xray = User.id.in_(select(WhiteInternetSubscription.user_id))
    return and_(or_(had_std, had_xray), inactive_cond)


def get_effective_never_condition():
    had_std = User.id.in_(
        select(VlessSubscription.user_id).where(VlessSubscription.expires_at.is_not(None))
    )
    return and_(
        ~had_std,
        ~User.id.in_(select(WhiteInternetSubscription.user_id)),
    )


def _apply_user_filters(stmt, filter_type: str, filter_param=None):
    now = now_utc()
    if filter_type == "new_24h":
        stmt = stmt.where(User.created_at >= now - timedelta(hours=24))
    elif filter_type in ("new_7d", "new"):
        stmt = stmt.where(User.created_at >= now - timedelta(days=7))
    elif filter_type == "expiring_3d":
        stmt = stmt.where(get_effective_expiring_3d_condition(now))
    elif filter_type == "active":
        stmt = stmt.where(get_effective_active_condition(now))
    elif filter_type == "expired":
        stmt = stmt.where(get_effective_expired_condition(now))
    elif filter_type == "no_sub":
        stmt = stmt.where(get_effective_inactive_condition(now))
    elif filter_type == "never":
        stmt = stmt.where(get_effective_never_condition())
    elif filter_type in ("banned", "problem"):
        stmt = stmt.where(
            User.is_bot_blocked.is_(True)
        )
    elif filter_type in ("active_standard", "standard_active"):
        stmt = stmt.where(User.id.in_(_std_active_subq(now)))
    elif filter_type in ("active_wi", "wi_active"):
        stmt = stmt.where(get_effective_wi_active_condition(now))
    elif filter_type == "tariff" and filter_param is not None:
        if filter_param == "white_internet":
            stmt = stmt.where(get_effective_wi_active_condition(now))
        else:
            try:
                val = int(filter_param)
            except (ValueError, TypeError):
                return stmt.where(false())
            if not (1 <= val <= MAX_INT32):
                return stmt.where(false())
            stmt = stmt.where(
                User.id.in_(
                    select(VlessSubscription.user_id).where(VlessSubscription.device_limit == val)
                )
            )
    return stmt


async def get_filtered_users_count(
    session: AsyncSession,
    filter_type: str = "all",
    filter_param=None,
) -> int:
    stmt = select(func.count(User.id.distinct())).where(User.is_deleted.is_(False))
    stmt = _apply_user_filters(stmt, filter_type, filter_param)
    result = await session.execute(stmt)
    return result.scalar_one() or 0


async def get_filtered_users_paginated(
    session: AsyncSession,
    filter_type: str = "all",
    page: int = 1,
    per_page: int = 10,
    filter_param=None,
) -> list[User]:
    offset = (page - 1) * per_page
    stmt = select(User).where(User.is_deleted.is_(False))
    stmt = _apply_user_filters(stmt, filter_type, filter_param)

    if filter_type == "expiring_3d":
        std_expiry_subq = (
            select(VlessSubscription.expires_at)
            .where(VlessSubscription.user_id == User.id)
            .limit(1)
            .scalar_subquery()
        )
        wi_expiry_subq = (
            select(WhiteInternetSubscription.expires_at)
            .where(
                WhiteInternetSubscription.user_id == User.id,
                WhiteInternetSubscription.status.in_([
                    WhiteInternetStatus.ACTIVE,
                    WhiteInternetStatus.PENDING,
                    WhiteInternetStatus.EXHAUSTED,
                ]),
                WhiteInternetSubscription.provisioning_status != WhiteInternetProvisioningStatus.PENDING_DELETE,
            )
            .limit(1)
            .scalar_subquery()
        )
        order_clause = func.coalesce(std_expiry_subq, wi_expiry_subq).asc()
    else:
        order_clause = User.created_at.desc()

    stmt = (
        stmt.options(selectinload(User.vless_subscription))
        .order_by(order_clause, User.id.desc())
        .offset(offset)
        .limit(per_page)
    )
    result = await session.execute(stmt)
    return list(result.scalars().unique().all())


async def get_user_filter_counts(session: AsyncSession) -> dict[str, int]:
    """Return aggregated counts for main user filter categories in a single query."""
    now = now_utc()
    stmt = select(
        func.count(User.id).label("total"),
        func.count(User.id).filter(
            User.created_at >= now - timedelta(days=7),
        ).label("new_7d"),
        func.count(User.id).filter(
            get_effective_active_condition(now),
        ).label("active"),
        func.count(User.id).filter(
            User.id.in_(_std_active_subq(now)),
        ).label("active_standard"),
        func.count(User.id).filter(
            get_effective_wi_active_condition(now),
        ).label("active_wi"),
        func.count(User.id).filter(
            get_effective_expiring_3d_condition(now),
        ).label("expiring_3d"),
        func.count(User.id).filter(
            get_effective_expired_condition(now),
        ).label("expired"),
        func.count(User.id).filter(
            get_effective_never_condition(),
        ).label("never"),
        func.count(User.id).filter(
            User.is_bot_blocked.is_(True)
        ).label("banned"),
    ).where(User.is_deleted.is_(False))
    result = (await session.execute(stmt)).one()
    return {
        "all": int(result.total or 0),
        "new_7d": int(result.new_7d or 0),
        "active": int(result.active or 0),
        "active_standard": int(result.active_standard or 0),
        "active_wi": int(result.active_wi or 0),
        "expiring_3d": int(result.expiring_3d or 0),
        "expired": int(result.expired or 0),
        "never": int(result.never or 0),
        "banned": int(result.banned or 0),
    }

