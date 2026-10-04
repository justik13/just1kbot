from datetime import timedelta
import inspect

from sqlalchemy import and_, case, false, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from config.constants import (
    AMNEZIA_PROTOCOLS,
    PERMANENT_END_DATE,
    PERMANENT_SUBSCRIPTION_DAYS,
    REFERRAL_ACTIVE_MIN_TOPUP_RUB,
    XRAY_PROTOCOL,
)
from config.enums import WhiteInternetProvisioningStatus, WhiteInternetStatus
from database.models import Order, Payment, Server, Tariff, User, VPNProfile, WhiteInternetSubscription
from database.repositories.profiles_repo import PROFILE_LIST_HIDDEN_STATUSES
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
    "last_trial_reset_at",
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
    scalar_fn = getattr(result, "scalar_one_or_none", None)
    if callable(scalar_fn):
        res = scalar_fn()
        if inspect.isawaitable(res):
            res = await res
        return res
    return None


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
    # Lock the row so concurrent read-modify-write extensions cannot lose
    # updates. Safe to call with an already-locked user (same transaction).
    await session.scalar(select(User).where(User.id == user.id).with_for_update())
    now = now_utc()
    if user.subscription_end and user.subscription_end > now:
        current_end = user.subscription_end
    else:
        current_end = now

    if days >= PERMANENT_SUBSCRIPTION_DAYS:
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


