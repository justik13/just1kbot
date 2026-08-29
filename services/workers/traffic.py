import asyncio
import logging
import time
from datetime import datetime, timezone

from aiogram import Bot
from bot.keyboards.notifications import get_traffic_alert_keyboard
from bot.texts.runtime.alerts import ALERT_TRAFFIC_OVERUSAGE
from cachetools import TTLCache
from sqlalchemy import select, update

from config.constants import (
    TRAFFIC_SYNC_INTERVAL,
    WORKER_ERROR_SLEEP_INTERVAL,
)
from config.settings import get_settings
from database.connection import session_scope
from database.models import Server, User, VPNProfile
from services.amnezia_client import AmneziaClient
from services.slots_cache import update_cached_peer_count
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)

BATCH_SIZE = 100
TRAFFIC_QUOTA_BYTES = 1 * 1024 * 1024 * 1024 * 1024
TRAFFIC_MAX_BACKOFF = 900
WORKER_START_DELAY = 30.0

_quota_alerted: TTLCache[int, bool] = TTLCache(maxsize=10000, ttl=86400)
_consecutive_crashes: int = 0
_background_tasks: set[asyncio.Task] = set()


def _start_background_task(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def traffic_sync_loop(
    bot_or_shutdown: Bot | asyncio.Event | None = None,
    shutdown_event: asyncio.Event | None = None,
):
    global _consecutive_crashes

    if isinstance(bot_or_shutdown, asyncio.Event):
        event = bot_or_shutdown
        bot = None
    else:
        bot = bot_or_shutdown
        event = shutdown_event or asyncio.Event()

    try:
        await asyncio.wait_for(
            event.wait(), timeout=WORKER_START_DELAY
        )
        logger.info(
            "Traffic sync worker stopped during start delay (shutdown)"
        )
        return
    except asyncio.TimeoutError:
        pass

    while not event.is_set():
        try:
            await _traffic_sync_once(bot)
            _consecutive_crashes = 0
        except asyncio.CancelledError:
            logger.info("Traffic sync worker cancelled")
            break
        except Exception as e:
            _consecutive_crashes += 1
            backoff = min(
                WORKER_ERROR_SLEEP_INTERVAL
                * (2 ** min(_consecutive_crashes - 1, 4)),
                TRAFFIC_MAX_BACKOFF,
            )
            logger.error(
                "Traffic sync worker crashed (attempt %s), backing off for %ss: %s",
                _consecutive_crashes,
                backoff,
                e,
                exc_info=True,
            )
            if event.is_set():
                break
            try:
                await asyncio.wait_for(event.wait(), timeout=backoff)
                break
            except asyncio.TimeoutError:
                pass
            continue

        try:
            await asyncio.wait_for(
                event.wait(), timeout=TRAFFIC_SYNC_INTERVAL
            )
            break
        except asyncio.TimeoutError:
            continue

    logger.info("Traffic sync worker stopped gracefully")


async def _traffic_sync_once(bot: Bot | None = None):
    servers = []
    async with session_scope() as session:
        stmt = (
            select(
                Server.id,
                Server.api_url,
                Server.api_key,
                Server.name,
                Server.is_active,
            )
            .where(Server.is_active.is_(True))
        )
        result = await session.execute(stmt)
        servers = [
            {
                "id": row[0],
                "api_url": row[1],
                "api_key": row[2],
                "name": row[3],
                "is_active": row[4],
            }
            for row in result.all()
        ]

    if not servers:
        return

    async def _fetch_server_traffic(server_info):
        from services.slots_cache import get_server_generation
        client = AmneziaClient(
            server_info["api_url"], server_info["api_key"]
        )
        gen = get_server_generation(server_info["id"])
        try:
            api_clients_list = await client.get_all_clients()
            t_done = time.monotonic()
            if api_clients_list is None:
                return server_info["id"], None, t_done, gen
            return server_info["id"], {
                c.id: c for c in api_clients_list
            }, t_done, gen
        except Exception as e:
            t_done = time.monotonic()
            logger.error(
                "Failed to fetch traffic from server %s: %s", server_info["name"], e
            )
            return server_info["id"], None, t_done, gen

    tasks = [_fetch_server_traffic(s) for s in servers]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    api_data_by_server = {
        r[0]: (r[1], r[2], r[3])
        for r in results
        if not isinstance(r, Exception) and r is not None and r[1] is not None
    }

    for server_info in servers:
        server_id = server_info["id"]
        if server_id not in api_data_by_server:
            continue
        api_clients, t_done, gen = api_data_by_server[server_id]

        from services.slots_cache import get_server_generation
        if gen != get_server_generation(server_id):
            logger.info("Skipping traffic processing for server %s due to configuration generation change", server_id)
            continue

        # ── ИСПРАВЛЕНО: обновляем slots_cache реальными данными ──
        update_cached_peer_count(server_id, len(api_clients), timestamp=t_done, generation=gen)

        await _process_server_traffic(server_info, api_clients, bot, expected_gen=gen)


async def _process_server_traffic(
    server_info,
    api_clients,
    bot: Bot | None = None,
    expected_gen: int | None = None,
):
    server_id = server_info["id"]
    from services.slots_cache import get_server_generation
    if expected_gen is not None and get_server_generation(server_id) != expected_gen:
        logger.info(
            "Aborting _process_server_traffic for server %s due to configuration generation change",
            server_id,
        )
        return

    updates_data = {}
    current_time = now_utc()

    async with session_scope() as session:
        stmt = (
            select(
                VPNProfile.id,
                VPNProfile.peer_id,
                VPNProfile.traffic_down,
                VPNProfile.traffic_up,
                VPNProfile.last_connected,
                VPNProfile.is_active,
                User.is_banned,
                User.telegram_id,
                User.subscription_end,
                User.financial_hold,
            )
            .join(User, VPNProfile.user_id == User.id)
            .where(VPNProfile.server_id == server_id)
        )
        result = await session.execute(stmt)
        rows = result.all()

        for (
            p_id,
            peer_id,
            t_down,
            t_up,
            last_conn,
            is_active,
            is_banned,
            tg_id,
            sub_end,
            financial_hold,
        ) in rows:
            if peer_id not in api_clients:
                continue

            api_data = api_clients[peer_id]
            api_t_down = api_data.traffics.totalDownload
            api_t_up = api_data.traffics.totalUpload
            new_t_down = api_t_down if api_t_down is not None else t_down
            new_t_up = api_t_up if api_t_up is not None else t_up

            last_conn_raw = (
                api_data.lastHandshake
                or api_data.lastSeen
                or api_data.updatedAt
            )
            new_last_connected = last_conn
            if last_conn_raw:
                try:
                    ts = int(float(str(last_conn_raw)))
                    if ts > 1e12:
                        ts = ts // 1000
                    new_last_connected = datetime.fromtimestamp(
                        ts, tz=timezone.utc
                    )
                except (ValueError, TypeError, OverflowError):
                    pass

            api_is_active = api_data.status == "active"
            is_subscription_expired = (
                sub_end is None or sub_end < current_time
            )
            local_should_be_disabled = (
                (not is_active) or is_banned or is_subscription_expired or financial_hold
            )

            if local_should_be_disabled and api_is_active:
                logger.debug(
                    "Peer desync detected (read-only): "
                    "server_id=%s, peer=%s, reason=%s",
                    server_id,
                    peer_id[:16],
                    "banned"
                    if is_banned
                    else (
                        "expired" if is_subscription_expired else "disabled"
                    ),
                )
            elif is_active and not api_is_active and not local_should_be_disabled:
                logger.debug(
                    "Peer desync detected (read-only): "
                    "server_id=%s, peer=%s, reason=api_desync",
                    server_id,
                    peer_id[:16],
                )

            if (
                t_down != new_t_down
                or t_up != new_t_up
                or last_conn != new_last_connected
            ):
                updates_data[p_id] = {
                    "traffic_down": new_t_down,
                    "traffic_up": new_t_up,
                    "last_connected": new_last_connected,
                }

            total_traffic = (new_t_down or 0) + (new_t_up or 0)
            if (
                total_traffic > TRAFFIC_QUOTA_BYTES
                and p_id not in _quota_alerted
            ):
                _quota_alerted[p_id] = True
                _start_background_task(
                    _send_quota_alert(
                        bot,
                        tg_id,
                        server_info["name"],
                        total_traffic,
                        p_id,
                    )
                )

        if updates_data:
            bulk_params = [
                {
                    "id": profile_id,
                    "traffic_down": data.get("traffic_down"),
                    "traffic_up": data.get("traffic_up"),
                    "last_connected": data.get("last_connected"),
                }
                for profile_id, data in updates_data.items()
            ]
            if bulk_params:
                await session.execute(
                    update(VPNProfile),
                    bulk_params,
                )




async def _send_quota_alert(
    bot: Bot | None,
    telegram_id: int,
    server_name: str,
    total_bytes: int,
    profile_id: int,
):
    try:
        if not bot:
            return
        settings = get_settings()
        admin_ids = settings.ADMIN_IDS
        if not admin_ids:
            return

        tib = total_bytes / (1024**4)
        msg = ALERT_TRAFFIC_OVERUSAGE.format(
            telegram_id=telegram_id,
            server_name=server_name,
            tib=tib,
            profile_id=profile_id,
        )
        reply_markup = get_traffic_alert_keyboard(telegram_id)

        for admin_id in admin_ids:
            try:
                await bot.send_message(
                    admin_id,
                    msg,
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error(
                    "Failed to send quota alert to %s: %s", admin_id, e
                )
    except Exception as e:
        logger.error("Failed to send quota alert: %s", e)
