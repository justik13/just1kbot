"""Background worker for automated Cloudflare Wildcard TLS renewal and deployment."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from config.settings import get_settings
from database.connection import session_scope
from services.certificate_service import WildcardCertificateManager

logger = logging.getLogger(__name__)

ROTATION_CHECK_INTERVAL = 43200.0  # 12 hours
STARTUP_DELAY = 15.0


async def certificate_rotator_loop(bot: Bot, shutdown_event: asyncio.Event) -> None:
    """Periodically check Wildcard TLS certificate validity and deploy to active nodes."""
    logger.info("Wildcard certificate rotator worker started")
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=STARTUP_DELAY)
        return
    except asyncio.TimeoutError:
        pass

    while not shutdown_event.is_set():
        settings = get_settings()
        if not settings.CLOUDFLARE_API_TOKEN or not settings.WILDCARD_DOMAIN:
            logger.debug("Cloudflare Wildcard is not configured in settings, skipping rotation check")
        else:
            try:
                logger.info("Running scheduled Wildcard TLS certificate check for %s...", settings.WILDCARD_DOMAIN)
                async with session_scope() as session:
                    results = await WildcardCertificateManager.deploy_to_all_active_nodes(session)
                    if results:
                        success_count = sum(1 for v in results.values() if v)
                        logger.info(
                            "Wildcard TLS sync completed: %d/%d servers updated successfully",
                            success_count,
                            len(results),
                        )
            except Exception as exc:
                logger.error("Unexpected error in certificate rotator loop: %s", exc, exc_info=True)

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=ROTATION_CHECK_INTERVAL)
            break
        except asyncio.TimeoutError:
            continue

    logger.info("Wildcard certificate rotator worker stopped")
