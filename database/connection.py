import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from alembic.config import Config
from alembic.command import upgrade
from config.settings import get_settings
from database.models import Tariff

_engine = None
_sessionmaker = None
_post_commit_background_tasks: set[asyncio.Task[None]] = set()

DEFAULT_TARIFFS = [
    {"name": "Базовый",  "description": "Телефон и ноутбук",                    "duration_days": 7,  "device_limit": 2,  "price_rub": 35, "sort_order": 10},
    {"name": "Базовый",  "description": "Телефон и ноутбук",                    "duration_days": 30, "device_limit": 2,  "price_rub": 90, "sort_order": 11},
    {"name": "Базовый",  "description": "Телефон и ноутбук",                    "duration_days": 90, "device_limit": 2,  "price_rub": 240, "sort_order": 12},
    {"name": "Семейный", "description": "Подключите всю семью",                 "duration_days": 30, "device_limit": 5, "price_rub": 180, "sort_order": 20},
    {"name": "Семейный", "description": "Подключите всю семью",                 "duration_days": 90, "device_limit": 5, "price_rub": 480, "sort_order": 21},
    {"name": "Pro",      "description": "Для офиса или большого парка гаджетов", "duration_days": 30, "device_limit": 10, "price_rub": 320, "sort_order": 30},
    {"name": "Pro",      "description": "Для офиса или большого парка гаджетов", "duration_days": 90, "device_limit": 10, "price_rub": 850, "sort_order": 31},
]


async def init_db():
    global _engine, _sessionmaker
    settings = get_settings()
    # P1 #8: Reduced pool_size from 50 to 10 for single-process deployment
    # 50 connections was excessive for a single bot process
    _engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        pool_size=10,
        max_overflow=10,
        pool_timeout=30,
        pool_pre_ping=True,
    )
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)

    # Seed default tariffs and maintenance mode (migrations are executed by docker-entrypoint.sh or alembic CLI)
    await _seed_default_data()

    logging.info("PostgreSQL database initialized successfully")
    return _engine, _sessionmaker


async def _run_alembic_migrations(database_url: str) -> None:
    """Run Alembic migrations on the database and seed default data."""
    try:
        alembic_cfg = Config(str(Path(__file__).parent.parent / "alembic.ini"))
        # ConfigParser (used internally by Alembic Config) treats '%' as
        # interpolation syntax. URL-encoded database passwords legitimately
        # contain '%' (for example %40 for '@'), so escape percent signs before
        # passing the URL into Alembic. ConfigParser restores them when the
        # option is read back by Alembic.
        alembic_cfg.set_main_option(
            "sqlalchemy.url",
            database_url.replace("%", "%%"),
        )

        # env.py uses asyncio.run(). Running Alembic in a worker thread avoids
        # nesting that event loop inside the bot's already-running loop. It also
        # keeps migrations on the configured asyncpg driver, so startup does not
        # require an undeclared psycopg2 dependency.
        await asyncio.to_thread(upgrade, alembic_cfg, "head")
        logging.info("Alembic migrations completed successfully.")

        await _seed_default_data()

    except Exception as e:
        logging.error("Failed to run Alembic migrations: %s", e, exc_info=True)
        raise


async def _seed_default_data() -> None:
    """Seed default tariffs and maintenance mode after migrations."""
    async with session_scope() as session:
        # Seed tariffs
        result = await session.execute(select(func.count(Tariff.id)))
        if result.scalar_one() == 0:
            for tariff in DEFAULT_TARIFFS:
                session.add(Tariff(**tariff, is_active=True))
            await session.commit()
            logging.info("Default tariffs seeded successfully.")

        # Seed maintenance mode
        from database.models import MaintenanceMode
        result = await session.execute(select(func.count(MaintenanceMode.id)))
        if result.scalar_one() == 0:
            session.add(
                MaintenanceMode(
                    id=1,
                    is_enabled=False,
                    message=(
                        "⚠️ Ведутся технические работы. "
                        "Некоторые действия временно недоступны. "
                        "Попробуйте позже."
                    ),
                )
            )
            await session.commit()
            logging.info("Maintenance mode singleton seeded.")


async def get_session() -> AsyncSession:
    if _sessionmaker is None:
        await init_db()
    return _sessionmaker()


_POST_COMMIT_TIMEOUT = 30.0


async def _safe_run_post_commit(
    task: Callable[[], Awaitable[None]],
) -> None:
    try:
        await asyncio.wait_for(task(), timeout=_POST_COMMIT_TIMEOUT)
    except asyncio.TimeoutError:
        logging.error(
            "Post-commit task timed out after %.0fs",
            _POST_COMMIT_TIMEOUT,
        )
    except Exception as e:
        logging.error("Post-commit task failed: %s", e, exc_info=True)


def _handle_task_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logging.error("Background task failed: %s", e, exc_info=True)


async def _run_post_commit_tasks(session: AsyncSession) -> None:
    tasks: list[Callable[[], Awaitable[None]]] = session.info.pop(
        "post_commit_tasks", []
    )
    if not tasks:
        return
    for task in tasks:
        background_task = asyncio.create_task(_safe_run_post_commit(task))
        _post_commit_background_tasks.add(background_task)
        background_task.add_done_callback(lambda t: (_post_commit_background_tasks.discard(t), _handle_task_result(t)))


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
    except Exception:
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
    await cancel_post_commit_tasks()
    if _engine:
        await _engine.dispose()
