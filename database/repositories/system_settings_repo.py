import time

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import queue_post_commit_task
from database.models import SystemSetting
from utils.datetime_helpers import now_utc

_SETTINGS_CACHE: dict[str, tuple[str | None, float]] = {}
CACHE_TTL_SECONDS = 60.0


async def get_system_setting(
    session: AsyncSession,
    key: str,
    default: str | None = None,
) -> str | None:
    now = time.monotonic()
    if key in _SETTINGS_CACHE:
        val, cached_at = _SETTINGS_CACHE[key]
        if now - cached_at < CACHE_TTL_SECONDS:
            return val if val is not None else default

    stmt = select(SystemSetting.value).where(SystemSetting.key == key)
    res = await session.execute(stmt)
    val = res.scalar_one_or_none()

    if val is not None and not isinstance(val, str):
        val = None

    _SETTINGS_CACHE[key] = (val, now)
    return val if val is not None else default


async def set_system_setting(
    session: AsyncSession,
    key: str,
    value: str | None,
    updated_by: int | None = None,
) -> SystemSetting:
    ts = now_utc()
    stmt = (
        pg_insert(SystemSetting)
        .values(key=key, value=value, updated_by=updated_by, updated_at=ts)
        .on_conflict_do_update(
            index_elements=[SystemSetting.key],
            set_={"value": value, "updated_by": updated_by, "updated_at": ts},
        )
        .returning(SystemSetting)
    )
    setting = (await session.execute(stmt)).scalar_one()
    _SETTINGS_CACHE.pop(key, None)

    async def _invalidate_after_commit() -> None:
        _SETTINGS_CACHE.pop(key, None)

    queue_post_commit_task(session, _invalidate_after_commit)
    return setting
