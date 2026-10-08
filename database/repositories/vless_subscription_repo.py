"""Transactional repository for standard VLESS subscriptions and INCY HWID tracking."""

from __future__ import annotations

from datetime import timedelta
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import VlessSubscription
from utils.datetime_helpers import now_utc

VLESS_HWID_TTL_HOURS = 48


def prune_stale_hwids(current_hwids: dict | None, ttl_hours: int = VLESS_HWID_TTL_HOURS) -> dict[str, str]:
    """Filter out HWIDs older than ttl_hours."""
    if not current_hwids:
        return {}
    now = now_utc()
    cutoff = (now - timedelta(hours=ttl_hours)).isoformat()
    return {
        h: ts
        for h, ts in current_hwids.items()
        if isinstance(ts, str) and ts >= cutoff
    }


async def get_or_create_subscription(
    session: AsyncSession,
    user_id: int,
) -> VlessSubscription:
    """Fetch existing VlessSubscription or create new unique credentials for user."""
    stmt = select(VlessSubscription).where(VlessSubscription.user_id == user_id)
    sub = (await session.execute(stmt)).scalar_one_or_none()
    if sub is not None:
        return sub

    token = secrets.token_urlsafe(32)
    client_uuid = str(uuid.uuid4())
    sub = VlessSubscription(
        user_id=user_id,
        token=token,
        uuid=client_uuid,
        is_active=True,
        active_hwids={},
    )
    session.add(sub)
    await session.flush()
    return sub


async def get_subscription_by_token(
    session: AsyncSession,
    token: str,
) -> VlessSubscription | None:
    """Fetch VlessSubscription by public token."""
    if not token or len(token) < 16:
        return None
    stmt = select(VlessSubscription).where(VlessSubscription.token == token)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_subscription_by_user_id(
    session: AsyncSession,
    user_id: int,
) -> VlessSubscription | None:
    """Fetch VlessSubscription by internal user_id."""
    stmt = select(VlessSubscription).where(VlessSubscription.user_id == user_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_active_hwid_count(
    session: AsyncSession,
    user_id: int,
    ttl_hours: int = VLESS_HWID_TTL_HOURS,
) -> int:
    """Return count of active non-stale HWIDs currently registered for user."""
    stmt = select(VlessSubscription.active_hwids).where(VlessSubscription.user_id == user_id)
    raw_hwids = (await session.execute(stmt)).scalar_one_or_none()
    active = prune_stale_hwids(raw_hwids, ttl_hours=ttl_hours)
    return len(active)


async def register_hwid_atomic(
    session: AsyncSession,
    subscription_id: int,
    hwid: str,
    effective_limit: int,
    ttl_hours: int = VLESS_HWID_TTL_HOURS,
) -> tuple[bool, int, int]:
    """Atomically registers an HWID for a VlessSubscription under row-level lock.

    effective_limit is the maximum number of VLESS HWIDs permitted given any
    existing AWG profiles already consuming quota.

    Returns:
        tuple[allowed: bool, active_count: int, effective_limit: int]
    """
    stmt = select(VlessSubscription).where(VlessSubscription.id == subscription_id).with_for_update()
    sub = (await session.execute(stmt)).scalar_one_or_none()
    if sub is None:
        return False, 0, max(0, effective_limit)

    clean_hwid = str(hwid).strip()[:128]
    if not clean_hwid:
        active = prune_stale_hwids(sub.active_hwids, ttl_hours=ttl_hours)
        return False, len(active), effective_limit

    active_hwids = prune_stale_hwids(sub.active_hwids, ttl_hours=ttl_hours)
    now = now_utc()

    # If already exceeds effective limit, trim oldest
    if effective_limit > 0 and len(active_hwids) > effective_limit:
        sorted_hwids = sorted(active_hwids.items(), key=lambda item: item[1], reverse=True)
        active_hwids = dict(sorted_hwids[:effective_limit])

    if clean_hwid in active_hwids:
        # Existing device — refresh activity timestamp
        active_hwids[clean_hwid] = now.isoformat()
        sub.active_hwids = active_hwids
        await session.flush()
        return True, len(active_hwids), effective_limit

    # New device — check available quota
    if len(active_hwids) >= effective_limit:
        sub.active_hwids = active_hwids
        await session.flush()
        return False, len(active_hwids), effective_limit

    # Quota available — register
    active_hwids[clean_hwid] = now.isoformat()
    sub.active_hwids = active_hwids
    await session.flush()
    return True, len(active_hwids), effective_limit


async def reset_hwids(
    session: AsyncSession,
    subscription_id: int,
) -> None:
    """Clear all registered HWIDs for subscription."""
    stmt = select(VlessSubscription).where(VlessSubscription.id == subscription_id).with_for_update()
    sub = (await session.execute(stmt)).scalar_one_or_none()
    if sub is not None:
        sub.active_hwids = {}
        await session.flush()
