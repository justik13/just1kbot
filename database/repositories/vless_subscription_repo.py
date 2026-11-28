"""Transactional repository for standard VLESS subscriptions and INCY HWID tracking."""

from __future__ import annotations

import logging
from datetime import datetime
import secrets
import uuid

from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from database.models import User, VlessSubscription
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)


async def get_or_create_subscription(
    session: AsyncSession,
    user_id: int,
) -> VlessSubscription:
    """Fetch existing VlessSubscription or create new unique credentials for user."""
    stmt = select(VlessSubscription).where(VlessSubscription.user_id == user_id)
    sub = await session.scalar(stmt)
    if isinstance(sub, VlessSubscription):
        return sub

    token = secrets.token_urlsafe(32)
    client_uuid = str(uuid.uuid4())
    sub = VlessSubscription(
        user_id=user_id,
        token=token,
        uuid=client_uuid,
        is_active=False,
        version=1,
        pending_revoked_uuids=[],
        node_traffic_snapshots={"_fresh": True},
    )
    try:
        async with session.begin_nested():
            session.add(sub)
            await session.flush()
        return sub
    except IntegrityError:
        # Concurrent creation race: existing row won the race
        existing = await session.scalar(stmt)
        if isinstance(existing, VlessSubscription):
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
    val = await session.scalar(stmt)
    return val if isinstance(val, VlessSubscription) else None


async def get_subscription_by_user_id(
    session: AsyncSession,
    user_id: int,
) -> VlessSubscription | None:
    """Fetch VlessSubscription by internal user_id."""
    stmt = select(VlessSubscription).where(VlessSubscription.user_id == user_id)
    val = await session.scalar(stmt)
    return val if isinstance(val, VlessSubscription) else None


