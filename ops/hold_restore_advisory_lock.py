"""Hold the production-restore PostgreSQL advisory lock until stdin closes."""

from __future__ import annotations

import asyncio
import os
import sys

import asyncpg


LOCK_KEY = 0x4A31524F53544F52  # "J1ROSTOR", stable signed-int64-safe key.


async def main() -> None:
    connection = await asyncpg.connect(
        host=os.environ["PGHOST"],
        port=int(os.environ.get("PGPORT", "5432")),
        user=os.environ.get("PGUSER") or None,
        password=os.environ.get("PGPASSWORD") or None,
        database=os.environ.get("RESTORE_MAINTENANCE_DATABASE", "postgres"),
    )
    try:
        await connection.execute("SELECT pg_advisory_lock($1)", LOCK_KEY)
        backend_pid = await connection.fetchval("SELECT pg_backend_pid()")
        print(f"advisory_lock=acquired backend_pid={backend_pid}", flush=True)
        if os.environ.get("RESTORE_TEST_ADVISORY_EXIT_AFTER_ACQUIRE") == "true":
            return
        await asyncio.to_thread(sys.stdin.buffer.read)
        await connection.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
