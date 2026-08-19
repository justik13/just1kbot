"""CLI script to re-encrypt all encrypted fields in the database with the primary key.

Usage:
    python -m scripts.reencrypt_database

Requirements:
    DB_ENCRYPTION_KEYS in .env must be set with the new primary key first,
    followed by the old key(s):
    DB_ENCRYPTION_KEYS='<NEW_KEY>,<OLD_KEY>'
"""

import asyncio
import logging
import sys

from sqlalchemy import select

from database.connection import session_scope
from database.models import Server, VPNProfile

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("reencrypt")


async def reencrypt_all() -> None:
    logger.info("Starting database re-encryption with primary key...")

    async with session_scope() as session:
        # 1. Re-encrypt servers
        servers = (await session.scalars(select(Server))).all()
        logger.info("Found %d servers to re-encrypt", len(servers))
        for server in servers:
            # Accessing and re-assigning triggers process_result_value + process_bind_param
            if server.api_key:
                server.api_key = str(server.api_key)
            if server.country_flag:
                server.country_flag = str(server.country_flag)

        # 2. Re-encrypt VPN profiles
        profiles = (
            await session.scalars(
                select(VPNProfile).where(VPNProfile.raw_config.is_not(None))
            )
        ).all()
        logger.info("Found %d VPN profiles with configs to re-encrypt", len(profiles))
        for profile in profiles:
            if profile.raw_config:
                profile.raw_config = str(profile.raw_config)

        await session.flush()
        logger.info(
            "Successfully re-encrypted %d servers and %d profiles.",
            len(servers),
            len(profiles),
        )


def main():
    try:
        asyncio.run(reencrypt_all())
    except Exception as exc:
        logger.exception("Database re-encryption failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
