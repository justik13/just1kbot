"""Shared helpers for Postgres-backed tests.

Single source of truth for the full-table TRUNCATE used to isolate suites.
The table list is derived from SQLAlchemy metadata so new tables are picked
up automatically; CASCADE handles FK ordering in one statement.
"""
import os

from sqlalchemy import text

DB = os.getenv("TEST_DATABASE_URL")

_EXCLUDED_TABLES = {"alembic_version"}


def build_truncate_sql() -> str:
    from database.models import Base

    tables = sorted(
        table.name
        for table in Base.metadata.sorted_tables
        if table.name not in _EXCLUDED_TABLES
    )
    return "TRUNCATE " + ", ".join(tables) + " RESTART IDENTITY CASCADE"


TRUNCATE_SQL = build_truncate_sql()


async def truncate_all(conn) -> None:
    await conn.execute(text(TRUNCATE_SQL))
