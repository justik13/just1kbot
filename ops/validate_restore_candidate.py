"""Read-only application validation for a prepared restore candidate.

This command deliberately imports no bot entrypoint or service client.  It cannot
start polling/workers and performs no network operation other than PostgreSQL.
"""

from __future__ import annotations

import asyncio
import os
import re
from urllib.parse import urlsplit

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from database.models import Payment, PaymentFulfillmentOperation, PaymentProviderOperation, Server, User, VpnProfile


def _candidate_url() -> str:
    value = os.environ.get("RESTORE_CANDIDATE_DATABASE_URL", "")
    parsed = urlsplit(value.replace("postgresql+asyncpg://", "postgresql://", 1))
    name = parsed.path.removeprefix("/")
    if parsed.scheme not in {"postgresql", "postgres"} or not re.fullmatch(
        r"just1kbot_candidate_[A-Za-z0-9_]+", name
    ):
        raise SystemExit("candidate validation guard rejected database target")
    return value


async def validate() -> None:
    engine = create_async_engine(_candidate_url(), pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            for model in (User, VpnProfile, Payment, PaymentProviderOperation, PaymentFulfillmentOperation):
                await connection.execute(select(func.count()).select_from(model))
            # ORM type processing proves that the current key decrypts at least
            # one critical encrypted value when such a value exists.
            await connection.execute(select(VpnProfile.raw_config).where(VpnProfile.raw_config.is_not(None)).limit(1))
            await connection.execute(select(Server.api_key).where(Server.api_key.is_not(None)).limit(1))
            await connection.rollback()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(validate())
    print("candidate_validation=success")
