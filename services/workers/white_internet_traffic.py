"""Traffic sync worker collecting stats from Xray nodes and deducting from grant ledgers."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.enums import ServerHealthState
from database.connection import session_scope
from database.models import Server, User, WhiteInternetSubscription
from database.repositories import white_internet_repo
from services.xray_node_client import XrayNodeClient
from utils.datetime_helpers import now_utc

logger = logging.getLogger("WhiteInternetTraffic")

TRAFFIC_SYNC_INTERVAL_SECONDS = 60.0


class WhiteInternetTrafficWorker:
    """Worker polling Xray Origin and updating the grant ledger."""

    def __init__(self, bot: Bot | None = None, node_client: XrayNodeClient | None = None):
        self.bot = bot
        self.client = node_client or XrayNodeClient()

    async def run_traffic_cycle(self, session: AsyncSession) -> int:
        now = now_utc()
        stmt = select(Server).where(
            Server.api_url.is_not(None),
            Server.health_state.in_([ServerHealthState.ONLINE, ServerHealthState.WAITING_CONFIRMATION]),
        )
        servers = (await session.execute(stmt)).scalars().all()
        total_processed = 0

        for server in servers:
            # Never treat an arbitrary server as an accounting source.
            if "xray_origin" not in (server.capabilities or []):
                continue

            node_epoch, users_stats = await self.client.get_traffic_snapshot(
                server.api_url, server.api_key
            )
            if not node_epoch or users_stats is None:
                continue

            if server.xray_instance_epoch != node_epoch:
                server.xray_instance_epoch = node_epoch
                await session.flush()

            for client_uuid, stats in users_stats.items():
                uplink = max(int(stats.get("uplink", 0)), 0)
                downlink = max(int(stats.get("downlink", 0)), 0)

                sub_meta = await session.scalar(
                    select(WhiteInternetSubscription).where(
                        WhiteInternetSubscription.uuid == client_uuid,
                        WhiteInternetSubscription.origin_node_id == server.id,
                    )
                )
                if sub_meta is None:
                    continue

                sub = await white_internet_repo.get_subscription_with_lock(session, sub_meta.id)
                if sub is None:
                    continue

                # Xray counters are monotonic within one process generation.
                # A generation change resets counters; the current snapshot is
                # therefore the first delta of the new generation.
                if sub.traffic_stats_epoch != node_epoch:
                    delta_up = uplink
                    delta_down = downlink
                    delta = delta_up + delta_down
                    sub.traffic_stats_epoch = node_epoch
                else:
                    delta_up = max(uplink - sub.last_uplink_snapshot, 0)
                    delta_down = max(downlink - sub.last_downlink_snapshot, 0)
                    delta = delta_up + delta_down

                sub.last_uplink_snapshot = uplink
                sub.last_downlink_snapshot = downlink

                if delta <= 0:
                    continue

                _consumed, became_exhausted, overage = await white_internet_repo.deduct_traffic_atomic(
                    session,
                    subscription_id=sub.id,
                    delta_bytes=delta,
                    delta_uplink=delta_up,
                    delta_downlink=delta_down,
                    now=now,
                )

                total_processed += 1
                if overage:
                    logger.warning(
                        "White Internet quota overshoot: sub_id=%d overage=%d bytes",
                        sub.id,
                        overage,
                    )

                if became_exhausted and self.bot is not None:
                    user = await session.scalar(select(User).where(User.id == sub.user_id))
                    if user and user.telegram_id:
                        try:
                            from bot import texts
                            await self.bot.send_message(
                                chat_id=user.telegram_id,
                                text=texts.WL_TRAFFIC_EXHAUSTED_ALERT,
                                parse_mode="HTML",
                            )
                        except Exception as exc:
                            logger.warning(
                                "Failed to send quota exhaustion alert to user %d: %s",
                                user.id,
                                exc,
                            )

            await session.flush()

        return total_processed


async def white_internet_traffic_loop(
    bot: Bot | None = None,
    shutdown_event: asyncio.Event | None = None,
):
    """Background loop polling traffic snapshots and updating ledgers."""
    event = shutdown_event or asyncio.Event()
    worker = WhiteInternetTrafficWorker(bot=bot)
    logger.info("White Internet traffic worker started.")

    while not event.is_set():
        try:
            async with session_scope() as session:
                processed = await worker.run_traffic_cycle(session)
                if processed > 0:
                    logger.debug("Processed traffic for %d White Internet subscriptions.", processed)
        except Exception as exc:
            logger.error("Unhandled error in White Internet traffic cycle: %s", exc, exc_info=True)

        try:
            await asyncio.wait_for(event.wait(), timeout=TRAFFIC_SYNC_INTERVAL_SECONDS)
            break
        except asyncio.TimeoutError:
            pass

    logger.info("White Internet traffic worker stopped.")
