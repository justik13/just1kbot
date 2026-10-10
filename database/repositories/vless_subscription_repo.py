"""Transactional repository for standard VLESS subscriptions and INCY HWID tracking."""

from __future__ import annotations

import inspect
import logging
import re
from datetime import datetime, timedelta, timezone
import secrets
from typing import Any
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import VlessSubscription
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)

VLESS_HWID_TTL_HOURS = 48


def prune_stale_hwids(current_hwids: dict | None, ttl_hours: int = VLESS_HWID_TTL_HOURS) -> dict[str, Any]:
    """Filter out HWIDs older than ttl_hours, supporting both ISO timestamp strings and enriched metadata dicts."""
    if not isinstance(current_hwids, dict):
        return {}
    now = now_utc()
    try:
        ttl = int(ttl_hours)
    except (ValueError, TypeError):
        ttl = VLESS_HWID_TTL_HOURS
    cutoff = now - timedelta(hours=ttl)
    res: dict[str, Any] = {}
    for h, val in current_hwids.items():
        try:
            if isinstance(val, dict):
                raw_ts = val.get("last_seen")
            else:
                raw_ts = val
            if isinstance(raw_ts, datetime):
                dt = raw_ts
            elif isinstance(raw_ts, (int, float)):
                dt = datetime.fromtimestamp(raw_ts, tz=timezone.utc)
            elif isinstance(raw_ts, str):
                dt = datetime.fromisoformat(raw_ts)
            else:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                if isinstance(val, dict):
                    res[h] = val
                else:
                    res[h] = val if isinstance(val, str) else dt.isoformat()
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
        version=1,
        active_hwids={},
        pending_revoked_uuids=[],
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
    res = session.execute(stmt)
    if inspect.isawaitable(res):
        res = await res
    scalar = getattr(res, "scalar_one_or_none", None)
    if callable(scalar):
        val = scalar()
        if inspect.isawaitable(val):
            val = await val
        return val if isinstance(val, VlessSubscription) else None
    return None


async def get_active_hwid_count(
    session: AsyncSession,
    user_id: int,
    ttl_hours: int = VLESS_HWID_TTL_HOURS,
) -> int:
    """Return count of active non-stale HWIDs currently registered for user."""
    stmt = select(VlessSubscription.active_hwids).where(VlessSubscription.user_id == user_id)
    res = session.execute(stmt)
    if inspect.isawaitable(res):
        res = await res
    scalar = getattr(res, "scalar_one_or_none", None)
    if callable(scalar):
        val = scalar()
        if inspect.isawaitable(val):
            val = await val
        raw_hwids = val if isinstance(val, dict) else None
    else:
        raw_hwids = None
    active = prune_stale_hwids(raw_hwids, ttl_hours=ttl_hours)
    return len(active)


