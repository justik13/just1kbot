"""CLI script to inspect and migrate domain names in stored VPNProfile configurations.

Usage:
    python -m scripts.migrate_vpn_domain --old nl.just1k.best --new nl.just1k.pro [--apply]

Operational note:
    By default runs in dry-run mode (read-only audit).
    Pass --apply to commit changes to the database.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from database.connection import session_scope
from database.models import VPNProfile
from utils.logging_security import install_sensitive_data_filter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("migrate_vpn_domain")
install_sensitive_data_filter(logger)


async def migrate_domain(*, old_domain: str, new_domain: str, apply_changes: bool = False) -> None:
    old_domain = old_domain.strip().lower()
    new_domain = new_domain.strip().lower()

    if not old_domain or not new_domain:
        logger.error("Both --old and --new domains must be non-empty.")
        sys.exit(1)

    logger.info("Scanning vpn_profiles for references to '%s'...", old_domain)

    matched_count = 0
    updated_count = 0

    async with session_scope() as session:
        stmt = select(VPNProfile).where(VPNProfile.raw_config.is_not(None)).order_by(VPNProfile.id.asc())
        profiles = (await session.scalars(stmt)).all()

        logger.info("Found %d total profiles with raw_config.", len(profiles))

        for profile in profiles:
            cfg = profile.raw_config or ""
            if old_domain in cfg.lower():
                matched_count += 1
                logger.info(
                    "Profile ID %d (user_id=%d, server_id=%d, device='%s') contains '%s'",
                    profile.id,
                    profile.user_id,
                    profile.server_id,
                    profile.device_name,
                    old_domain,
                )
                if apply_changes:
                    # Case-insensitive replacement preserving casing if possible
                    import re
                    pattern = re.compile(re.escape(old_domain), re.IGNORECASE)
                    new_cfg = pattern.sub(new_domain, cfg)
                    profile.raw_config = new_cfg
                    flag_modified(profile, "raw_config")
                    updated_count += 1

        if apply_changes:
            await session.commit()
            logger.info("Successfully updated and re-encrypted %d profiles to '%s'.", updated_count, new_domain)
        else:
            logger.info(
                "Dry run complete: %d profiles matched '%s'. Re-run with --apply to update.",
                matched_count,
                old_domain,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate domain in encrypted VPN profiles.")
    parser.add_argument("--old", required=True, help="Old domain to find (e.g. nl.just1k.best)")
    parser.add_argument("--new", required=True, help="New domain to replace with (e.g. nl.just1k.pro)")
    parser.add_argument("--apply", action="store_true", help="Apply and commit changes to the database")

    args = parser.parse_args()
    asyncio.run(migrate_domain(old_domain=args.old, new_domain=args.new, apply_changes=args.apply))


if __name__ == "__main__":
    main()