async def lock_subscription_with_user(
    session: AsyncSession,
    subscription_id: int,
) -> VlessSubscription | None:
    """Lock User FIRST, then VlessSubscription SECOND (canonical lock hierarchy)."""
    with session.no_autoflush:
        row = (
            await session.execute(
                select(VlessSubscription.id, VlessSubscription.user_id).where(
                    VlessSubscription.id == subscription_id
                )
            )
        ).first()
    if row is None:
        return None
    if row.user_id is not None:
        await session.execute(
            select(User.id).where(User.id == row.user_id).with_for_update()
        )
    return await session.scalar(
        select(VlessSubscription)
        .where(VlessSubscription.id == subscription_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def reset_hwids(
    session: AsyncSession,
    subscription_id: int,
) -> tuple[str | None, str | None]:
    """Increment monotonic version and rotate client UUID to revoke access on old devices.

    Returns:
        tuple[old_uuid, new_uuid] or (None, None) if subscription not found.
    """
    sub = await lock_subscription_with_user(session, subscription_id)
    if isinstance(sub, VlessSubscription):
        old_uuid = sub.uuid
        new_uuid = str(uuid.uuid4())
        sub.uuid = new_uuid
        sub.version = (getattr(sub, "version", 1) or 1) + 1
        sub.node_traffic_snapshots = {"_fresh": True}
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
    sub = await lock_subscription_with_user(session, subscription_id)
    if not isinstance(sub, VlessSubscription):
        raise ValueError(f"VlessSubscription {subscription_id} not found")

    token = secrets.token_urlsafe(32)
    old_uuid: str | None = None
    new_uuid: str | None = None
    sub.token = token
    if reset_hwids:
        old_uuid = sub.uuid
        new_uuid = str(uuid.uuid4())
        sub.uuid = new_uuid
        sub.version = (getattr(sub, "version", 1) or 1) + 1
        sub.node_traffic_snapshots = {"_fresh": True}
        pending = list(getattr(sub, "pending_revoked_uuids", None) or [])
        if old_uuid and old_uuid not in pending:
            pending.append(old_uuid)
        sub.pending_revoked_uuids = pending
    await session.flush()
    return token, old_uuid, new_uuid


async def add_pending_revocation_uuids(
    session: AsyncSession,
    subscription_id: int,
    uuids: list[str],
) -> VlessSubscription | None:
    """Adds a list of revoked UUIDs to pending_revoked_uuids under row lock."""
    if not uuids:
        return None
    sub = await lock_subscription_with_user(session, subscription_id)
    if isinstance(sub, VlessSubscription):
        pending = list(getattr(sub, "pending_revoked_uuids", None) or [])
        added = False
        for u in uuids:
            if u and u not in pending:
                pending.append(u)
                added = True
        if added:
            sub.pending_revoked_uuids = pending
            sub.version = (getattr(sub, "version", 1) or 1) + 1
            await session.flush()
    return sub


async def pop_pending_revoked_uuid(
    session: AsyncSession,
    subscription_id: int,
    uuid_to_remove: str,
) -> None:
    """Removes a successfully deprovisioned UUID from pending_revoked_uuids under row lock."""
    sub = await lock_subscription_with_user(session, subscription_id)
    if isinstance(sub, VlessSubscription) and sub.pending_revoked_uuids:
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
        .order_by(VlessSubscription.updated_at.asc(), VlessSubscription.id.asc())
        .limit(limit)
    )
    res = await session.scalars(stmt)
    return list(res.all())


async def bump_subscription_version(
    session: AsyncSession,
    subscription_id: int,
) -> VlessSubscription | None:
    """Atomically bump subscription version under row lock."""
    sub = await lock_subscription_with_user(session, subscription_id)
    if sub:
        sub.version = (sub.version or 1) + 1
        await session.flush()
    return sub


async def record_vless_traffic_atomic(
    session: AsyncSession,
    subscription_id: int,
    server_id: int,
    node_epoch: str,
    raw_uplink: int,
    raw_downlink: int,
    *,
    client_uuid: str | None = None,
    expected_uuid: str | None = None,
    now: datetime | None = None,
) -> tuple[int, int, int]:
    """Atomically record and calculate monotonic traffic deltas for a VLESS subscription on a specific server node.

    Locks User FIRST, then VlessSubscription SECOND (canonical hierarchy: User -> Subscription).
    Updates vless_subscriptions.traffic_down/up, node_traffic_snapshots,
    and updates User.total_traffic_bytes and User.monthly_vless_bytes with monthly cycle rotation.

    Returns:
        tuple[total_delta, delta_down, delta_up]
    """
    now = now or now_utc()
    str_server_id = str(server_id)
    str_epoch = str(node_epoch)

    sub = await lock_subscription_with_user(session, subscription_id)
    if not sub:
        return 0, 0, 0

    if expected_uuid and sub.uuid != expected_uuid and client_uuid != expected_uuid:
        return 0, 0, 0

    snap_key = f"{str_server_id}:{client_uuid}" if client_uuid else str_server_id
    snapshots = dict(sub.node_traffic_snapshots or {})
    prev_snap = snapshots.get(snap_key) or (snapshots.get(str_server_id) if not client_uuid else None)

    prev_epoch = prev_snap.get("epoch") if isinstance(prev_snap, dict) else None
    prev_up = int(prev_snap.get("up", 0)) if isinstance(prev_snap, dict) else 0
    prev_down = int(prev_snap.get("down", 0)) if isinstance(prev_snap, dict) else 0

    current_up = max(0, int(raw_uplink or 0))
    current_down = max(0, int(raw_downlink or 0))

    if snap_key not in snapshots:
        has_prior_snapshots = any(k != "_fresh" for k in snapshots)
        has_zero_usage = (sub.traffic_up or 0) + (sub.traffic_down or 0) == 0
        if snapshots.get("_fresh") or has_prior_snapshots or has_zero_usage:
            delta_up = current_up
            delta_down = current_down
        else:
            delta_up = 0
            delta_down = 0
    elif prev_epoch != str_epoch:
        delta_up = current_up
        delta_down = current_down
    else:
        delta_up = (current_up - prev_up) if current_up >= prev_up else current_up
        delta_down = (current_down - prev_down) if current_down >= prev_down else current_down

    delta_up = max(0, delta_up)
    delta_down = max(0, delta_down)
    total_delta = delta_up + delta_down

    if (
        total_delta > 0
        or prev_epoch != str_epoch
        or prev_up != current_up
        or prev_down != current_down
        or snap_key not in snapshots
    ):
        sub.traffic_up = (sub.traffic_up or 0) + delta_up
        sub.traffic_down = (sub.traffic_down or 0) + delta_down
        snapshots.pop("_fresh", None)
        snapshots[snap_key] = {
            "epoch": str_epoch,
            "up": current_up,
            "down": current_down,
            "updated_at": int(now.timestamp()),
        }
        sub.node_traffic_snapshots = snapshots
        flag_modified(sub, "node_traffic_snapshots")

        if total_delta > 0 and sub.user_id:
            current_cycle = now.strftime("%Y-%m")
            await session.execute(
                update(User)
                .where(User.id == sub.user_id)
                .values(
                    total_traffic_bytes=User.total_traffic_bytes + total_delta,
                    monthly_vless_bytes=case(
                        (User.traffic_cycle > current_cycle, User.monthly_vless_bytes),
                        (User.traffic_cycle == current_cycle, func.coalesce(User.monthly_vless_bytes, 0) + total_delta),
                        else_=total_delta,
                    ),
                    monthly_wi_bytes=case(
                        (User.traffic_cycle > current_cycle, User.monthly_wi_bytes),
                        (User.traffic_cycle == current_cycle, func.coalesce(User.monthly_wi_bytes, 0)),
                        else_=0,
                    ),
                    traffic_cycle=case(
                        (User.traffic_cycle > current_cycle, User.traffic_cycle),
                        else_=current_cycle,
                    ),
                )
            )
        await session.flush()

    return total_delta, delta_down, delta_up




