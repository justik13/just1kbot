import asyncio
import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from bot import texts
from bot.keyboards.notifications import (
    get_traffic_alert_keyboard,
)
from bot.texts.runtime.alerts import ALERT_TRAFFIC_OVERUSAGE
from cachetools import TTLCache
from sqlalchemy import case, func, select, update

from config.constants import (
    TRAFFIC_SYNC_INTERVAL,
    WORKER_ERROR_SLEEP_INTERVAL,
)
from config.enums import (
    ServerHealthState,
    ServerLifecycleStatus,
    WhiteInternetStatus,
)
from config.settings import get_settings
from database.connection import session_scope
from database.models import Server, User, VlessSubscription, WhiteInternetSubscription
from database.repositories import (
    servers_repo,
    vless_subscription_repo,
    white_internet_repo,
)
from database.repositories.servers_repo import (
    get_server_xray_api_url,
    is_v2_node_server,
    is_vless_capable_server,
)
from services.xray_node_client import XrayNodeClient
from utils.datetime_helpers import now_utc
from utils.rate_limiter import global_send_limiter
from utils.telegram import safe, safe_send_message
from utils.traffic_helpers import accumulate_server_traffic_extra

logger = logging.getLogger(__name__)

TRAFFIC_QUOTA_BYTES = 1 * 1024 * 1024 * 1024 * 1024  # 1 TB
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
        await asyncio.wait_for(event.wait(), timeout=WORKER_START_DELAY)
        logger.info("Traffic sync worker stopped during start delay (shutdown)")
        return
    except asyncio.TimeoutError:
        pass

    while not event.is_set():
        backoff: float | None = None
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
                "Traffic sync pass failed (attempt %s), backing off for %ss: %s",
                _consecutive_crashes,
                backoff,
                e,
                exc_info=True,
            )
            if event.is_set():
                break

        sleep_duration = backoff if backoff is not None else TRAFFIC_SYNC_INTERVAL
        try:
            await asyncio.wait_for(event.wait(), timeout=sleep_duration)
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
                logger.error("Failed to send quota alert to %s: %s", admin_id, e)
        if not any_sent and user_id:
            cache_key = f"{user_id}:{cycle}"
            _quota_alerted.pop(cache_key, None)
    except Exception as e:
        logger.error("Failed to send quota alert: %s", e)
        if user_id:
            cache_key = f"{user_id}:{cycle}"
            _quota_alerted.pop(cache_key, None)


async def _send_wi_exhausted_alerts(bot: Bot | None, exhausted_users: list[int]):
    if not bot or not exhausted_users:
        return

    for uid in set(exhausted_users):
        async with session_scope() as session:
            user = await session.scalar(select(User).where(User.id == uid))
            telegram_id = user.telegram_id if (user and not user.is_bot_blocked) else None

        if not telegram_id:
            continue

        alert_text = texts.WL_TRAFFIC_EXHAUSTED_ALERT
        for attempt in range(3):
            try:
                await global_send_limiter.acquire()
                await safe_send_message(
                    bot,
                    chat_id=telegram_id,
                    text=alert_text,
                    parse_mode="HTML",
                    raise_forbidden=True,
                )
                break
            except TelegramForbiddenError:
                logger.info("User %d blocked bot; marking blocked in database", uid)
                async with session_scope() as session:
                    await session.execute(
                        update(User).where(User.id == uid).values(is_bot_blocked=True)
                    )
                break
            except Exception as exc:
                if attempt == 2:
                    logger.warning("Failed to send quota exhaustion alert to user %d: %s", uid, exc)
                else:
                    await asyncio.sleep(1)


