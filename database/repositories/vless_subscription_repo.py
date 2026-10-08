"""Transactional repository for standard VLESS subscriptions and INCY HWID tracking."""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
import secrets
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import VlessSubscription
from utils.datetime_helpers import now_utc

VLESS_HWID_TTL_HOURS = 48


def prune_stale_hwids(current_hwids: dict | None, ttl_hours: int = VLESS_HWID_TTL_HOURS) -> dict[str, str]:
    """Filter out HWIDs older than ttl_hours."""
    if not isinstance(current_hwids, dict):
        return {}
    now = now_utc()
    cutoff = now - timedelta(hours=ttl_hours)
    res = {}
    for h, ts in current_hwids.items():
        if not isinstance(ts, str):
            continue
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                res[h] = ts
        except Exception:
            continue
    return res


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
    try:
        async with session.begin_nested():
            session.add(sub)
            await session.flush()
        return sub
    except IntegrityError:
        # Concurrent creation race: existing row won the race
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return existing
        raise


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
    try:
        res = session.execute(stmt)
        if inspect.isawaitable(res):
            res = await res
        scalar = getattr(res, "scalar_one_or_none", None)
        if callable(scalar):
            val = scalar()
            if inspect.isawaitable(val):
                val = await val
            return val if isinstance(val, VlessSubscription) else None
    except Exception:
        pass
    return None


async def get_active_hwid_count(
    session: AsyncSession,
    user_id: int,
    ttl_hours: int = VLESS_HWID_TTL_HOURS,
) -> int:
    """Return count of active non-stale HWIDs currently registered for user."""
    stmt = select(VlessSubscription.active_hwids).where(VlessSubscription.user_id == user_id)
    try:
        res = session.execute(stmt)
        if inspect.isawaitable(res):
            res = await res
        scalar = getattr(res, "scalar_one_or_none", None)
        if callable(scalar):
            raw_hwids = scalar()
            if inspect.isawaitable(raw_hwids):
                raw_hwids = await raw_hwids
        else:
            raw_hwids = None
    except Exception:
        raw_hwids = None
    active = prune_stale_hwids(raw_hwids, ttl_hours=ttl_hours)
    return len(active)


async def register_hwid_atomic(
    session: AsyncSession,
    subscription_id: int,
    hwid: str,
    effective_limit: int | None = None,
    ttl_hours: int = VLESS_HWID_TTL_HOURS,
) -> tuple[bool, int, int]:
    """Atomically registers an HWID for a VlessSubscription under row-level lock.

    effective_limit is the maximum number of VLESS HWIDs permitted given any
    existing AWG profiles already consuming quota. If None, it is dynamically
    computed under User lock.

    Returns:
        tuple[allowed: bool, active_count: int, effective_limit: int]
    """
    stmt = select(VlessSubscription).where(VlessSubscription.id == subscription_id).with_for_update()
    sub = (await session.execute(stmt)).scalar_one_or_none()
    if sub is None:
        return False, 0, max(0, effective_limit or 0)

    # Lock User row to synchronize combined quota check across AWG and VLESS
    from database.models import User
    user = (await session.execute(select(User).where(User.id == sub.user_id).with_for_update())).scalar_one_or_none()

    if effective_limit is None:
        from database.models import VPNProfile
        from services.device_service import RESERVING_STATUSES
        from services.subscription_service import SubscriptionService

        awg_res = (
            await session.execute(
                select(func.count(VPNProfile.id)).where(
                    VPNProfile.user_id == sub.user_id,
                    VPNProfile.provisioning_status.in_(RESERVING_STATUSES),
                )
            )
        ).scalar_one()
        awg_count = awg_res if isinstance(awg_res, int) else 0

        user_limit = (
            await SubscriptionService.get_effective_device_limit(session, user)
            if user
            else 5
        )
        effective_limit = max(0, user_limit - awg_count)

    clean_hwid = str(hwid).strip().lower()[:128]
    if not clean_hwid:
        active = prune_stale_hwids(sub.active_hwids, ttl_hours=ttl_hours)
        return False, len(active), effective_limit

    active_hwids = prune_stale_hwids(sub.active_hwids, ttl_hours=ttl_hours)
    now = now_utc()

    if effective_limit <= 0:
        return False, len(active_hwids), 0

    # If already exceeds effective limit, trim oldest
    if len(active_hwids) > effective_limit:
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
) -> tuple[str | None, str | None]:
    """Clear all registered HWIDs and rotate client UUID to revoke access on old devices.

    Returns:
        tuple[old_uuid, new_uuid] or (None, None) if subscription not found.
    """
    stmt = select(VlessSubscription).where(VlessSubscription.id == subscription_id).with_for_update()
    sub = (await session.execute(stmt)).scalar_one_or_none()
    if sub is not None:
        old_uuid = sub.uuid
        new_uuid = str(uuid.uuid4())
        sub.uuid = new_uuid
        sub.active_hwids = {}
        await session.flush()
        return old_uuid, new_uuid
    return None, None


async def rotate_token(
    session: AsyncSession,
    subscription_id: int,
    *,
    reset_hwids: bool = True,
) -> tuple[str, str | None, str | None]:
    """Atomically regenerates the VLESS subscription token, rotates UUID, and clears active HWIDs.

    Returns:
        tuple[new_token, old_uuid, new_uuid]
    """
    stmt = select(VlessSubscription).where(VlessSubscription.id == subscription_id).with_for_update()
    sub = (await session.execute(stmt)).scalar_one_or_none()
    if sub is None:
        raise ValueError(f"VlessSubscription {subscription_id} not found")

    token = secrets.token_urlsafe(32)
    old_uuid = sub.uuid
    new_uuid = str(uuid.uuid4())
    sub.token = token
    sub.uuid = new_uuid
    if reset_hwids:
        sub.active_hwids = {}
    await session.flush()
    return token, old_uuid, new_uuid

