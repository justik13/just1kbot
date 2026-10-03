"""SQLite shims emulating PostgreSQL for the local simulation testbed."""
from __future__ import annotations

import aiosqlite
from datetime import datetime, timezone
from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import ARRAY, BIGINT, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.types import TypeDecorator

# --- 1. SQLITE COMPILER & POSTGRESQL EMULATION SHIMS ---

@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

@compiles(ARRAY, "sqlite")
def _compile_array_sqlite(type_, compiler, **kw):
    return "TEXT"

@compiles(BIGINT, "sqlite")
def _compile_bigint_sqlite(type_, compiler, **kw):
    return "INTEGER"

# Intercept aiosqlite connection creation to register PostgreSQL emulator functions
_orig_aiosqlite_connect = aiosqlite.connect

def _custom_aiosqlite_connect(*args, **kwargs):
    kwargs["check_same_thread"] = False
    conn = _orig_aiosqlite_connect(*args, **kwargs)
    orig_connect_coro = conn._connect

    async def patched_connect():
        c = await orig_connect_coro()
        await c.create_function("pg_advisory_xact_lock", 1, lambda x: 1)
        await c.create_function("pg_advisory_xact_lock", 2, lambda x, y: 1)
        await c.create_function("pg_advisory_lock", 1, lambda x: 1)
        await c.create_function("pg_advisory_lock", 2, lambda x, y: 1)
        await c.create_function("pg_advisory_unlock", 1, lambda x: 1)
        await c.create_function("pg_advisory_unlock", 2, lambda x, y: 1)
        await c.create_function("trunc", 1, lambda x: int(x) if x is not None else 0)
        await c.create_function("is_nonnegative_integer_json_array", 1, lambda x: 1)
        return c

    conn._connect = patched_connect
    return conn

aiosqlite.connect = _custom_aiosqlite_connect

# Force SQLite datetimes to be loaded as timezone-aware UTC objects
class UTCDateTime(TypeDecorator):
    impl = DateTime
    cache_ok = True

    def process_result_value(self, value, dialect):
        if value is not None:
            if isinstance(value, str):
                for fmt in (
                    "%Y-%m-%d %H:%M:%S.%f",
                    "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%dT%H:%M:%S.%f",
                    "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M:%S.%f%z",
                    "%Y-%m-%d %H:%M:%S%z",
                ):
                    try:
                        value = datetime.strptime(value, fmt)
                        break
                    except ValueError:
                        pass
            if isinstance(value, datetime) and value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
        return value
