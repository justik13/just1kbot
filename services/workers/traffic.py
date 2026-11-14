import asyncio
import logging

from aiogram import Bot
from bot.keyboards.notifications import get_traffic_alert_keyboard
from bot.texts.runtime.alerts import ALERT_TRAFFIC_OVERUSAGE
from cachetools import TTLCache
from sqlalchemy import select

from config.constants import (
    TRAFFIC_SYNC_INTERVAL,
    WORKER_ERROR_SLEEP_INTERVAL,
)
from config.settings import get_settings
from database.connection import session_scope
from database.models import Server, User
from utils.datetime_helpers import get_hosting_billing_cycle, now_utc
from utils.telegram import safe, safe_send_message
from utils.traffic_helpers import accumulate_host_traffic_cycle, get_server_hosting_reset_day

logger = logging.getLogger(__name__)

BATCH_SIZE = 100
TRAFFIC_QUOTA_BYTES = 1 * 1024 * 1024 * 1024 * 1024
TRAFFIC_MAX_BACKOFF = 900
WORKER_START_DELAY = 30.0

_quota_alerted: TTLCache[str, bool] = TTLCache(maxsize=10000, ttl=86400 * 35)
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
        vless_backoff: float | None = None
        try:
            await _vless_traffic_sync_once(bot)
            _consecutive_crashes = 0
        except asyncio.CancelledError:
            logger.info("Traffic sync worker cancelled")
            break
        except Exception as e:
            _consecutive_crashes += 1
            vless_backoff = min(
                WORKER_ERROR_SLEEP_INTERVAL
                * (2 ** min(_consecutive_crashes - 1, 4)),
                TRAFFIC_MAX_BACKOFF,
            )
            logger.error(
                "VLESS traffic sync pass failed (attempt %s), backing off for %ss: %s",
                _consecutive_crashes,
                vless_backoff,
                e,
                exc_info=True,
            )
            if event.is_set():
                break

        sleep_duration = vless_backoff if vless_backoff is not None else TRAFFIC_SYNC_INTERVAL
        try:
            await asyncio.wait_for(
                event.wait(), timeout=sleep_duration
            )
            break
        except asyncio.TimeoutError:
            continue

    logger.info("Traffic sync worker stopped gracefully")


def _check_user_quota_alert(
    bot: Bot | None,
    user_id: int,
    telegram_id: int,
    current_cycle: str,
    vless_bytes: int,
):
    total_monthly = max(0, vless_bytes)
    if total_monthly > TRAFFIC_QUOTA_BYTES:
        cache_key = f"{user_id}:{current_cycle}"
        if cache_key not in _quota_alerted:
            _quota_alerted[cache_key] = True
            _start_background_task(
                _send_quota_alert(
                    bot=bot,
                    user_id=user_id,
                    telegram_id=telegram_id,
                    cycle=current_cycle,
                    total_bytes=total_monthly,
                    vless_bytes=vless_bytes,
                )
            )


async def _send_quota_alert(
    bot: Bot | None,
    telegram_id: int,
    cycle: str,
    total_bytes: int,
    vless_bytes: int,
    user_id: int = 0,
):
    try:
        if not bot:
            return
        settings = get_settings()
        admin_ids = settings.ADMIN_IDS
        if not admin_ids:
            return

        from utils.formatters import format_traffic

        msg = ALERT_TRAFFIC_OVERUSAGE.format(
            telegram_id=telegram_id,
            cycle=safe(cycle),
            total_traffic=format_traffic(total_bytes),
            vless_traffic=format_traffic(vless_bytes),
        )
        reply_markup = get_traffic_alert_keyboard(telegram_id)

        any_sent = False
        for admin_id in admin_ids:
            try:
                sent = await safe_send_message(
                    bot,
                    chat_id=admin_id,
                    text=msg,
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                )
                if sent is not None:
                    any_sent = True
            except Exception as e:
                logger.error(
                    "Failed to send quota alert to %s: %s", admin_id, e
                )
        if not any_sent and user_id:
            cache_key = f"{user_id}:{cycle}"
            _quota_alerted.pop(cache_key, None)
    except Exception as e:
        logger.error("Failed to send quota alert: %s", e)
        if user_id:
            cache_key = f"{user_id}:{cycle}"
            _quota_alerted.pop(cache_key, None)


