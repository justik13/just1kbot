from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import SystemSetting
from utils.datetime_helpers import now_utc


async def get_system_setting(
    session: AsyncSession,
    key: str,
    default: Optional[str] = None,
) -> Optional[str]:
    stmt = select(SystemSetting.value).where(SystemSetting.key == key)
    result = await session.execute(stmt)
    val = result.scalar_one_or_none()
    return val if val is not None else default


async def set_system_setting(
    session: AsyncSession,
    key: str,
    value: Optional[str],
    updated_by: Optional[int] = None,
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
    return setting