async def _send_wi_90p_alerts(bot: Bot | None, warn_users: list[tuple[int, int, int]]):
    if not bot or not warn_users:
        return

    for sub_id, uid, expected_version in set(warn_users):
        async with session_scope() as session:
            user = await session.scalar(select(User).where(User.id == uid))
            telegram_id = user.telegram_id if (user and not user.is_bot_blocked) else None

        if not telegram_id:
            async with session_scope() as session:
                await session.execute(
                    update(WhiteInternetSubscription)
                    .where(
                        WhiteInternetSubscription.id == sub_id,
                        WhiteInternetSubscription.desired_version == expected_version,
                    )
                    .values(notified_90p=True)
                )
            continue

        try:
            alert_text = texts.WL_TRAFFIC_90P_ALERT
            await global_send_limiter.acquire()
            await safe_send_message(
                bot,
                chat_id=telegram_id,
                text=alert_text,
                parse_mode="HTML",
                raise_forbidden=True,
            )
            async with session_scope() as session:
                await session.execute(
                    update(WhiteInternetSubscription)
                    .where(
                        WhiteInternetSubscription.id == sub_id,
                        WhiteInternetSubscription.desired_version == expected_version,
                    )
                    .values(notified_90p=True)
                )
        except TelegramForbiddenError:
            logger.info("User %d blocked bot; marking blocked in database", uid)
            async with session_scope() as session:
                await session.execute(
                    update(User).where(User.id == uid).values(is_bot_blocked=True)
                )
                await session.execute(
                    update(WhiteInternetSubscription)
                    .where(
                        WhiteInternetSubscription.id == sub_id,
                        WhiteInternetSubscription.desired_version == expected_version,
                    )
                    .values(notified_90p=True)
                )
        except Exception as exc:
            logger.warning("Failed to send 90%% traffic warning alert to user %d: %s", uid, exc)


