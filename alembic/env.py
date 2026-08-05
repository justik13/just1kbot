from logging.config import fileConfig
import asyncio
import os
from logging import getLogger
from dotenv import load_dotenv

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Load environment variables from .env file
load_dotenv()

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

logger = getLogger("alembic.env")

from database.models import Base  # noqa: E402
from database import dispute_models as _dispute_models  # noqa: E402,F401
from database import refund_models as _refund_models  # noqa: E402,F401

target_metadata = Base.metadata

database_url = os.getenv("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)
    from urllib.parse import urlparse
    parsed = urlparse(database_url)
    logger.info(
        f"Using database: {parsed.hostname}:{parsed.port or 5432}/{parsed.path.lstrip('/')}"
    )


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_async() -> None:
    """Run migrations using async engine for PostgreSQL."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(
            lambda sync_connection: context.configure(
                connection=sync_connection,
                target_metadata=target_metadata,
                compare_type=True,
                compare_server_default=True,
            )
        )
        async with connection.begin():
            await connection.run_sync(context.run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_migrations_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
