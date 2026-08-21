import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    result = await session.execute(stmt)
    val = result.scalar_one_or_none()

    _SETTINGS_CACHE[key] = (val, now)
    return val if val is not None else default


async def set_system_setting(
    session: AsyncSession,
    key: str,
    value: str | None,
    updated_by: int | None = None,
) -> SystemSetting:
    setting = await session.get(SystemSetting, key)
    if setting is None:
        setting = SystemSetting(
            key=key,
            value=value,
            updated_by=updated_by,
            updated_at=now_utc(),
        )
        session.add(setting)
    else:
        setting.value = value
        setting.updated_by = updated_by
        setting.updated_at = now_utc()

    await session.flush()
    _SETTINGS_CACHE[key] = (value, time.monotonic())
    return setting