async def _vless_traffic_sync_once(bot: Bot | None = None):
    from config.enums import ServerHealthState, ServerLifecycleStatus
    from database.models import VlessSubscription
    from database.repositories import vless_subscription_repo
    from database.repositories.servers_repo import (
        get_server_role,
        get_server_xray_api_url,
        is_v2_node_server,
        is_vless_capable_server,
    )
    from services.xray_node_client import XrayNodeClient

    server_targets: list[tuple[int, str, str, str]] = []
    async with session_scope() as session:
        stmt = (
            select(Server)
            .where(
                Server.is_active.is_(True),
                Server.lifecycle_status == ServerLifecycleStatus.ACTIVE,
                Server.health_state.in_(
                    [ServerHealthState.ONLINE, ServerHealthState.WAITING_CONFIRMATION]
                ),
            )
            .order_by(Server.id.asc())
        )
        candidates = (await session.execute(stmt)).scalars().all()
        for srv in candidates:
            role = get_server_role(srv)
            if role == "ru_hub":
                continue
            if not (is_vless_capable_server(srv) or is_v2_node_server(srv)):
                continue
            api_url = get_server_xray_api_url(srv)
            api_key = srv.api_key
            if api_url and api_key:
                server_targets.append((srv.id, api_url, api_key, srv.name))

    if not server_targets:
        return

    # Network I/O outside DB transactions
    async def _fetch_vless_snapshot(srv_id: int, api_url: str, api_key: str, srv_name: str):
        try:
            async with XrayNodeClient(timeout=5.0) as client:
                snapshot = await client.get_traffic_snapshot(api_url, api_key)
                return srv_id, srv_name, snapshot, None
        except Exception as exc:
            logger.warning("Failed to fetch VLESS traffic snapshot from %s (%d): %s", srv_name, srv_id, exc)
            return srv_id, srv_name, None, exc

    tasks = [_fetch_vless_snapshot(s_id, url, key, name) for s_id, url, key, name in server_targets]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    current_time = now_utc()
    for item in results:
        if isinstance(item, Exception) or not item:
            continue
        server_id, server_name, snapshot, err = item
        if err is not None or snapshot is None:
            continue

        try:
            node_epoch, node_boot_id, node_starttime, users_stats = snapshot[:4]
            if not node_epoch:
                continue

            host_tx = getattr(snapshot, "host_tx_bytes", 0) or 0
            host_rx = getattr(snapshot, "host_rx_bytes", 0) or 0
            server_delta = 0

            # Process traffic deduction/accumulation per matching VlessSubscription
            affected_user_ids: set[int] = set()
            if isinstance(users_stats, dict) and users_stats:
                matching_uuids = [u for u in users_stats.keys() if isinstance(u, str) and u.strip()]
                if matching_uuids:
                    async with session_scope() as session:
                        subs_stmt = (
                            select(VlessSubscription.id, VlessSubscription.uuid, VlessSubscription.user_id)
                            .where(VlessSubscription.uuid.in_(matching_uuids))
                            .order_by(VlessSubscription.user_id, VlessSubscription.id)
                        )
                        candidate_subs = (await session.execute(subs_stmt)).all()
                        for sub_id, sub_uuid, _sub_uid in candidate_subs:
                            stats = users_stats.get(sub_uuid)
                            if not isinstance(stats, dict):
                                continue
                            raw_up = stats.get("uplink", 0)
                            raw_down = stats.get("downlink", 0)
                            try:
                                uplink = max(0, int(raw_up) if raw_up is not None else 0)
                                downlink = max(0, int(raw_down) if raw_down is not None else 0)
                            except (ValueError, TypeError):
                                continue

                            delta_total, _delta_down, _delta_up = await vless_subscription_repo.record_vless_traffic_atomic(
                                session=session,
                                subscription_id=sub_id,
                                server_id=server_id,
                                node_epoch=str(node_epoch),
                                raw_uplink=uplink,
                                raw_downlink=downlink,
                                expected_uuid=sub_uuid,
                                now=current_time,
                            )
                            if delta_total > 0:
                                server_delta += delta_total
                                if _sub_uid:
                                    affected_user_ids.add(_sub_uid)

            # 1. In a separate transaction, update Server extra_data immediately after subscription commit
            if server_delta > 0 or host_tx > 0 or host_rx > 0:
                current_cycle = current_time.strftime("%Y-%m")
                async with session_scope() as session:
                    server_obj = await session.get(Server, server_id, with_for_update=True)
                    if server_obj:
                        raw_extra = getattr(server_obj, "extra_data", None)
                        extra = dict(raw_extra) if isinstance(raw_extra, dict) else {}
                        changed = False
                        if server_delta > 0:
                            saved_cycle = extra.get("traffic_cycle")
                            if saved_cycle != current_cycle:
                                extra["traffic_cycle"] = current_cycle
                                extra["monthly_traffic_bytes"] = server_delta
                            else:
                                extra["monthly_traffic_bytes"] = int(extra.get("monthly_traffic_bytes", 0)) + server_delta
                            changed = True
                        if host_tx > 0 or host_rx > 0:
                            host_cycle = get_hosting_billing_cycle(
                                current_time,
                                get_server_hosting_reset_day(extra),
                            )
                            if accumulate_host_traffic_cycle(extra, host_tx, host_rx, host_cycle):
                                changed = True
                        if changed:
                            server_obj.extra_data = extra

            # 2. Check user quota alerts in an isolated try-block so notification errors never impact server stats
            if affected_user_ids and bot:
                try:
                    current_cycle = current_time.strftime("%Y-%m")
                    async with session_scope() as session:
                        user_rows = (
                            await session.execute(
                                select(
                                    User.id,
                                    User.telegram_id,
                                    User.monthly_vless_bytes,
                                    User.traffic_cycle,
                                ).where(User.id.in_(affected_user_ids))
                            )
                        ).all()
                        for uid_val, tg_id_val, m_vless, u_cycle in user_rows:
                            if u_cycle != current_cycle:
                                continue
                            _check_user_quota_alert(
                                bot=bot,
                                user_id=uid_val,
                                telegram_id=tg_id_val,
                                current_cycle=current_cycle,
                                vless_bytes=int(m_vless or 0),
                            )
                except Exception as alert_err:
                    logger.error(
                        "Failed to check VLESS quota alerts for users %s on server %s (%d): %s",
                        affected_user_ids,
                        server_name,
                        server_id,
                        alert_err,
                        exc_info=True,
                    )
        except Exception as srv_err:
            logger.error(
                "Failed to process VLESS traffic for server %s (%d): %s",
                server_name,
                server_id,
                srv_err,
                exc_info=True,
            )

