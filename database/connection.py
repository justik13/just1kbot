import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config.settings import get_settings

logger = logging.getLogger(__name__)

_engine = None
_sessionmaker = None
_engine_loop: asyncio.AbstractEventLoop | None = None
_db_lock: asyncio.Lock | None = None
_db_lock_loop: asyncio.AbstractEventLoop | None = None
_post_commit_background_tasks: set[asyncio.Task[None]] = set()


def _get_db_lock() -> asyncio.Lock:
    global _db_lock, _db_lock_loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None
    if _db_lock is None or _db_lock_loop is not current_loop:
        _db_lock = asyncio.Lock()
        _db_lock_loop = current_loop
    return _db_lock


def _asyncpg_connect_args() -> dict:
    return {
        "timeout": 15,
        "server_settings": {
            "statement_timeout": "60000",
            "idle_in_transaction_session_timeout": "120000",
        },
    }


async def init_db():
    global _engine, _sessionmaker, _engine_loop
    lock = _get_db_lock()
    async with lock:
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if _engine is not None and _sessionmaker is not None and _engine_loop is current_loop:
            return _engine, _sessionmaker

        if _engine is not None and _engine_loop is not current_loop:
            _engine = None
            _sessionmaker = None

        settings = get_settings()
        # P1 #8: Reduced pool_size from 50 to 10 for single-process deployment
        # 50 connections was excessive for a single bot process
        #
        # P2 #6 (audit): pool_recycle bounds connection lifetime so a silently
        # dropped TCP peer cannot pin a pooled slot forever; statement_timeout
        # guarantees no runaway query holds row/advisory locks indefinitely.
        _engine = create_async_engine(
            settings.DATABASE_URL,
            echo=False,
            pool_size=20,
            max_overflow=15,
            pool_timeout=20,
            pool_pre_ping=True,
            pool_recycle=1800,
            connect_args=_asyncpg_connect_args(),
        )
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
        _engine_loop = current_loop

        logger.info("PostgreSQL database initialized successfully")
        return _engine, _sessionmaker


async def get_session() -> AsyncSession:
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None
    if _sessionmaker is None or (_engine_loop is not None and _engine_loop is not current_loop):
        await init_db()
    return _sessionmaker()


_POST_COMMIT_TIMEOUT = 30.0


async def _safe_run_post_commit(
    task: Callable[[], Awaitable[None]],
) -> None:
    try:
        await asyncio.wait_for(task(), timeout=_POST_COMMIT_TIMEOUT)
    except asyncio.TimeoutError:
        logger.error(
            "Post-commit task timed out after %.0fs",
            _POST_COMMIT_TIMEOUT,
        )
    except Exception as e:
        logger.error("Post-commit task failed: %s", e, exc_info=True)


def _handle_task_result(task: asyncio.Task) -> None:
    _post_commit_background_tasks.discard(task)
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error("Background task failed: %s", e, exc_info=True)


async def _run_post_commit_tasks(session: AsyncSession) -> None:
    tasks: list[Callable[[], Awaitable[None]]] = session.info.pop(
        "post_commit_tasks", []
    )
    if not tasks:
        return
    for task in tasks:
        background_task = asyncio.create_task(_safe_run_post_commit(task))
        _post_commit_background_tasks.add(background_task)
        background_task.add_done_callback(_handle_task_result)


def queue_post_commit_task(
    session: AsyncSession,
    task: Callable[[], Awaitable[None]],
) -> None:
    if "post_commit_tasks" not in session.info:
        session.info["post_commit_tasks"] = []
    session.info["post_commit_tasks"].append(task)


@asynccontextmanager
async def session_scope():
    session = await get_session()
    try:
        yield session
        await session.commit()
        await _run_post_commit_tasks(session)
    except (Exception, asyncio.CancelledError) as exc:
        if not isinstance(exc, asyncio.CancelledError):
            logger.debug(
                "Database transaction rolled back due to %s: %s",
                type(exc).__name__,
                exc,
            )
        await session.rollback()
        session.info.pop("post_commit_tasks", None)
        raise
    finally:
        await session.close()


async def cancel_post_commit_tasks() -> None:
    tasks = list(_post_commit_background_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def close_db():
    global _engine, _sessionmaker, _engine_loop, _db_lock, _db_lock_loop
    # Cancel post-commit tasks BEFORE acquiring the lock.
    # Post-commit tasks may call session_scope() -> get_session() -> init_db(),
    # which would try to acquire _db_lock. If close_db() held the lock while
    # awaiting cancel_post_commit_tasks(), that would cause a deadlock.
    await cancel_post_commit_tasks()
    lock = _get_db_lock()
    async with lock:
        if _engine:
            try:
                await _engine.dispose()
            except Exception:
                pass
            _engine = None
            _sessionmaker = None
            _engine_loop = None
    _db_lock = None
    _db_lock_loop = None
