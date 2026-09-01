"""Traffic sync worker collecting stats from Xray nodes and deducting from grant ledgers."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.enums import ServerHealthState
from database.connection import session_scope
from database.models import Server, User, WhiteInternetSubscription
from database.repositories import servers_repo, white_internet_repo
from services.xray_node_client import XrayNodeClient
from utils.datetime_helpers import now_utc

logger = logging.getLogger("WhiteInternetTraffic")

TRAFFIC_SYNC_INTERVAL_SECONDS = 60.0


class WhiteInternetTrafficWorker:
    """Worker polling Xray Origin and updating the grant ledger."""

    def __init__(
        self,
        bot: Bot | None = None,
        node_client: XrayNodeClient | None = None,
        session_factory=None,
    ):
        self.bot = bot
        self.client = node_client or XrayNodeClient()
        self.session_factory = session_factory or session_scope

    async def run_traffic_cycle(self, session: AsyncSession | None = None) -> int:
        now = now_utc()
        sf = self.session_factory
        if session is not None:
            @asynccontextmanager
            async def _session_ctx():
                yield session

            sf = _session_ctx

        async with sf() as sess:
            stmt = (
                select(Server)
                .where(
                    Server.api_url.is_not(None),
                    Server.api_key.is_not(None),
                    Server.is_active.is_(True),
                    Server.health_state.in_([ServerHealthState.ONLINE, ServerHealthState.WAITING_CONFIRMATION]),
                )
                .order_by(Server.id.asc())
            )
            servers = (await sess.execute(stmt)).scalars().all()
            server_list = [
                (s.id, s.api_url, s.api_key, s.xray_instance_epoch, s.xray_instance_boot_id, s.xray_instance_starttime)
                for s in servers
                if "xray_origin" in (s.capabilities or []) and s.api_url and s.api_key
            ]

        total_processed = 0
        exhausted_users_to_notify: list[int] = []

        for server_id, api_url, api_key, cur_epoch, cur_boot_id, cur_starttime in server_list:
            # Network I/O outside DB transaction
            node_epoch, node_boot_id, node_starttime, users_stats = await self.client.get_traffic_snapshot(
                api_url, api_key
            )
            if not node_epoch or users_stats is None:
                continue

            if (
                cur_epoch != node_epoch
                or cur_boot_id != node_boot_id
                or cur_starttime != node_starttime
            ):
                async with sf() as sess:
                    cas_ok, updated = await servers_repo.update_server_xray_epoch_cas(
                        sess,
                        server_id,
                        expected_boot_id=cur_boot_id,
                        expected_starttime=cur_starttime,
                        new_epoch=node_epoch,
                        new_boot_id=node_boot_id,
                        new_starttime=node_starttime,
                    )
                    if not cas_ok:
                        logger.warning(
                            "CAS rejected stale snapshot for server %d. Discarding snapshot.",
                            server_id,
                        )
                        continue

            for client_uuid, stats in users_stats.items():
                try:
                    uplink = max(int(stats.get("uplink", 0)), 0)
                    downlink = max(int(stats.get("downlink", 0)), 0)

                    async with sf() as sess:
                        sub_meta = await sess.scalar(
                            select(WhiteInternetSubscription).where(
                                WhiteInternetSubscription.uuid == client_uuid,
                                WhiteInternetSubscription.origin_node_id == server_id,
                            )
                        )
                        if sub_meta is None:
                            continue

                        sub = await white_internet_repo.get_subscription_with_lock(sess, sub_meta.id)
                        if sub is None:
                            continue

                        if sub.traffic_stats_epoch == node_epoch:
                            before_up = sub.last_uplink_snapshot if uplink >= sub.last_uplink_snapshot else 0
                            before_down = sub.last_downlink_snapshot if downlink >= sub.last_downlink_snapshot else 0
                            if before_up == 0 and uplink < sub.last_uplink_snapshot:
                                logger.info(
                                    "Xray uplink counter reset detected within epoch for sub_id=%d on server %d. Rebasing uplink baseline.",
                                    sub.id,
                                    server_id,
                                )
                            if before_down == 0 and downlink < sub.last_downlink_snapshot:
                                logger.info(
                                    "Xray downlink counter reset detected within epoch for sub_id=%d on server %d. Rebasing downlink baseline.",
                                    sub.id,
                                    server_id,
                                )
                        else:
                            before_up = 0
                            before_down = 0

                        delta_up = uplink - before_up
                        delta_down = downlink - before_down
                        delta = delta_up + delta_down
                        if delta <= 0:
                            continue

                        consumed, became_exhausted, overage = await white_internet_repo.deduct_traffic_atomic(
                            sess,
                            subscription_id=sub.id,
                            delta_bytes=delta,
                            delta_uplink=delta_up,
                            delta_downlink=delta_down,
                            now=now,
                        )

                        await white_internet_repo.record_traffic_event_atomic(
                            sess,
                            subscription_id=sub.id,
                            node_epoch=node_epoch,
                            node_boot_id=node_boot_id,
                            node_starttime=node_starttime,
                            snapshot_uplink_before=before_up,
                            snapshot_uplink_after=uplink,
                            snapshot_downlink_before=before_down,
                            snapshot_downlink_after=downlink,
                            delta_uplink=delta_up,
                            delta_downlink=delta_down,
                            allocated_bytes=consumed,
                            overage_bytes=overage,
                            now=now,
                        )

                        sub.last_uplink_snapshot = uplink
                        sub.last_downlink_snapshot = downlink
                        sub.traffic_stats_epoch = node_epoch
                        total_processed += 1

                        if became_exhausted:
                            exhausted_users_to_notify.append(sub.user_id)
                except Exception as client_exc:
                    logger.error(
                        "Error processing traffic deduction for client %s on server %d: %s",
                        client_uuid[:8] if client_uuid else "unknown",
                        server_id,
                        client_exc,
                        exc_info=True,
                    )

        # Send Telegram notifications strictly outside all DB transactions
        if self.bot is not None and exhausted_users_to_notify:
            for uid in set(exhausted_users_to_notify):
                async with sf() as sess:
                    user = await sess.scalar(select(User).where(User.id == uid))
                    telegram_id = user.telegram_id if user else None

                if telegram_id:
                    try:
                        from bot import texts
                        await self.bot.send_message(
                            chat_id=telegram_id,
                            text=texts.WL_TRAFFIC_EXHAUSTED_ALERT,
                            parse_mode="HTML",
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed to send quota exhaustion alert to user %d: %s",
                            uid,
                            exc,
                        )

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
            processed = await worker.run_traffic_cycle()
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
