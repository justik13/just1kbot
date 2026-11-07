"""CLI script to re-encrypt all encrypted fields in the database with the primary key.

Usage:
    python -m scripts.reencrypt_database [--yes] [--force]

Operational contract:
    DB_ENCRYPTION_KEY in .env must be set to the NEW primary encryption key.
    DB_ENCRYPTION_KEYS (optional) should list old key(s) to allow decrypting existing data:
    DB_ENCRYPTION_KEY='<NEW_PRIMARY_KEY>'
    DB_ENCRYPTION_KEYS='<OLD_KEY_1>,<OLD_KEY_2>'

Safety:
    Contract: rotation proceeds ONLY when MaintenanceMode is explicitly
    enabled, or with an explicit `--force` EMERGENCY override (not a normal
    mode - it permits live writers during rotation). Stopping the bot
    container alone does NOT satisfy the guard — the check reads the
    MaintenanceMode row. Typical flow: stop bot → enable maintenance →
    rotate → verify → disable maintenance → start bot. Interactive
    confirmation is required unless `--yes` is passed. The maintenance state
    is sampled once at startup; the operator must keep the environment
    frozen (bot stopped, maintenance enabled) until the script exits.
    Rotation commits batch-by-batch and is RESUMABLE, not atomic: a mid-run
    failure leaves part of the rows on the new key. Keep old keys in
    DB_ENCRYPTION_KEYS until the verification phase passes, then re-run.
"""

import asyncio
import logging
import sys

from cryptography.fernet import Fernet
from sqlalchemy import select, text
from sqlalchemy.orm.attributes import flag_modified

from config.settings import get_settings
from database.connection import session_scope
from database.models import Server
from utils.logging_security import install_sensitive_data_filter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("reencrypt")
install_sensitive_data_filter(logger)

BATCH_SIZE = 100


async def reencrypt_all(*, force: bool = False) -> None:
    logger.info("Starting database re-encryption with primary key...")

    # Hard safety guard: rotation must not run against a live writer. Rotation
    # proceeds ONLY with maintenance explicitly enabled; every other state
    # (row missing, maintenance OFF) aborts unless the operator passed --force.
    from database.models import MaintenanceMode

    async with session_scope() as session:
        maintenance_enabled = await session.scalar(
            select(MaintenanceMode.is_enabled).where(MaintenanceMode.id == 1)
        )
    if maintenance_enabled is True:
        pass
    elif force:
        # SECURITY-SENSITIVE OPERATOR OVERRIDE: this allows live writers
        # during rotation; rows written under the old key become unreadable
        # once it is removed. Emergency use only - not a normal mode.
        logger.warning(
            "REENCRYPT --force OVERRIDE: maintenance mode state is %r; "
            "rotation is proceeding against the operational guard. Rows "
            "written by a running bot during rotation may keep the old key "
            "and become unreadable after the key is removed.",
            maintenance_enabled,
        )
    else:
        raise RuntimeError(
            "Maintenance mode must be enabled before rotation "
            f"(current state: {maintenance_enabled!r}). Enable maintenance "
            "mode first, then re-run; --force consciously bypasses this "
            "guard."
        )

    total_servers = 0

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

    logger.info(
        "Successfully finished database re-encryption & primary key verification: %d servers.",
        total_servers,
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
