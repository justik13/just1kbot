"""Repository for unified HWID device management across Standard and White Internet."""

from __future__ import annotations

from datetime import timedelta, timezone
import logging
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import DeviceRegistrationEvent, User, UserDevice
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)


def calculate_daily_churn_limit(effective_limit: int) -> int:
    """Allowed distinct HWID registrations per rolling 24 hours.

    Formula: effective_limit + max(2, round(effective_limit * 0.5))
    - limit 1 (WI base): 1 + 2 = 3
    - limit 2 (Standard basic): 2 + 2 = 4
    - limit 5 (Family): 5 + 3 = 8
    - limit 10 (Pro): 10 + 5 = 15
    """
    limit = max(1, int(effective_limit or 1))
    extra = max(2, int(round(limit * 0.5)))
    return limit + extra


def _extract_device_fields(device_info: dict | None) -> dict[str, str | None]:
    if not isinstance(device_info, dict):
        return {}
    mapping = {
        "device_name": ("device_name", "device_model", "model"),
        "platform": ("platform", "device_os", "os"),
        "os_version": ("os_version", "ver_os"),
        "app_version": ("app_version", "app_ver", "client"),
    }
    out: dict[str, str | None] = {}
    for target, keys in mapping.items():
        for k in keys:
            val = device_info.get(k)
            if isinstance(val, str) and val.strip():
                limit = 128 if target == "device_name" else 64
                out[target] = val.strip()[:limit]
                break
    return out


async def list_user_devices(
    session: AsyncSession,
    user_id: int,
    *,
    wi_device_limit: int = 0,
) -> list[dict[str, Any]]:
    """Return bound devices ordered by BI priority (manual desc, then FIFO first_seen_at asc)."""
    stmt = (
        select(UserDevice)
        .where(UserDevice.user_id == user_id)
        .order_by(
            UserDevice.bi_priority.desc(),
            UserDevice.first_seen_at.asc(),
            UserDevice.id.asc(),
        )
    )
    rows = (await session.scalars(stmt)).all()
    limit = max(0, int(wi_device_limit or 0))
    assigned_count = 0
    result: list[dict[str, Any]] = []
    for dev in rows:
        is_assigned = False
        if assigned_count < limit and (dev.bi_priority or 0) >= 0:
            is_assigned = True
            assigned_count += 1
        result.append(
            {
                "id": dev.id,
                "hwid": dev.hwid,
                "device_uuid": dev.device_uuid,
                "device_name": dev.device_name,
                "platform": dev.platform,
                "os_version": dev.os_version,
                "app_version": dev.app_version,
                "bi_priority": dev.bi_priority,
                "first_seen_at": dev.first_seen_at.isoformat() if dev.first_seen_at else None,
                "last_seen_at": dev.last_seen_at.isoformat() if dev.last_seen_at else None,
                "is_bi_assigned": is_assigned,
            }
        )
    return result


async def count_active_user_devices(
    session: AsyncSession,
    user_id: int,
) -> int:
    """Count bound devices for user."""
    stmt = select(func.count(UserDevice.id)).where(UserDevice.user_id == user_id)
    return int(await session.scalar(stmt) or 0)


async def register_user_device(
    session: AsyncSession,
    user_id: int,
    hwid: str,
    max_devices: int,
    *,
    device_info: dict | None = None,
) -> tuple[bool, bool, int, int, str | None]:
    """Atomically register or refresh a unified HWID device under user row lock.

    Returns:
        tuple[allowed_hwid, is_new_device, active_count, effective_limit, block_reason]
        where block_reason is None, 'empty_hwid', 'user_not_found', 'slot_limit', or 'churn_limit'.
    """
    clean_hwid = str(hwid or "").strip()[:128]
    effective_limit = max(1, int(max_devices or 1))
    if not clean_hwid:
        active_count = await count_active_user_devices(session, user_id)
        return False, False, active_count, effective_limit, "empty_hwid"

    user = await session.scalar(
        select(User).where(User.id == user_id).with_for_update()
    )
    if user is None:
        return False, False, 0, effective_limit, "user_not_found"

    now = now_utc()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    devices = (
        await session.scalars(
            select(UserDevice)
            .where(UserDevice.user_id == user_id)
            .order_by(UserDevice.first_seen_at.asc(), UserDevice.id.asc())
        )
    ).all()

    fields = _extract_device_fields(device_info)
    existing = next((d for d in devices if d.hwid == clean_hwid), None)
    if existing is not None:
        changed = any(
            getattr(existing, k) != v for k, v in fields.items() if v is not None
        )
        if changed or (now - existing.last_seen_at).total_seconds() >= 900:
            existing.last_seen_at = now
            for k, v in fields.items():
                if v is not None:
                    setattr(existing, k, v)
            await session.flush()
        return True, False, len(devices), effective_limit, None

    if len(devices) >= effective_limit:
        return False, False, len(devices), effective_limit, "slot_limit"

    # Anti-abuse: check rolling 24h churn limit for brand-new HWIDs
    churn_limit = calculate_daily_churn_limit(effective_limit)
    churn_cutoff = now - timedelta(hours=24)
    await session.execute(
        delete(DeviceRegistrationEvent).where(
            DeviceRegistrationEvent.user_id == user_id,
            DeviceRegistrationEvent.created_at < churn_cutoff,
        )
    )
    recent_event_hwids = set(
        (
            await session.scalars(
                select(DeviceRegistrationEvent.hwid).where(
                    DeviceRegistrationEvent.user_id == user_id,
                    DeviceRegistrationEvent.created_at >= churn_cutoff,
                )
            )
        ).all()
    )
    active_24h_hwids = {
        d.hwid
        for d in devices
        if d.created_at and (now - d.created_at).total_seconds() <= 86400
    }
    all_24h_hwids = active_24h_hwids | recent_event_hwids
    if clean_hwid not in all_24h_hwids and len(all_24h_hwids) >= churn_limit:
        logger.warning(
            "User %s exceeded daily device churn limit (%s in 24h, limit=%s)",
            user_id,
            len(all_24h_hwids),
            churn_limit,
        )
        return False, False, len(devices), effective_limit, "churn_limit"

    dev = UserDevice(
        user_id=user_id,
        hwid=clean_hwid,
        device_uuid=str(uuid4()),
        device_name=fields.get("device_name"),
        platform=fields.get("platform"),
        os_version=fields.get("os_version"),
        app_version=fields.get("app_version"),
        bi_priority=0,
        first_seen_at=now,
        last_seen_at=now,
    )
    session.add(dev)
    if clean_hwid not in recent_event_hwids:
        session.add(
            DeviceRegistrationEvent(
                user_id=user_id,
                hwid=clean_hwid,
                created_at=now,
            )
        )
    await session.flush()
    return True, True, len(devices) + 1, effective_limit, None


