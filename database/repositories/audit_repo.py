import inspect
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import AuditLog
from typing import List, Optional
from utils.datetime_helpers import now_utc


async def create_audit_log(
    session: AsyncSession,
    admin_id: int,
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[int] = None,
    details: Optional[str] = None
) -> AuditLog:
    log = AuditLog(
        admin_id=admin_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details
    )
    res = session.add(log)
    if inspect.isawaitable(res):
        await res
    await session.flush()
    await session.refresh(log)
    return log


async def get_recent_audit_logs(session: AsyncSession, limit: int = 10) -> List[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_all_audit_logs_paginated(
    session: AsyncSession, offset: int = 0, limit: int = 10
) -> List[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_total_audit_logs_count(session: AsyncSession) -> int:
    stmt = select(func.count(AuditLog.id))
    return int(await session.scalar(stmt) or 0)


async def clear_audit_logs(
    session: AsyncSession,
    older_than_days: int = 30,
    batch_size: int = 500,
    max_rounds: int = 100,
) -> int:
    import asyncio
    from datetime import timedelta
    from sqlalchemy import delete, select
    threshold = now_utc() - timedelta(days=older_than_days)

    total_deleted = 0
    for _ in range(max_rounds):
        id_stmt = (
            select(AuditLog.id)
            .where(AuditLog.created_at < threshold)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        res = await session.execute(id_stmt)
        ids = list(res.scalars().all())
        if not ids:
            break
        del_stmt = delete(AuditLog).where(AuditLog.id.in_(ids))
        del_res = await session.execute(del_stmt)
        await session.flush()
        total_deleted += int(del_res.rowcount or 0)
        if len(ids) < batch_size:
            break
        await asyncio.sleep(0.01)
    return total_deleted



async def get_user_audit_logs(
    session: AsyncSession,
    user_id: int,
    telegram_id: Optional[int] = None,
    offset: int = 0,
    limit: int = 10,
) -> List[AuditLog]:
    from sqlalchemy import or_

    conditions = [func.lower(AuditLog.target_type) == "user"]
    if telegram_id is not None and telegram_id != user_id:
        conditions.append(
            or_(
                AuditLog.target_id == user_id,
                AuditLog.target_id == telegram_id,
            )
        )
    else:
        conditions.append(AuditLog.target_id == user_id)

    stmt = (
        select(AuditLog)
        .where(*conditions)
        .order_by(AuditLog.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_user_audit_logs_count(
    session: AsyncSession,
    user_id: int,
    telegram_id: Optional[int] = None,
) -> int:
    from sqlalchemy import or_

    conditions = [func.lower(AuditLog.target_type) == "user"]
    if telegram_id is not None and telegram_id != user_id:
        conditions.append(
            or_(
                AuditLog.target_id == user_id,
                AuditLog.target_id == telegram_id,
            )
        )
    else:
        conditions.append(AuditLog.target_id == user_id)

    stmt = (
        select(func.count(AuditLog.id))
        .where(*conditions)
    )
    return int(await session.scalar(stmt) or 0)
