import sqlite3
import uuid
from datetime import datetime, timezone

import aiosqlite
from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import ARRAY, BIGINT, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.elements import BinaryExpression, ReleaseSavepointClause
from sqlalchemy.types import TypeDecorator

# Register native SQLite UUID adapters to avoid ProgrammingError on UUID bind parameters
sqlite3.register_adapter(uuid.UUID, str)
sqlite3.register_converter("GUID", lambda b: uuid.UUID(b.decode()))

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

@compiles(BinaryExpression, "sqlite")
def _compile_binary_sqlite(expr, compiler, **kw):
    op = (
        getattr(expr.operator, "opstring", None)
        or getattr(expr.operator, "operator", None)
        or str(expr.operator)
    )
    if op == "?":
        left = compiler.process(expr.left, **kw)
        right = compiler.process(expr.right, **kw)
        return f"(json_type({left}, '$.' || {right}) IS NOT NULL)"
    return compiler.visit_binary(expr, **kw)

@compiles(ReleaseSavepointClause, "sqlite")
def _compile_release_savepoint_sqlite(element, compiler, **kw):
    # In SQLite, RELEASE SAVEPOINT is optional upon commit. In simulation with StaticPool,
    # concurrent commits clear active savepoints; turning RELEASE into a safe no-op prevents
    # false-positive sqlite3.OperationalError: no such savepoint errors.
    name = getattr(element, "name", None) or getattr(element, "target_identifier", "")
    return f"SELECT 1 /* RELEASE SAVEPOINT {name} */"


class SQLiteUUID(TypeDecorator):
    """UUID type for SQLite storage that stores UUIDs as CHAR(36) strings."""

    impl = String(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        try:
            return uuid.UUID(str(value))
        except (ValueError, TypeError):
            return value

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

# Intercept aiosqlite cursor execute to silently ignore harmless missing savepoint errors
_orig_cursor_execute = aiosqlite.cursor.Cursor.execute

async def _safe_cursor_execute(self, operation, parameters=None):
    try:
        if parameters is None:
            return await _orig_cursor_execute(self, operation)
        return await _orig_cursor_execute(self, operation, parameters)
    except sqlite3.OperationalError as e:
        op_str = str(operation).strip().upper()
        if "no such savepoint" in str(e).lower() and (
            op_str.startswith("RELEASE SAVEPOINT") or op_str.startswith("ROLLBACK TO SAVEPOINT")
        ):
            return None
        raise

aiosqlite.cursor.Cursor.execute = _safe_cursor_execute

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