async def delete_user_device(
    session: AsyncSession,
    user_id: int,
    hwid: str,
) -> UserDevice | None:
    """Delete a specific HWID device, returning the deleted device record."""
    clean_hwid = str(hwid or "").strip()[:128]
    if not clean_hwid:
        return None
    await session.scalar(select(User.id).where(User.id == user_id).with_for_update())
    dev = await session.scalar(
        select(UserDevice)
        .where(
            UserDevice.user_id == user_id,
            UserDevice.hwid == clean_hwid,
        )
        .with_for_update()
    )
    if dev is None:
        return None
    await session.delete(dev)
    await session.flush()
    return dev


async def delete_all_user_devices(
    session: AsyncSession,
    user_id: int,
) -> list[UserDevice]:
    """Delete all HWID devices for user, returning the deleted device records."""
    await session.scalar(select(User.id).where(User.id == user_id).with_for_update())
    stmt = (
        select(UserDevice)
        .where(UserDevice.user_id == user_id)
        .with_for_update()
    )
    rows = (await session.scalars(stmt)).all()
    if not rows:
        return []
    await session.execute(
        delete(UserDevice).where(UserDevice.user_id == user_id)
    )
    await session.flush()
    return list(rows)


async def get_user_device_by_hwid(
    session: AsyncSession,
    user_id: int,
    hwid: str,
) -> UserDevice | None:
    """Fetch user device by exact HWID under user_id."""
    clean_hwid = str(hwid or "").strip()[:128]
    if not clean_hwid:
        return None
    return await session.scalar(
        select(UserDevice).where(
            UserDevice.user_id == user_id,
            UserDevice.hwid == clean_hwid,
        )
    )


async def get_active_device_uuids(
    session: AsyncSession,
    user_id: int,
) -> list[str]:
    """Return all bound device UUIDs for user."""
    stmt = select(UserDevice.device_uuid).where(UserDevice.user_id == user_id)
    rows = (await session.scalars(stmt)).all()
    return [u for u in rows if isinstance(u, str) and u.strip()]


async def set_device_bi_priority(
    session: AsyncSession,
    user_id: int,
    hwid: str,
    priority: int,
    *,
    replace_hwid: str | None = None,
) -> bool:
    """Set manual BI priority for a specific HWID device (priority > 0 enables, <= 0 disables)."""
    clean_hwid = str(hwid or "").strip()[:128]
    if not clean_hwid:
        return False
    await session.scalar(select(User.id).where(User.id == user_id).with_for_update())
    dev = await session.scalar(
        select(UserDevice).where(
            UserDevice.user_id == user_id,
            UserDevice.hwid == clean_hwid,
        ).with_for_update()
    )
    if dev is None:
        return False

    if replace_hwid:
        clean_replace = str(replace_hwid).strip()[:128]
        if clean_replace and clean_replace != clean_hwid:
            rep_dev = await session.scalar(
                select(UserDevice).where(
                    UserDevice.user_id == user_id,
                    UserDevice.hwid == clean_replace,
                )
            )
            if rep_dev is not None:
                rep_dev.bi_priority = -1

    if priority > 0:
        max_prio = await session.scalar(
            select(func.coalesce(func.max(UserDevice.bi_priority), 0)).where(
                UserDevice.user_id == user_id
            )
        )
        dev.bi_priority = max(int(priority), int(max_prio or 0) + 10)
    else:
        dev.bi_priority = -1
    await session.flush()
    return True


__all__ = [
    "calculate_daily_churn_limit",
    "count_active_user_devices",
    "delete_all_user_devices",
    "delete_user_device",
    "get_active_device_uuids",
    "get_user_device_by_hwid",
    "list_user_devices",
    "register_user_device",
    "set_device_bi_priority",
]
