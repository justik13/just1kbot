"""Traffic sync worker collecting stats from Xray nodes and deducting from grant ledgers."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging

from typing import Any

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.constants import XRAY_PROTOCOL
from config.enums import ServerHealthState, ServerLifecycleStatus
from database.connection import session_scope
from database.models import Server, User, WhiteInternetSubscription
from database.repositories import servers_repo, white_internet_repo
from services.xray_node_client import XrayNodeClient
from utils.datetime_helpers import now_utc

import enum

logger = logging.getLogger("WhiteInternetTraffic")

TRAFFIC_SYNC_INTERVAL_SECONDS = 60.0


def _mask_uuid(val: Any) -> str:
    if not val or not isinstance(val, str):
        return "***"
    return f"{val[:8]}***"


class TrafficCounterState(str, enum.Enum):
    NORMAL = "normal"
    RESET = "reset"
    EPOCH_CHANGED = "epoch_changed"
    INVALID = "invalid"


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
                    Server.protocol == XRAY_PROTOCOL,
                    Server.api_url.is_not(None),
                    Server.api_key.is_not(None),
                    Server.is_active.is_(True),
                    Server.lifecycle_status == ServerLifecycleStatus.ACTIVE,
                    Server.health_state.in_(
                        [ServerHealthState.ONLINE, ServerHealthState.WAITING_CONFIRMATION]
                    ),
                )
                .order_by(Server.id.asc())
            )
            servers = (await sess.execute(stmt)).scalars().all()
            server_list = [
                (
                    s.id,
                    s.api_url,
                    s.api_key,
                    s.xray_instance_epoch,
                    s.xray_instance_boot_id,
                    s.xray_instance_starttime,
                )
                for s in servers
                if (
                    getattr(s, "protocol", None) == XRAY_PROTOCOL
                    or (getattr(s, "protocol", None) is None and "xray_origin" in (s.capabilities or []))
                )
                and "xray_origin" in (s.capabilities or [])
                and s.api_url
                and s.api_key
            ]

        total_processed = 0
        exhausted_users_to_notify: list[int] = []

        for server_id, api_url, api_key, cur_epoch, cur_boot_id, cur_starttime in server_list:
            # Network I/O outside DB transaction
            (
                node_epoch,
                node_boot_id,
                node_starttime,
                users_stats,
            ) = await self.client.get_traffic_snapshot(api_url, api_key)
            if not node_epoch or users_stats is None or not isinstance(users_stats, dict):
                if users_stats is not None and not isinstance(users_stats, dict):
                    logger.warning(
                        "Node on server %d returned invalid users_stats type: %s",
                        server_id,
                        type(users_stats),
                    )
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
                if (
                    not isinstance(client_uuid, str)
                    or not client_uuid.strip()
                    or not isinstance(stats, dict)
                ):
                    logger.warning(
                        "Invalid client record on server %d: uuid=%s, stats=%r",
                        server_id,
                        _mask_uuid(client_uuid),
                        stats,
                    )
                    continue

                try:
                    raw_up = stats.get("uplink", 0)
                    raw_down = stats.get("downlink", 0)
                    uplink = max(int(raw_up) if raw_up is not None else 0, 0)
                    downlink = max(int(raw_down) if raw_down is not None else 0, 0)
                except (ValueError, TypeError) as parse_err:
                    logger.warning(
                        "Malformed uplink/downlink counters for client %s on server %d: %s",
                        _mask_uuid(client_uuid),
                        server_id,
                        parse_err,
                    )
                    continue

                try:
                    async with sf() as sess:
                        sub_meta = await sess.scalar(
                            select(WhiteInternetSubscription).where(
                                WhiteInternetSubscription.uuid == client_uuid,
                                WhiteInternetSubscription.origin_node_id == server_id,
                            )
                        )
                        if sub_meta is None:
                            continue

                        sub = await white_internet_repo.get_subscription_with_lock(
                            sess, sub_meta.id
                        )
                        if sub is None:
                            continue

                        if uplink < 0 or downlink < 0:
                            logger.warning(
                                "Invalid negative counter detected for sub_id=%d on server %d: up=%d, down=%d. Skipping.",
                                sub.id,
                                server_id,
                                uplink,
                                downlink,
                            )
                            continue

                        if sub.traffic_stats_epoch != node_epoch:
                            counter_state = TrafficCounterState.EPOCH_CHANGED
                            before_up = 0
                            before_down = 0
                        elif (
                            uplink < sub.last_uplink_snapshot
                            or downlink < sub.last_downlink_snapshot
                        ):
                            counter_state = TrafficCounterState.RESET
                            before_up = (
                                sub.last_uplink_snapshot
                                if uplink >= sub.last_uplink_snapshot
                                else 0
                            )
                            before_down = (
                                sub.last_downlink_snapshot
                                if downlink >= sub.last_downlink_snapshot
                                else 0
                            )
                            logger.info(
                                "TrafficCounterState.RESET detected within epoch for sub_id=%d on server %d: up(%d -> %d), down(%d -> %d). Rebasing baseline.",
                                sub.id,
                                server_id,
                                sub.last_uplink_snapshot,
                                uplink,
                                sub.last_downlink_snapshot,
                                downlink,
                            )
                        else:
                            counter_state = TrafficCounterState.NORMAL
                            before_up = sub.last_uplink_snapshot
                            before_down = sub.last_downlink_snapshot

                        delta_up = uplink - before_up
                        delta_down = downlink - before_down
                        delta = delta_up + delta_down
                        if delta <= 0:
                            continue

                        logger.debug(
                            "Processing sub_id=%d traffic with counter_state=%s, delta=%d",
                            sub.id,
                            counter_state.value,
                            delta,
                        )

                        (
                            consumed,
                            became_exhausted,
                            _available,
                            event,
                        ) = await white_internet_repo.record_and_deduct_traffic_atomic(
                            sess,
                            subscription_id=sub.id,
                            node_epoch=node_epoch,
                            snapshot_uplink_after=uplink,
                            snapshot_downlink_after=downlink,
                            snapshot_uplink_before=before_up,
                            snapshot_downlink_before=before_down,
                            node_boot_id=node_boot_id,
                            node_starttime=node_starttime,
                            now=now,
                        )
                        total_processed += 1

                        if became_exhausted:
                            exhausted_users_to_notify.append(sub.user_id)
                except Exception as client_exc:
                    logger.error(
                        "Error processing traffic deduction for client %s on server %d: %s",
                        _mask_uuid(client_uuid),
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
                        from config.constants import WHITE_INTERNET_TRIAL_MODE_ONLY

                        alert_text = (
                            texts.WL_TRAFFIC_EXHAUSTED_TRIAL_ALERT
                            if WHITE_INTERNET_TRIAL_MODE_ONLY
                            else texts.WL_TRAFFIC_EXHAUSTED_ALERT
                        )
                        await self.bot.send_message(
                            chat_id=telegram_id,
                            text=alert_text,
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