async def get_dashboard_stats(session: AsyncSession) -> dict:
    now = now_utc()
    current_cycle = now.strftime("%Y-%m")
    active_wi_sub_user_ids = select(WhiteInternetSubscription.user_id).where(
        WhiteInternetSubscription.status.in_(
            (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.EXHAUSTED)
        ),
        WhiteInternetSubscription.expires_at > now,
    )
    stmt = select(
        func.count(User.id).label("total"),
        func.count(User.id).filter(User.subscription_end > now).label("active"),
        func.count(User.id)
        .filter(User.created_at > now - timedelta(hours=24))
        .label("new_24h"),
        func.coalesce(func.sum(User.total_traffic_bytes), 0).label("total_traffic_bytes"),
        func.coalesce(func.sum(User.total_wi_traffic_bytes), 0).label("total_wi_traffic_bytes"),
        func.coalesce(
            func.avg(User.total_traffic_bytes).filter(User.subscription_end > now),
            0,
        ).label("avg_traffic_bytes_active"),
        func.coalesce(
            func.avg(
                case(
                    (User.traffic_cycle == current_cycle, User.monthly_awg_bytes),
                    else_=0,
                )
            ).filter(User.subscription_end > now),
            0,
        ).label("avg_monthly_awg_bytes"),
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
        "avg_monthly_awg_bytes": int(row.avg_monthly_awg_bytes or 0),
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
    real_topup = (
        select(Payment.id)
        .where(
            Payment.user_id == referral.id,
            Payment.provider_status == "succeeded",
            Payment.fulfillment_status == "succeeded",
            Payment.credited_at.is_not(None),
            Payment.amount >= REFERRAL_ACTIVE_MIN_TOPUP_RUB,
        )
        .exists()
    )
    return or_(qualifying_order, real_topup)


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


async def get_referral_leaderboard(
    session: AsyncSession, limit: int = 5
) -> list[tuple[int, int]]:
    """Return top referrers by count of active referred users [(telegram_id, active_count), ...]."""
    limit = max(1, min(limit, 100))
    referrer = aliased(User)
    referral = aliased(User)

    stmt = (
        select(referrer.telegram_id, func.count(referral.id).label("active_count"))
        .join(referral, referral.referred_by == referrer.telegram_id)
        .where(
            referrer.is_deleted.is_(False),
            referral.is_deleted.is_(False),
            _referral_paid_activity_condition(referral),
        )
        .group_by(referrer.telegram_id)
        .order_by(text("active_count DESC"), referrer.telegram_id.asc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [(int(row[0]), int(row[1])) for row in rows]


async def get_user_referral_rank(
    session: AsyncSession, telegram_id: int
) -> tuple[int | None, int]:
    """Return (rank, active_count) for the user. Rank is 1-indexed, or None if active_count == 0."""
    active_count = await get_user_active_referrals_count(session, telegram_id)
    if active_count == 0:
        return None, 0

    referrer = aliased(User)
    referral = aliased(User)

    subq = (
        select(
            referrer.telegram_id.label("tid"),
            func.count(referral.id).label("cnt"),
        )
        .join(referral, referral.referred_by == referrer.telegram_id)
        .where(
            referrer.is_deleted.is_(False),
            referral.is_deleted.is_(False),
            _referral_paid_activity_condition(referral),
        )
        .group_by(referrer.telegram_id)
        .subquery()
    )

    rank_stmt = select(func.count(subq.c.tid)).where(
        or_(
            subq.c.cnt > active_count,
            and_(subq.c.cnt == active_count, subq.c.tid < telegram_id),
        )
    )
    higher_count = (await session.scalar(rank_stmt)) or 0
    return int(higher_count + 1), active_count


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
            Order.service_type != "topup",
        )
    )
    if not isinstance(paid_orders, (int, float)):
        if paid_orders is not None:
            return False
        paid_orders = 0
    if int(paid_orders) > 0:
        return False

    paid_payments = await session.scalar(
        select(func.count(Payment.id)).where(
            Payment.user_id == user_id,
            Payment.credited_at.is_not(None),
            Payment.fulfillment_status == "succeeded",
            Payment.amount > 0,
        )
    )
    if not isinstance(paid_payments, (int, float)):
        if paid_payments is not None:
            return False
        paid_payments = 0
    return int(paid_payments) == 0


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


async def count_users_with_tariff(session: AsyncSession, tariff_id: int) -> int:
    if not isinstance(tariff_id, int) or tariff_id < 1 or tariff_id > MAX_INT32:
        return 0
    stmt = select(func.count(User.id)).where(
        User.current_tariff_id == tariff_id, User.is_deleted.is_(False)
    )
    result = await session.execute(stmt)
    return result.scalar_one() or 0


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
        .options(selectinload(User.profiles))
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
                .options(selectinload(User.profiles))
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

    # 3. Canonical client UUID (36 chars: 8-4-4-4-12)
    if len(query_str) == 36 and query_str.count("-") == 4:
        sub_user_id = await session.scalar(
            select(WhiteInternetSubscription.user_id)
            .where(WhiteInternetSubscription.uuid == query_str)
            .limit(1)
        )
        if sub_user_id:
            stmt = (
                select(User)
                .where(User.id == sub_user_id, User.is_deleted.is_(False))
                .options(selectinload(User.profiles))
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    # 4. Peer ID (AmneziaWG)
    peer_user_id = await session.scalar(
        select(VPNProfile.user_id)
        .where(VPNProfile.peer_id == query_str)
        .limit(1)
    )
    if peer_user_id:
        stmt = (
            select(User)
            .where(User.id == peer_user_id, User.is_deleted.is_(False))
            .options(selectinload(User.profiles))
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    # 5. Payment ID (external_id)
    payment_user_id = await session.scalar(
        select(Payment.user_id)
        .where(Payment.external_id == query_str)
        .limit(1)
    )
    if payment_user_id:
        stmt = (
            select(User)
            .where(User.id == payment_user_id, User.is_deleted.is_(False))
            .options(selectinload(User.profiles))
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    # 6. HWID lookup (GIN index on active_hwids)
    if len(query_str) >= 8:
        hwid_user_id = await session.scalar(
            select(WhiteInternetSubscription.user_id)
            .where(WhiteInternetSubscription.active_hwids.has_key(query_str))
            .limit(1)
        )
        if hwid_user_id:
            stmt = (
                select(User)
                .where(User.id == hwid_user_id, User.is_deleted.is_(False))
                .options(selectinload(User.profiles))
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    return None


def get_effective_active_condition(now=None):
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
    return or_(
        User.subscription_end > now,
        User.id.in_(xray_active_subq),
    )


def get_effective_expiring_3d_condition(now=None):
    if now is None:
        now = now_utc()
    limit_3d = now + timedelta(days=3)
    awg_expiring = and_(
        User.subscription_end > now,
        User.subscription_end <= limit_3d,
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
    awg_longer = User.subscription_end > limit_3d
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
        or_(awg_expiring, xray_expiring),
        ~awg_longer,
        ~xray_longer,
    )


def get_effective_expired_condition(now=None):
    if now is None:
        now = now_utc()
    active_cond = get_effective_active_condition(now)
    had_awg = User.subscription_end.is_not(None)
    had_xray = User.id.in_(select(WhiteInternetSubscription.user_id))
    return and_(or_(had_awg, had_xray), ~active_cond)


def get_effective_never_condition():
    return and_(
        User.subscription_end.is_(None),
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
        stmt = stmt.where(~get_effective_active_condition(now))
    elif filter_type == "never":
        stmt = stmt.where(get_effective_never_condition())
    elif filter_type in ("banned", "problem"):
        stmt = stmt.where(
            (User.is_banned.is_(True)) | (User.is_bot_blocked.is_(True))
        )
    elif filter_type == "server" and filter_param is not None:
        try:
            target_server_id = int(filter_param)
        except (ValueError, TypeError):
            return stmt.where(false())
        if not (1 <= target_server_id <= MAX_INT32):
            return stmt.where(false())
        server_proto_subq = select(Server.protocol).where(Server.id == target_server_id).scalar_subquery()
        stmt = stmt.where(
            or_(
                and_(
                    server_proto_subq.in_(AMNEZIA_PROTOCOLS),
                    User.profiles.any(
                        (VPNProfile.server_id == target_server_id)
                        & (VPNProfile.provisioning_status.notin_(PROFILE_LIST_HIDDEN_STATUSES))
                    ),
                ),
                and_(
                    server_proto_subq == XRAY_PROTOCOL,
                    User.id.in_(
                        select(WhiteInternetSubscription.user_id).where(
                            WhiteInternetSubscription.origin_node_id == target_server_id,
                            WhiteInternetSubscription.status.in_([
                                WhiteInternetStatus.ACTIVE,
                                WhiteInternetStatus.PENDING,
                                WhiteInternetStatus.EXHAUSTED,
                            ]),
                            WhiteInternetSubscription.provisioning_status != WhiteInternetProvisioningStatus.PENDING_DELETE,
                        )
                    ),
                ),
            )
        )
    elif filter_type == "country" and filter_param:
        stmt = stmt.where(User.profiles.any(VPNProfile.server.has(Server.country_flag == str(filter_param))))
    elif filter_type == "tariff" and filter_param is not None:
        if filter_param == "white_internet":
            stmt = stmt.where(
                User.id.in_(
                    select(WhiteInternetSubscription.user_id).where(
                        WhiteInternetSubscription.status.in_([
                            WhiteInternetStatus.ACTIVE,
                            WhiteInternetStatus.PENDING,
                            WhiteInternetStatus.EXHAUSTED,
                        ]),
                        WhiteInternetSubscription.expires_at > now,
                        WhiteInternetSubscription.provisioning_status != WhiteInternetProvisioningStatus.PENDING_DELETE,
                    )
                )
            )
        else:
            try:
                val = int(filter_param)
            except (ValueError, TypeError):
                return stmt.where(false())
            if not (1 <= val <= MAX_INT32):
                return stmt.where(false())
            matching_tariff_ids = select(Tariff.id).where(
                Tariff.service_type == "awg",
                Tariff.device_limit == val,
            )
            stmt = stmt.where(
                or_(
                    User.current_tariff_id.in_(matching_tariff_ids),
                    and_(User.current_tariff_id.is_(None), User.device_limit == val),
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
        order_clause = User.subscription_end.asc()
    else:
        order_clause = User.created_at.desc()

    stmt = (
        stmt.options(selectinload(User.profiles))
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
            get_effective_expiring_3d_condition(now),
        ).label("expiring_3d"),
        func.count(User.id).filter(
            get_effective_expired_condition(now),
        ).label("expired"),
        func.count(User.id).filter(
            (User.is_banned.is_(True)) | (User.is_bot_blocked.is_(True))
        ).label("banned"),
    ).where(User.is_deleted.is_(False))
    result = (await session.execute(stmt)).one()
    return {
        "all": int(result.total or 0),
        "new_7d": int(result.new_7d or 0),
        "active": int(result.active or 0),
        "expiring_3d": int(result.expiring_3d or 0),
        "expired": int(result.expired or 0),
        "banned": int(result.banned or 0),
    }

