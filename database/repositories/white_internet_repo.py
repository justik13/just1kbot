"""Transactional persistence boundary for White Internet subscriptions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from config.constants import (
    WHITE_INTERNET_HWID_TTL_HOURS,
    WHITE_INTERNET_MAX_DEVICE_LIMIT,
)
from config.enums import (
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from database.models import WhiteInternetSubscription
from utils.datetime_helpers import calculate_extension_end, now_utc


class WhiteInternetError(RuntimeError):
    """Base exception for White Internet domain errors."""


class WhiteInternetDeviceLimitExceededError(WhiteInternetError):
    pass


class WhiteInternetSubscriptionNotFoundError(WhiteInternetError):
    pass


class WhiteInternetInactiveSubscriptionError(WhiteInternetError):
    pass


def parse_hwid_last_seen_dt(val: Any) -> datetime:
    if isinstance(val, dict):
        raw = val.get("last_seen")
    else:
        raw = val
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(raw, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            pass
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw)
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return datetime.min.replace(tzinfo=timezone.utc)


def count_active_hwids(
    active_hwids: dict | None,
    now: datetime | None = None,
    ttl_hours: int = WHITE_INTERNET_HWID_TTL_HOURS,
) -> int:
    if not active_hwids or not isinstance(active_hwids, dict):
        return 0
    now = now or now_utc()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(hours=ttl_hours)
    return sum(1 for val in active_hwids.values() if parse_hwid_last_seen_dt(val) >= cutoff)


async def get_subscription_by_token(
    session: AsyncSession, token: str
) -> WhiteInternetSubscription | None:
    if not token or len(token) < 16:
        return None
    stmt = select(WhiteInternetSubscription).where(WhiteInternetSubscription.token == token)
    return await session.scalar(stmt)


async def get_subscription_by_user_id(
    session: AsyncSession, user_id: int
) -> WhiteInternetSubscription | None:
    if not isinstance(user_id, int) or user_id < 1:
        return None
    stmt = (
        select(WhiteInternetSubscription)
        .where(WhiteInternetSubscription.user_id == user_id)
        .order_by(WhiteInternetSubscription.id.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_white_internet_dashboard_stats(session: AsyncSession) -> dict:
    from sqlalchemy import func
    now = now_utc()
    stmt = select(
        func.count(WhiteInternetSubscription.id).filter(
            WhiteInternetSubscription.status == WhiteInternetStatus.ACTIVE,
            WhiteInternetSubscription.expires_at > now,
        ).label("active_count"),
        func.coalesce(func.sum(WhiteInternetSubscription.traffic_used_bytes), 0).label("total_traffic_bytes"),
    )
    result = await session.execute(stmt)
    row = result.one()
    return {
        "active_count": row.active_count or 0,
        "total_traffic_bytes": int(row.total_traffic_bytes or 0),
    }


async def get_subscription_with_lock(
    session: AsyncSession, subscription_id: int
) -> WhiteInternetSubscription | None:
    stmt = (
        select(WhiteInternetSubscription)
        .where(WhiteInternetSubscription.id == subscription_id)
        .with_for_update()
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def register_hwid_atomic(
    session: AsyncSession,
    subscription_id: int,
    raw_hwid: str,
    *,
    effective_limit: int,
    device_info: dict | None = None,
    ttl_hours: int = WHITE_INTERNET_HWID_TTL_HOURS,
) -> tuple[bool, int, int]:
    stmt = (
        select(WhiteInternetSubscription)
        .where(WhiteInternetSubscription.id == subscription_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    sub = await session.scalar(stmt)
    if not sub:
        return False, 0, effective_limit

    now = now_utc()
    active_hwids = dict(sub.active_hwids or {})

    # Prune stale
    cutoff = now - timedelta(hours=ttl_hours)
    active_hwids = {
        h: v for h, v in active_hwids.items()
        if parse_hwid_last_seen_dt(v) >= cutoff
    }

    if raw_hwid in active_hwids:
        cur_val = active_hwids[raw_hwid]
        if isinstance(cur_val, dict):
            cur_val["last_seen"] = now.isoformat()
            if device_info:
                cur_val.update(device_info)
        else:
            active_hwids[raw_hwid] = {
                "last_seen": now.isoformat(),
                **(device_info or {}),
            }
        sub.active_hwids = active_hwids
        flag_modified(sub, "active_hwids")
        await session.flush()
        return True, len(active_hwids), effective_limit

    if len(active_hwids) >= effective_limit:
        return False, len(active_hwids), effective_limit

    active_hwids[raw_hwid] = {
        "last_seen": now.isoformat(),
        **(device_info or {}),
    }
    sub.active_hwids = active_hwids
    flag_modified(sub, "active_hwids")
    await session.flush()
    return True, len(active_hwids), effective_limit


async def set_device_limit_atomic(
    session: AsyncSession,
    subscription_id: int,
    limit: int,
) -> WhiteInternetSubscription:
    if limit < 1 or limit > WHITE_INTERNET_MAX_DEVICE_LIMIT:
        raise WhiteInternetDeviceLimitExceededError(
            f"Device limit must be between 1 and {WHITE_INTERNET_MAX_DEVICE_LIMIT}"
        )
    sub = await session.get(WhiteInternetSubscription, subscription_id, with_for_update=True)
    if sub is None:
        raise WhiteInternetSubscriptionNotFoundError(f"Subscription {subscription_id} not found")

    sub.device_limit = limit
    current_hwids: dict[str, Any] = dict(sub.active_hwids or {})
    if len(current_hwids) > limit:
        sorted_hwids = sorted(
            current_hwids.items(),
            key=lambda item: parse_hwid_last_seen_dt(item[1]),
            reverse=True,
        )
        sub.active_hwids = dict(sorted_hwids[:limit])
        flag_modified(sub, "active_hwids")
    await session.flush()
    return sub


async def extend_subscription_atomic(
    session: AsyncSession,
    subscription_id: int,
    days: int,
    *,
    now: datetime | None = None,
) -> WhiteInternetSubscription:
    sub = await get_subscription_with_lock(session, subscription_id)
    if sub is None:
        raise WhiteInternetSubscriptionNotFoundError(f"Subscription {subscription_id} not found")

    if sub.status == WhiteInternetStatus.DISABLED:
        raise WhiteInternetInactiveSubscriptionError("Subscription is disabled")

    current_time = now or now_utc()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    old_expires_at = sub.expires_at
    if old_expires_at is not None and old_expires_at.tzinfo is None:
        old_expires_at = old_expires_at.replace(tzinfo=timezone.utc)

    is_expired = old_expires_at is not None and old_expires_at <= current_time
    is_new_cycle = is_expired or sub.status in (
        WhiteInternetStatus.EXPIRED,
        WhiteInternetStatus.EXHAUSTED,
        WhiteInternetStatus.PENDING,
    )

    sub.expires_at = calculate_extension_end(sub.expires_at, days, now=current_time)
    sub.desired_version += 1
    if is_new_cycle:
        sub.traffic_used_bytes = 0
        sub.traffic_overage_bytes = 0
        sub.traffic_uplink_bytes = 0
        sub.traffic_downlink_bytes = 0
        sub.extra_traffic_bytes = 0
        sub.notified_90p = False
        sub.status = WhiteInternetStatus.ACTIVE
        sub.status_reason = None
        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE

    sub.notified_3d = False
    sub.notified_1d = False
    sub.notified_2h = False
    sub.notified_expired = False

    await session.flush()
    return sub