async def register_hwid_atomic(
    session: AsyncSession,
    subscription_id: int,
    hwid: str,
    effective_limit: int | None = None,
    ttl_hours: int = VLESS_HWID_TTL_HOURS,
    device_info: dict | None = None,
) -> tuple[bool, int, int]:
    """Atomically registers an HWID for a VlessSubscription under row-level lock.

    effective_limit is the maximum number of VLESS HWIDs permitted given any
    existing AWG profiles already consuming quota. If None, it is dynamically
    computed under User lock.

    Returns:
        tuple[allowed: bool, active_count: int, effective_limit: int]
    """
    if not hwid or not isinstance(hwid, str) or not re.match(r"^[a-zA-Z0-9_\-:]{3,128}$", hwid.strip()):
        return False, 0, max(0, effective_limit or 0)
    hwid = hwid.strip()

    # Resolve user_id without lock to enforce strict global lock hierarchy: User -> VlessSubscription
    user_id_stmt = select(VlessSubscription.user_id).where(VlessSubscription.id == subscription_id)
    res = session.execute(user_id_stmt)
    if inspect.isawaitable(res):
        res = await res
    scalar = getattr(res, "scalar_one_or_none", None)
    raw_sub_user_id = scalar() if callable(scalar) else None
    if inspect.isawaitable(raw_sub_user_id):
        raw_sub_user_id = await raw_sub_user_id
    if raw_sub_user_id is None:
        return False, 0, max(0, effective_limit or 0)
    sub_user_id = getattr(raw_sub_user_id, "user_id", raw_sub_user_id)

    # 1. Lock User row FIRST (populate_existing=True guarantees fresh attributes in Identity Map)
    from database.models import User
    user_stmt = select(User).where(User.id == sub_user_id).with_for_update().execution_options(populate_existing=True)
    user_res = session.execute(user_stmt)
    if inspect.isawaitable(user_res):
        user_res = await user_res
    user_scalar = getattr(user_res, "scalar_one_or_none", None)
    user = user_scalar() if callable(user_scalar) else None
    if inspect.isawaitable(user):
        user = await user

    # 2. Lock VlessSubscription row SECOND (populate_existing=True guarantees fresh attributes)
    stmt = (
        select(VlessSubscription)
        .where(VlessSubscription.id == subscription_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    sub_res = session.execute(stmt)
    if inspect.isawaitable(sub_res):
        sub_res = await sub_res
    sub_scalar = getattr(sub_res, "scalar_one_or_none", None)
    sub = sub_scalar() if callable(sub_scalar) else None
    if inspect.isawaitable(sub):
        sub = await sub
    if sub is None:
        return False, 0, max(0, effective_limit or 0)

    from services.subscription import SubscriptionService

    if (
        user is None
        or getattr(user, "is_banned", False)
        or getattr(user, "financial_hold", False)
        or getattr(user, "is_deleted", False)
        or not getattr(sub, "is_active", True)
        or (hasattr(user, "subscription_end") and not SubscriptionService.check_vless_access(user))
    ):
        active = prune_stale_hwids(getattr(sub, "active_hwids", None), ttl_hours=ttl_hours)
        return False, len(active), 0

    if effective_limit is None:
        from database.models import VPNProfile
        from services.device_service import RESERVING_STATUSES

        awg_stmt = select(func.count(VPNProfile.id)).where(
            VPNProfile.user_id == getattr(sub, "user_id", None),
            VPNProfile.provisioning_status.in_(RESERVING_STATUSES),
        )
        awg_res_raw = session.execute(awg_stmt)
        if inspect.isawaitable(awg_res_raw):
            awg_res_raw = await awg_res_raw
        awg_scalar = getattr(awg_res_raw, "scalar_one", None) or getattr(awg_res_raw, "scalar", None)
        awg_res = awg_scalar() if callable(awg_scalar) else 0
        if inspect.isawaitable(awg_res):
            awg_res = await awg_res
        awg_count = awg_res if isinstance(awg_res, int) else 0

        user_limit = (
            await SubscriptionService.get_effective_device_limit(session, user)
            if user
            else 5
        )
        user_limit_int = user_limit if isinstance(user_limit, int) else 5
        effective_limit = max(0, user_limit_int - awg_count)

    clean_hwid = str(hwid).strip().lower()[:128]
    if not clean_hwid:
        active = prune_stale_hwids(getattr(sub, "active_hwids", None), ttl_hours=ttl_hours)
        return False, len(active), effective_limit

    active_hwids = prune_stale_hwids(getattr(sub, "active_hwids", None), ttl_hours=ttl_hours)
    now = now_utc()

    if effective_limit <= 0:
        return False, len(active_hwids), 0

    entry: dict[str, Any] = {"last_seen": now.isoformat()}
    if device_info and isinstance(device_info, dict):
        for k, v in device_info.items():
            if isinstance(v, str) and v.strip():
                entry[k] = v.strip()[:64]

    if clean_hwid in active_hwids:
        # Existing device — refresh activity timestamp and update metadata
        prev = active_hwids[clean_hwid]
        if isinstance(prev, dict):
            updated_entry = {**prev, "last_seen": now.isoformat()}
            if device_info and isinstance(device_info, dict):
                for k, v in device_info.items():
                    if isinstance(v, str) and v.strip():
                        updated_entry[k] = v.strip()[:64]
            active_hwids[clean_hwid] = updated_entry
        else:
            active_hwids[clean_hwid] = entry
        sub.active_hwids = dict(active_hwids)
        flush_res = session.flush()
        if inspect.isawaitable(flush_res):
            await flush_res
        return True, len(active_hwids), effective_limit

    # New device — check available quota without evicting active devices on rejection
    if len(active_hwids) >= effective_limit:
        return False, len(active_hwids), effective_limit

    # Quota available — register
    active_hwids[clean_hwid] = entry
    sub.active_hwids = dict(active_hwids)
    flush_res = session.flush()
    if inspect.isawaitable(flush_res):
        await flush_res
    return True, len(active_hwids), effective_limit


async def reset_hwids(
    session: AsyncSession,
    subscription_id: int,
) -> tuple[str | None, str | None]:
    """Clear all registered HWIDs, increment monotonic version, and rotate client UUID to revoke access on old devices.

    Returns:
        tuple[old_uuid, new_uuid] or (None, None) if subscription not found.
    """
    stmt = select(VlessSubscription).where(VlessSubscription.id == subscription_id).with_for_update()
    sub = (await session.execute(stmt)).scalar_one_or_none()
    if sub is not None:
        old_uuid = sub.uuid
        new_uuid = str(uuid.uuid4())
        sub.uuid = new_uuid
        sub.version = (getattr(sub, "version", 1) or 1) + 1
        sub.active_hwids = {}
        pending = list(getattr(sub, "pending_revoked_uuids", None) or [])
        if old_uuid and old_uuid not in pending:
            pending.append(old_uuid)
        sub.pending_revoked_uuids = pending
        await session.flush()
        return old_uuid, new_uuid
    return None, None


async def rotate_token(
    session: AsyncSession,
    subscription_id: int,
    *,
    reset_hwids: bool = True,
) -> tuple[str, str | None, str | None]:
    """Atomically regenerates the VLESS subscription token, increments version, rotates UUID, and records old UUID for durable revocation.

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
    sub.version = (getattr(sub, "version", 1) or 1) + 1
    if reset_hwids:
        sub.active_hwids = {}
    pending = list(getattr(sub, "pending_revoked_uuids", None) or [])
    if old_uuid and old_uuid not in pending:
        pending.append(old_uuid)
    sub.pending_revoked_uuids = pending
    await session.flush()
    return token, old_uuid, new_uuid


async def pop_pending_revoked_uuid(
    session: AsyncSession,
    subscription_id: int,
    uuid_to_remove: str,
) -> None:
    """Removes a successfully deprovisioned UUID from pending_revoked_uuids under row lock."""
    stmt = select(VlessSubscription).where(VlessSubscription.id == subscription_id).with_for_update()
    sub = (await session.execute(stmt)).scalar_one_or_none()
    if sub and sub.pending_revoked_uuids:
        sub.pending_revoked_uuids = [u for u in sub.pending_revoked_uuids if u != uuid_to_remove]
        await session.flush()


async def get_subscriptions_with_pending_revocations(
    session: AsyncSession,
    limit: int = 50,
) -> list[VlessSubscription]:
    """Find subscriptions having pending revoked UUIDs requiring retry."""
    stmt = (
        select(VlessSubscription)
        .where(
            VlessSubscription.pending_revoked_uuids.is_not(None),
            func.jsonb_array_length(VlessSubscription.pending_revoked_uuids) > 0,
        )
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


