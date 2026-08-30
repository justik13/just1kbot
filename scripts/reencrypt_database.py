"""CLI script to re-encrypt all encrypted fields in the database with the primary key.

Usage:
    python -m scripts.reencrypt_database [--yes] [--force]

Operational contract:
    DB_ENCRYPTION_KEY in .env must be set to the NEW primary encryption key.
    DB_ENCRYPTION_KEYS (optional) should list old key(s) to allow decrypting existing data:
    DB_ENCRYPTION_KEY='<NEW_PRIMARY_KEY>'
    DB_ENCRYPTION_KEYS='<OLD_KEY_1>,<OLD_KEY_2>'

Safety:
    The bot must be stopped (docker compose stop bot) or maintenance mode must
    be enabled while rotating keys. When MaintenanceMode is explicitly OFF the
    script aborts; `--force` is the only conscious bypass. Interactive
    confirmation is required unless `--yes` is passed.
"""

import asyncio
import logging
import sys

from cryptography.fernet import Fernet
from sqlalchemy import select, text
from sqlalchemy.orm.attributes import flag_modified

from config.settings import get_settings
from database.connection import session_scope
from database.models import APIOperation, Server, VPNProfile

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("reencrypt")

BATCH_SIZE = 100


async def reencrypt_all(*, force: bool = False) -> None:
    logger.info("Starting database re-encryption with primary key...")

    # Hard safety guard: rotation must not run against a live writer. When the
    # MaintenanceMode row explicitly says maintenance is OFF, abort unless the
    # operator passed --force. A missing row (fresh/test schema) cannot be
    # verified and only produces a warning.
    from database.models import MaintenanceMode

    async with session_scope() as session:
        maintenance_enabled = await session.scalar(
            select(MaintenanceMode.is_enabled).where(MaintenanceMode.id == 1)
        )
    if maintenance_enabled is False:
        if force:
            logger.warning(
                "Maintenance mode is OFF; proceeding because --force was given. "
                "Rows written by a running bot during rotation may keep the old key."
            )
        else:
            raise RuntimeError(
                "Maintenance mode is OFF. Stop the bot container "
                "(docker compose stop bot) or enable maintenance mode first; "
                "re-run with --force to consciously bypass this guard."
            )
    elif maintenance_enabled is None:
        logger.warning(
            "MaintenanceMode row not found (fresh/test schema?); "
            "proceeding without a maintenance check."
        )

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

    # 3. Re-encrypt API Operations in batches
    total_operations = 0
    last_op_id = 0
    while True:
        async with session_scope() as session:
            ops = (
                await session.scalars(
                    select(APIOperation)
                    .where(
                        APIOperation.id > last_op_id,
                        APIOperation.api_key_snapshot.is_not(None),
                    )
                    .order_by(APIOperation.id)
                    .limit(BATCH_SIZE)
                )
            ).all()
            if not ops:
                break

            for op in ops:
                if op.api_key_snapshot:
                    op.api_key_snapshot = str(op.api_key_snapshot)
                    flag_modified(op, "api_key_snapshot")
                last_op_id = op.id
                total_operations += 1

            logger.info("Re-encrypted batch of API Operations up to ID %d (total: %d)", last_op_id, total_operations)

    # 4. Verification phase: ensure raw database ciphertext is encrypted strictly with the primary key
    logger.info("Verifying all records in database are encrypted strictly with the NEW primary key...")
    settings = get_settings()
    primary_fernet = Fernet(settings.DB_ENCRYPTION_KEY.encode("utf-8"))

    async with session_scope() as session:
        server_rows = (await session.execute(text("SELECT id, api_key FROM servers"))).all()
        for s_id, raw_api_key in server_rows:
            if raw_api_key:
                try:
                    primary_fernet.decrypt(raw_api_key.encode("utf-8"))
                except Exception as exc:
                    raise RuntimeError(
                        f"Server ID {s_id} is not encrypted with the new primary key: {exc}"
                    ) from exc

        profile_rows = (
            await session.execute(
                text("SELECT id, raw_config FROM vpn_profiles WHERE raw_config IS NOT NULL")
            )
        ).all()
        for p_id, raw_config in profile_rows:
            if raw_config:
                try:
                    primary_fernet.decrypt(raw_config.encode("utf-8"))
                except Exception as exc:
                    raise RuntimeError(
                        f"VPNProfile ID {p_id} is not encrypted with the new primary key: {exc}"
                    ) from exc

        op_rows = (
            await session.execute(
                text("SELECT id, api_key_snapshot FROM api_operations WHERE api_key_snapshot IS NOT NULL")
            )
        ).all()
        for o_id, raw_key in op_rows:
            if raw_key:
                try:
                    primary_fernet.decrypt(raw_key.encode("utf-8"))
                except Exception as exc:
                    raise RuntimeError(
                        f"APIOperation ID {o_id} is not encrypted with the new primary key: {exc}"
                    ) from exc

    logger.info(
        "Successfully finished database re-encryption & primary key verification: %d servers, %d VPN profiles, %d API Operations.",
        total_servers,
        total_profiles,
        total_operations,
    )


def main():
    # Operational contract: rotation must run with the bot stopped (or in
    # maintenance mode), otherwise the still-running process keeps writing
    # ciphertext under the old primary key and rows diverge between keys.
    argv = sys.argv[1:]
    force = "--force" in argv
    if "--yes" not in argv:
        print(__doc__ or "")
        print(
            "Убедитесь, что контейнер бота остановлен или включён режим техработ "
            "(docker compose stop bot). Продолжить? [y/N]: ",
            end="",
            flush=True,
        )
        try:
            answer = input().strip().lower()
        except EOFError:
            answer = ""
        if answer not in {"y", "yes"}:
            logger.info("Re-encryption aborted by operator.")
            sys.exit(1)
    try:
        asyncio.run(reencrypt_all(force=force))
    except Exception as exc:
        logger.exception("Database re-encryption failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
