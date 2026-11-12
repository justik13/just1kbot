import inspect

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AuditLog
from utils.datetime_helpers import now_utc


async def create_audit_log(
    session: AsyncSession,
    admin_id: int,
    action: str,
    target_type: str | None = None,
    target_id: int | None = None,
    details: str | None = None
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


async def clear_audit_logs(
    session: AsyncSession | None = None,
    older_than_days: int = 30,
    batch_size: int = 500,
    max_rounds: int = 100,
) -> int:
    """Purges audit log entries older than older_than_days in bounded chunked rounds.

    Transaction Boundaries:
    - session=None (Default / Production Cleanup Worker): Opens an independent `session_scope()`
      per batch of `batch_size` rows, immediately committing and releasing PostgreSQL row locks.
    - session=<AsyncSession>: Operates within the caller's transaction context using `session.flush()`.
    """
    import asyncio
    from datetime import timedelta

    from sqlalchemy import delete, select
    threshold = now_utc() - timedelta(days=older_than_days)

    if session is not None:
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

    from database.connection import session_scope
    total_deleted = 0
    for _ in range(max_rounds):
        async with session_scope() as sess:
            id_stmt = (
                select(AuditLog.id)
                .where(AuditLog.created_at < threshold)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
            res = await sess.execute(id_stmt)
            ids = list(res.scalars().all())
            if not ids:
                break
            del_stmt = delete(AuditLog).where(AuditLog.id.in_(ids))
            del_res = await sess.execute(del_stmt)
            total_deleted += int(del_res.rowcount or 0)
            if len(ids) < batch_size:
                break
        await asyncio.sleep(0.01)
    return total_deleted
