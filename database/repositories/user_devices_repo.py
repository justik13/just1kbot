"""Repository for unified HWID device management across Standard and White Internet."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User, UserDevice
from utils.datetime_helpers import now_utc

DEFAULT_HWID_TTL_HOURS = 48


def _extract_device_fields(device_info: dict | None) -> dict[str, str | None]:
    if not isinstance(device_info, dict):
        return {}
    mapping = {
        "device_name": ("device_name", "device_model", "model"),
        "platform": ("platform", "device_os"),
        "os_version": ("os_version", "ver_os"),
        "app_version": ("app_version", "app_ver"),
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
    ttl_hours: int = DEFAULT_HWID_TTL_HOURS,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return active devices ordered by BI priority (manual desc, then FIFO first_seen_at asc)."""
    now = now or now_utc()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(hours=ttl_hours)

    stmt = (
        select(UserDevice)
        .where(
            UserDevice.user_id == user_id,
            UserDevice.last_seen_at >= cutoff,
        )
        .order_by(
            UserDevice.bi_priority.desc(),
            UserDevice.first_seen_at.asc(),
            UserDevice.id.asc(),
        )
    )
    rows = (await session.scalars(stmt)).all()
    limit = max(0, int(wi_device_limit or 0))
    result: list[dict[str, Any]] = []
    for idx, dev in enumerate(rows):
        result.append(
            {
                "id": dev.id,
                "hwid": dev.hwid,
                "device_name": dev.device_name,
                "platform": dev.platform,
                "os_version": dev.os_version,
                "app_version": dev.app_version,
                "bi_priority": dev.bi_priority,
                "first_seen_at": dev.first_seen_at.isoformat() if dev.first_seen_at else None,
                "last_seen_at": dev.last_seen_at.isoformat() if dev.last_seen_at else None,
                "is_bi_assigned": idx < limit,
            }
        )
    return result


async def count_active_user_devices(
    session: AsyncSession,
    user_id: int,
    *,
    ttl_hours: int = DEFAULT_HWID_TTL_HOURS,
    now: datetime | None = None,
) -> int:
    """Count active devices for user within TTL window."""
    now = now or now_utc()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(hours=ttl_hours)
    stmt = select(func.count(UserDevice.id)).where(
        UserDevice.user_id == user_id,
        UserDevice.last_seen_at >= cutoff,
    )
    return int(await session.scalar(stmt) or 0)


async def register_user_device(
    session: AsyncSession,
    user_id: int,
    hwid: str,
    max_devices: int,
    *,
    ttl_hours: int = DEFAULT_HWID_TTL_HOURS,
    device_info: dict | None = None,
) -> tuple[bool, int, int]:
    """Atomically register or refresh a unified HWID device under user row lock."""
    clean_hwid = str(hwid or "").strip()[:128]
    effective_limit = max(1, int(max_devices or 1))
    if not clean_hwid:
        active_count = await count_active_user_devices(session, user_id, ttl_hours=ttl_hours)
        return False, active_count, effective_limit

    user = await session.scalar(
        select(User).where(User.id == user_id).with_for_update()
    )
    if user is None:
        return False, 0, effective_limit

    now = now_utc()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(hours=ttl_hours)

    # Prune expired devices so stale HWIDs never block slots
    await session.execute(
        delete(UserDevice).where(
            UserDevice.user_id == user_id,
            UserDevice.last_seen_at < cutoff,
        )
    )

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
        return True, len(devices), effective_limit

    if len(devices) >= effective_limit:
        return False, len(devices), effective_limit

    dev = UserDevice(
        user_id=user_id,
        hwid=clean_hwid,
        device_name=fields.get("device_name"),
        platform=fields.get("platform"),
        os_version=fields.get("os_version"),
        app_version=fields.get("app_version"),
        bi_priority=0,
        first_seen_at=now,
        last_seen_at=now,
    )
    session.add(dev)
    await session.flush()
    return True, len(devices) + 1, effective_limit


async def delete_user_device(
    session: AsyncSession,
    user_id: int,
    hwid: str,
) -> bool:
    """Delete a specific HWID device, immediately freeing its slot."""
    clean_hwid = str(hwid or "").strip()[:128]
    if not clean_hwid:
        return False
    res = await session.execute(
        delete(UserDevice).where(
            UserDevice.user_id == user_id,
            UserDevice.hwid == clean_hwid,
        )
    )
    await session.flush()
    return (res.rowcount or 0) > 0


async def set_device_bi_priority(
    session: AsyncSession,
    user_id: int,
    hwid: str,
    priority: int,
) -> bool:
    """Set manual BI priority for a specific HWID device (higher = higher priority)."""
    clean_hwid = str(hwid or "").strip()[:128]
    if not clean_hwid:
        return False
    dev = await session.scalar(
        select(UserDevice).where(
            UserDevice.user_id == user_id,
            UserDevice.hwid == clean_hwid,
        )
    )
    if dev is None:
        return False
    dev.bi_priority = int(priority)
    await session.flush()
    return True


__all__ = [
    "count_active_user_devices",
    "delete_user_device",
    "list_user_devices",
    "register_user_device",
    "set_device_bi_priority",
]
