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

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

logger = getLogger("alembic.env")

# add your model's MetaData object here
# for 'autogenerate' support
from database.models import Base  # noqa: E402
from database import dispute_models as _dispute_models  # noqa: E402,F401
from database import refund_models as _refund_models  # noqa: E402,F401

target_metadata = Base.metadata

# Override sqlalchemy.url with DATABASE_URL from environment if available
database_url = os.getenv("DATABASE_URL")
if database_url:
    # Convert asyncpg URL to sync psycopg2 for Alembic if needed
    # Alembic can work with asyncpg directly in async mode
    config.set_main_option("sqlalchemy.url", database_url)
    # Log only the host and database name, not credentials
    from urllib.parse import urlparse
    parsed = urlparse(database_url)
    logger.info(f"Using database: {parsed.hostname}:{parsed.port or 5432}/{parsed.path.lstrip('/')}")

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Enable detection of type changes and server default changes
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

    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def do_run_migrations(connection) -> None:
    """Configure and execute migrations on a synchronous connection proxy."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    # Let the database driver handle connection errors naturally.
    # If DB is unavailable, Alembic will fail with a clear error and non-zero exit code.
    # This prevents silent failures where migrations are skipped but deploy appears successful.
    asyncio.run(run_migrations_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