async def _traffic_sync_once(bot: Bot | None = None):
    server_targets: list[tuple[int, str, str, str, Any, Any, Any]] = []
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
            if not (is_vless_capable_server(srv) or is_v2_node_server(srv)):
                continue
            api_url = get_server_xray_api_url(srv)
            api_key = srv.api_key
            if api_url and api_key:
                server_targets.append(
                    (
                        srv.id,
                        api_url,
                        api_key,
                        srv.name,
                        srv.xray_instance_epoch,
                        srv.xray_instance_boot_id,
                        srv.xray_instance_starttime,
                    )
                )

    if not server_targets:
        return

    # Network I/O outside DB transactions
    async def _fetch_snapshot(srv_id: int, api_url: str, api_key: str, srv_name: str):
        try:
            async with XrayNodeClient(timeout=5.0) as client:
                snapshot = await client.get_traffic_snapshot(api_url, api_key)
                return srv_id, srv_name, snapshot, None
        except Exception as exc:
            logger.warning("Failed to fetch traffic snapshot from %s (%d): %s", srv_name, srv_id, exc)
            return srv_id, srv_name, None, exc

    tasks = [_fetch_snapshot(s_id, url, key, name) for s_id, url, key, name, _, _, _ in server_targets]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    targets_by_id = {s_id: (cur_ep, cur_boot, cur_start) for s_id, _, _, _, cur_ep, cur_boot, cur_start in server_targets}
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
            cur_epoch, cur_boot_id, cur_starttime = targets_by_id.get(server_id, (None, None, None))

            # CAS epoch check
            if (
                cur_epoch != node_epoch
                or cur_boot_id != node_boot_id
                or cur_starttime != node_starttime
            ):
                async with session_scope() as session:
                    cas_ok, _ = await servers_repo.update_server_xray_epoch_cas(
                        session,
                        server_id,
                        expected_boot_id=cur_boot_id,
                        expected_starttime=cur_starttime,
                        new_epoch=node_epoch,
                        new_boot_id=node_boot_id,
                        new_starttime=node_starttime,
                    )
                    if not cas_ok:
                        srv_check = await session.scalar(select(Server).where(Server.id == server_id))
                        if srv_check and srv_check.xray_instance_epoch == node_epoch:
                            logger.info(
                                "Server %s (%d) epoch already updated to %s concurrently. Continuing snapshot.",
                                server_name,
                                server_id,
                                node_epoch,
                            )
                        else:
                            logger.warning(
                                "Stale snapshot for server %s (%d) (snapshot epoch %s != db %s). Discarding snapshot.",
                                server_name,
                                server_id,
                                node_epoch,
                                srv_check.xray_instance_epoch if srv_check else "unknown",
                            )
                            continue

            server_delta = 0
            affected_vless_user_ids: set[int] = set()
            exhausted_wi_users: list[tuple[int, bool]] = []
            warn_90p_wi_users: list[tuple[int, int, bool, int]] = []

            if isinstance(users_stats, dict) and users_stats:
                matching_uuids = [u for u in users_stats.keys() if isinstance(u, str) and u.strip()]
                if matching_uuids:
                    # 1. Process VLESS Subscriptions
                    async with session_scope() as session:
                        subs_stmt = (
                            select(VlessSubscription.id, VlessSubscription.uuid, VlessSubscription.user_id)
                            .where(VlessSubscription.uuid.in_(matching_uuids))
                            .order_by(VlessSubscription.user_id, VlessSubscription.id)
                        )
                        candidate_subs = list((await session.execute(subs_stmt)).all())

                        from database.models import UserDevice
                        dev_stmt = (
                            select(VlessSubscription.id, UserDevice.device_uuid, VlessSubscription.user_id)
                            .join(UserDevice, UserDevice.user_id == VlessSubscription.user_id)
                            .where(UserDevice.device_uuid.in_(matching_uuids))
                            .order_by(VlessSubscription.user_id, VlessSubscription.id)
                        )
                        candidate_subs.extend((await session.execute(dev_stmt)).all())

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

                            delta_total, _, _ = await vless_subscription_repo.record_vless_traffic_atomic(
                                session=session,
                                subscription_id=sub_id,
                                server_id=server_id,
                                node_epoch=str(node_epoch),
                                raw_uplink=uplink,
                                raw_downlink=downlink,
                                client_uuid=sub_uuid,
                                now=current_time,
                            )
                            if delta_total > 0:
                                server_delta += delta_total
                                if _sub_uid:
                                    affected_vless_user_ids.add(_sub_uid)

                    # 2. Process White Internet Subscriptions (aggregate sub.uuid + all user UserDevice UUIDs)
                    async with session_scope() as session:
                        wi_stmt = (
                            select(WhiteInternetSubscription.id, WhiteInternetSubscription.uuid, WhiteInternetSubscription.user_id)
                            .where(
                                WhiteInternetSubscription.hub_server_id == server_id,
                            )
                            .order_by(WhiteInternetSubscription.user_id, WhiteInternetSubscription.id)
                        )
                        candidate_wi_subs = list((await session.execute(wi_stmt)).all())
                        wi_user_ids = [uid for _sid, _suuid, uid in candidate_wi_subs if uid]
                        wi_dev_map: dict[int, list[str]] = {}
                        if wi_user_ids:
                            wi_dev_rows = (
                                await session.execute(
                                    select(UserDevice.user_id, UserDevice.device_uuid).where(
                                        UserDevice.user_id.in_(wi_user_ids),
                                        UserDevice.is_active.is_(True),
                                        UserDevice.device_uuid.is_not(None),
                                    )
                                )
                            ).all()
                            for u_id, d_uuid in wi_dev_rows:
                                wi_dev_map.setdefault(u_id, []).append(str(d_uuid))

                    for wi_sub_id, wi_sub_uuid, wi_sub_uid in candidate_wi_subs:
                        tracked_uuids = {wi_sub_uuid, *wi_dev_map.get(wi_sub_uid, [])}
                        uplink = 0
                        downlink = 0
                        has_any_stats = False
                        for t_uuid in tracked_uuids:
                            stats = users_stats.get(t_uuid)
                            if not isinstance(stats, dict):
                                continue
                            has_any_stats = True
                            raw_up = stats.get("uplink", 0)
                            raw_down = stats.get("downlink", 0)
                            try:
                                uplink += max(0, int(raw_up) if raw_up is not None else 0)
                                downlink += max(0, int(raw_down) if raw_down is not None else 0)
                            except (ValueError, TypeError):
                                continue
                        if not has_any_stats:
                            continue

                        try:
                            async with session_scope() as session:
                                if wi_sub_uid:
                                    await session.execute(
                                        select(User.id).where(User.id == wi_sub_uid).with_for_update()
                                    )
                                sub = await white_internet_repo.get_subscription_with_lock(session, wi_sub_id)
                                if sub is None or sub.hub_server_id != server_id:
                                    continue

                                if sub.traffic_stats_epoch != str(node_epoch):
                                    before_up = 0
                                    before_down = 0
                                elif (
                                    uplink < (sub.last_uplink_snapshot or 0)
                                    or downlink < (sub.last_downlink_snapshot or 0)
                                ):
                                    before_up = sub.last_uplink_snapshot if uplink >= (sub.last_uplink_snapshot or 0) else 0
                                    before_down = sub.last_downlink_snapshot if downlink >= (sub.last_downlink_snapshot or 0) else 0
                                else:
                                    before_up = sub.last_uplink_snapshot or 0
                                    before_down = sub.last_downlink_snapshot or 0

                                delta_up = uplink - before_up
                                delta_down = downlink - before_down
                                delta = delta_up + delta_down

                                if delta <= 0:
                                    total_quota = (getattr(sub, "base_traffic_bytes", 0) or 0) + (
                                        getattr(sub, "extra_traffic_bytes", 0) or 0
                                    )
                                    used = max(
                                        0,
                                        (getattr(sub, "traffic_used_bytes", 0) or 0)
                                        - (getattr(sub, "traffic_overage_bytes", 0) or 0),
                                    )
                                    if (
                                        not getattr(sub, "notified_90p", False)
                                        and getattr(sub, "status", None) == WhiteInternetStatus.ACTIVE
                                        and total_quota > 0
                                        and used >= (0.90 * total_quota)
                                    ):
                                        warn_90p_wi_users.append(
                                            (
                                                sub.id,
                                                sub.user_id,
                                                int(getattr(sub, "desired_version", 1) or 1),
                                            )
                                        )
                                    continue

                                (
                                    consumed,
                                    became_exhausted,
                                    _available,
                                    event,
                                ) = await white_internet_repo.record_and_deduct_traffic_atomic(
                                    session,
                                    subscription_id=sub.id,
                                    node_epoch=node_epoch,
                                    snapshot_uplink_after=uplink,
                                    snapshot_downlink_after=downlink,
                                    snapshot_uplink_before=before_up,
                                    snapshot_downlink_before=before_down,
                                    node_boot_id=node_boot_id,
                                    node_starttime=node_starttime,
                                    now=current_time,
                                )
                                if consumed > 0 and sub.user_id:
                                    current_cycle = current_time.strftime("%Y-%m")
                                    await session.execute(
                                        update(User)
                                        .where(User.id == sub.user_id)
                                        .values(
                                            total_wi_traffic_bytes=User.total_wi_traffic_bytes + consumed,
                                            monthly_wi_bytes=case(
                                                (User.traffic_cycle > current_cycle, User.monthly_wi_bytes),
                                                (User.traffic_cycle == current_cycle, func.coalesce(User.monthly_wi_bytes, 0) + consumed),
                                                else_=consumed,
                                            ),
                                            monthly_vless_bytes=case(
                                                (User.traffic_cycle > current_cycle, User.monthly_vless_bytes),
                                                (User.traffic_cycle == current_cycle, func.coalesce(User.monthly_vless_bytes, 0)),
                                                else_=0,
                                            ),
                                            traffic_cycle=case(
                                                (User.traffic_cycle > current_cycle, User.traffic_cycle),
                                                else_=current_cycle,
                                            ),
                                        )
                                    )
                                    server_delta += consumed

                                if became_exhausted:
                                    exhausted_wi_users.append(sub.user_id)
                                elif event == "traffic_90p":
                                    warn_90p_wi_users.append(
                                        (
                                            sub.id,
                                            sub.user_id,
                                            int(getattr(sub, "desired_version", 1) or 1),
                                        )
                                    )
                        except Exception as client_exc:
                            logger.error(
                                "Error processing WI traffic deduction for client %s on server %d: %s",
                                wi_sub_uuid[:8] if wi_sub_uuid else "***",
                                server_id,
                                client_exc,
                                exc_info=True,
                            )

            # 3. Update Server extra_data
            if server_delta > 0 or host_tx > 0 or host_rx > 0:
                async with session_scope() as session:
                    server_obj = await session.get(Server, server_id, with_for_update=True)
                    if server_obj:
                        accumulate_server_traffic_extra(
                            server_obj,
                            server_delta,
                            host_tx,
                            host_rx,
                            current_time,
                            accumulate_host=True,
                        )

            # 4. Check user quota alerts outside DB transactions
            if affected_vless_user_ids and bot:
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
                                ).where(User.id.in_(affected_vless_user_ids))
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
                        affected_vless_user_ids,
                        server_name,
                        server_id,
                        alert_err,
                        exc_info=True,
                    )

            if exhausted_wi_users and bot:
                _start_background_task(_send_wi_exhausted_alerts(bot, exhausted_wi_users))

            if warn_90p_wi_users and bot:
                _start_background_task(_send_wi_90p_alerts(bot, warn_90p_wi_users))

        except Exception as srv_err:
            logger.error(
                "Failed to process traffic for server %s (%d): %s",
                server_name,
                server_id,
                srv_err,
                exc_info=True,
            )


# Backward compatibility alias
_vless_traffic_sync_once = _traffic_sync_once
