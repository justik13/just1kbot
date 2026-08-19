"""CLI script to re-encrypt all encrypted fields in the database with the primary key.

Usage:
    python -m scripts.reencrypt_database

Requirements:
    DB_ENCRYPTION_KEY in .env must be set to the NEW primary encryption key.
    DB_ENCRYPTION_KEYS (optional) should list old key(s) to allow decrypting existing data:
    DB_ENCRYPTION_KEY='<NEW_PRIMARY_KEY>'
    DB_ENCRYPTION_KEYS='<OLD_KEY_1>,<OLD_KEY_2>'
"""

import asyncio
import logging
import sys

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from database.connection import session_scope
from database.models import Server, VPNProfile

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("reencrypt")

BATCH_SIZE = 100


async def reencrypt_all() -> None:
    logger.info("Starting database re-encryption with primary key...")

    total_servers = 0
    total_profiles = 0

    # 1. Re-encrypt servers in batches
    last_server_id = 0
    while True:
        async with session_scope() as session:
            servers = (
                await session.scalars(
                    select(Server)
                    .where(Server.id > last_server_id)
                    .order_by(Server.id)
                    .limit(BATCH_SIZE)
                )
            ).all()
            if not servers:
                break

            for server in servers:
                if server.api_key:
                    server.api_key = str(server.api_key)
                    flag_modified(server, "api_key")
                last_server_id = server.id
                total_servers += 1

            # session_scope() automatically commits on block exit
            logger.info("Re-encrypted batch of servers up to ID %d (total: %d)", last_server_id, total_servers)

    # 2. Re-encrypt VPN profiles in batches
    last_profile_id = 0
    while True:
        async with session_scope() as session:
            profiles = (
                await session.scalars(
                    select(VPNProfile)
                    .where(
                        VPNProfile.id > last_profile_id,
                        VPNProfile.raw_config.is_not(None),
                    )
                    .order_by(VPNProfile.id)
                    .limit(BATCH_SIZE)
                )
            ).all()
            if not profiles:
                break

            for profile in profiles:
                if profile.raw_config:
                    profile.raw_config = str(profile.raw_config)
                    flag_modified(profile, "raw_config")
                last_profile_id = profile.id
                total_profiles += 1

            # session_scope() automatically commits on block exit
            logger.info("Re-encrypted batch of VPN profiles up to ID %d (total: %d)", last_profile_id, total_profiles)

    # 3. Verification phase: ensure every record decrypts cleanly
    logger.info("Verifying all records decrypt cleanly...")
    async with session_scope() as session:
        verified_servers = (await session.scalars(select(Server))).all()
        for s in verified_servers:
            if s.api_key:
                _ = len(s.api_key)

        verified_profiles = (
            await session.scalars(
                select(VPNProfile).where(VPNProfile.raw_config.is_not(None))
            )
        ).all()
        for p in verified_profiles:
            if p.raw_config:
                _ = len(p.raw_config)

    logger.info(
        "Successfully finished database re-encryption & verification: %d servers, %d VPN profiles.",
        total_servers,
        total_profiles,
    )


def main():
    try:
        asyncio.run(reencrypt_all())
    except Exception as exc:
        logger.exception("Database re-encryption failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
