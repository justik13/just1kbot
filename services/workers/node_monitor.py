"""Periodic health, disk usage (>85%), and availability monitor for VPN nodes.

Unified Server Health Monitor with persistent state machine:
ONLINE -> WAITING_CONFIRMATION -> PROBLEM -> AUTO_DISABLED -> MANUAL_DISABLED
"""
import asyncio
import logging
import os
import time
from datetime import datetime, timezone

import aiohttp
from aiogram import Bot
from sqlalchemy import func, select

from bot.keyboards.notifications import get_node_monitor_alert_keyboard
from bot.texts.runtime.alerts import (
    ALERT_INGRESS_PROBLEM,
    ALERT_INGRESS_RESTORED,
    ALERT_SERVER_AUTO_DISABLED,
    ALERT_SERVER_AUTO_DISABLED_RECOVERED,
    ALERT_SERVER_DISK_CRITICAL,
    ALERT_SERVER_PROBLEM,
    ALERT_SERVER_RESTORED,
)
from config.constants import AMNEZIA_PROTOCOL, ServerHealthState, XRAY_PROTOCOL
from config.settings import get_settings
from database.connection import session_scope
from database.models import Server
from database.repositories.servers_repo import (
    get_all_servers,
    get_server_by_id,
    update_server,
    update_server_health_snapshot,
    update_server_xray_epoch_cas,
)
from services.amnezia_client import AmneziaClient
from utils.datetime_helpers import now_utc
from utils.telegram import safe

_build_alert_keyboard = get_node_monitor_alert_keyboard

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 15.0  # Частота тиков фонового воркера
CONFIRMATION_DELAY_SECONDS = 30.0  # Пауза между FAIL #1 и FAIL #2 (неблокирующая)
PROBLEM_OBSERVATION_TIMEOUT = 15 * 60.0  # 15 минут наблюдения за сервером в статусе PROBLEM
AUTO_DISABLED_CHECK_INTERVAL = 900.0  # 15 минут между тихими проверками в режиме AUTO_DISABLED
REQUIRED_STABLE_SUCCESSES = 3  # 3 успешных ответа подряд для подтверждения восстановления
DISK_ALERT_COOLDOWN_SECONDS = 3600.0  # 1 час между повторными уведомлениями о диске
# A hung node can hold a healthcheck for tens of seconds; checking servers in
# bounded parallel batches keeps one degraded node from freezing alerts for
# every other node in the cycle.
CHECK_PARALLELISM = 6


class ServerMonitorState:
    def __init__(self, server_id: int):
        self.server_id = server_id
        self.health_state = ServerHealthState.ONLINE
        self.consecutive_fails = 0
        self.consecutive_successes = 0
        self.problem_started_at: float | None = None
        self.next_check_at: float | None = None
        self.last_check_monotonic: float = 0.0
        self.last_alert_sent_state: str | None = None
        self.disk_alert_last_sent: float | None = None
        self.recovery_notice_sent: bool = False
        self.ingress_problem: bool = False

    def sync_from_db_server(self, db_server: Server):
        if db_server:
            if not db_server.is_active:
                if db_server.disabled_reason == "AUTO_UNAVAILABLE":
                    self.health_state = ServerHealthState.AUTO_DISABLED
                else:
                    self.health_state = ServerHealthState.MANUAL_DISABLED
            else:
                self.health_state = db_server.health_state or ServerHealthState.ONLINE

            self.consecutive_fails = db_server.consecutive_fails or 0
            self.consecutive_successes = db_server.consecutive_successes or 0
            self.recovery_notice_sent = bool(db_server.recovery_notice_sent)
            self.last_alert_sent_state = db_server.last_alert_sent_state
            if isinstance(getattr(db_server, "extra_data", None), dict):
                self.ingress_problem = bool(db_server.extra_data.get("ingress_problem", False))
            else:
                self.ingress_problem = False

            if db_server.problem_started_at:
                now_m = time.monotonic()
                diff = (now_utc() - db_server.problem_started_at).total_seconds()
                self.problem_started_at = now_m - max(0.0, diff)
            else:
                self.problem_started_at = None

            if db_server.next_check_at:
                now_m = time.monotonic()
                rem = (db_server.next_check_at - now_utc()).total_seconds()
                self.next_check_at = now_m + max(0.0, rem)
            else:
                self.next_check_at = None


_monitor_states: dict[int, ServerMonitorState] = {}
_server_states = _monitor_states


def get_server_monitor_state(server_id: int, server: Server | None = None) -> ServerMonitorState:
    if server_id not in _monitor_states:
        st = ServerMonitorState(server_id)
        if server:
            st.sync_from_db_server(server)
        _monitor_states[server_id] = st
    return _monitor_states[server_id]


def clear_server_monitor_state(server_id: int) -> None:
    _monitor_states.pop(server_id, None)


def reset_server_monitor_state(server_id: int, new_state: str = ServerHealthState.ONLINE) -> None:
    st = get_server_monitor_state(server_id)
    st.health_state = new_state
    st.consecutive_fails = 0
    st.consecutive_successes = 0
    st.problem_started_at = None
    st.next_check_at = None
    st.recovery_notice_sent = False
    st.last_alert_sent_state = None
    st.disk_alert_last_sent = None
    st.ingress_problem = False


def clear_monitor_states():
    _monitor_states.clear()


async def _send_admin_alert_msg(bot: Bot, text: str, reply_markup=None) -> bool:
    settings = get_settings()
    admin_ids = settings.ADMIN_IDS
    if not admin_ids:
        return True

    success = False
    for admin_id in admin_ids:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
            success = True
        except Exception as exc:
            logger.warning("Failed to send node monitor alert to admin %s: %s", admin_id, exc)
    return success


async def check_node_resources_and_alerts(bot: Bot):
    async with session_scope() as session:
        servers = await get_all_servers(session)

    if not servers:
        return

    now_m = time.monotonic()

    async def _check_one(server):
        st = get_server_monitor_state(server.id, server)

        # 1. Проверка ручного/автоматического отключения
        if not server.is_active:
            if server.disabled_reason == "AUTO_UNAVAILABLE":
                if st.health_state != ServerHealthState.AUTO_DISABLED:
                    st.health_state = ServerHealthState.AUTO_DISABLED
            else:
                st.health_state = ServerHealthState.MANUAL_DISABLED
        else:
            if st.health_state in (ServerHealthState.AUTO_DISABLED, ServerHealthState.MANUAL_DISABLED):
                # Сервер переключен администратором в активный режим
                st.health_state = ServerHealthState.ONLINE
                st.consecutive_fails = 0
                st.consecutive_successes = 0
                st.problem_started_at = None
                st.next_check_at = None
                st.recovery_notice_sent = False
                st.last_alert_sent_state = None

        # 2. Игнорируем сервера, отключенные вручную
        if st.health_state == ServerHealthState.MANUAL_DISABLED:
            return

        # 3. Неблокирующая проверка временных интервалов (WAITING_CONFIRMATION или AUTO_DISABLED)
        if st.health_state in (ServerHealthState.WAITING_CONFIRMATION, ServerHealthState.AUTO_DISABLED):
            if st.next_check_at and now_m < st.next_check_at:
                return

        # Захватываем ожидаемое состояние ТОЧНО перед выполнением сетевой проверки
        expected_health_state = st.health_state
        expected_consecutive_fails = st.consecutive_fails
        expected_consecutive_successes = st.consecutive_successes

        st.last_check_monotonic = now_m
        server_proto = getattr(server, "protocol", None)
        if not isinstance(server_proto, str):
            server_proto = AMNEZIA_PROTOCOL

        is_xray_node = (
            server_proto in (XRAY_PROTOCOL, "xray")
            or "xray_origin" in (getattr(server, "capabilities", None) or [])
        )
        is_amnezia_node = not is_xray_node and server_proto == AMNEZIA_PROTOCOL

        # 4a. Исполнение проверки Core Node API с гарантированным отловом любых сетевых ошибок/таймаутов
        is_healthy = False
        xray_epoch = None
        xray_data = None
        client = None
        try:
            if is_xray_node:
                from services.xray_node_client import XrayNodeClient
                async with XrayNodeClient(timeout=10.0) as xray_client:
                    is_healthy, xray_epoch, xray_data = await xray_client.check_health(
                        server.api_url, server.api_key
                    )
            elif is_amnezia_node:
                client = AmneziaClient(server.api_url, server.api_key)
                is_healthy = await client.healthcheck()
            else:
                logger.warning(
                    "Server %s (%s) has unsupported or unassigned protocol '%s', skipping healthcheck",
                    server.id, server.name, server.protocol,
                )
                is_healthy = False
        except Exception as exc:
            logger.warning("Healthcheck exception for server %s (%s): %s", server.id, server.name, exc)
            is_healthy = False

        # 4b. Изолированная синтетическая проверка Ingress (публичный CDN / Nginx Origin)
        # Строгое расцепление (AGENTS.md §9.7): сбои на синтетическом эндпоинте не аффектят статус ядра
        # и выполняются независимо от того, успешен ли был core healthcheck.
        ingress_probe_result: tuple[bool, str] | None = None
        probe_domain: str | None = None
        if is_xray_node:
            try:
                is_origin = (
                    "xray_origin" in (getattr(server, "capabilities", None) or [])
                    or bool((getattr(server, "extra_data", None) or {}).get("relays"))
                    or bool((getattr(server, "extra_data", None) or {}).get("cdn_domain"))
                )
                if is_origin:
                    if isinstance(getattr(server, "extra_data", None), dict):
                        probe_domain = server.extra_data.get("cdn_domain") or server.extra_data.get("domain")
                    if not probe_domain and server.api_url:
                        from urllib.parse import urlsplit
                        parsed = urlsplit(server.api_url)
                        if parsed.hostname and not parsed.hostname.replace(".", "").isdigit() and parsed.hostname != "localhost":
                            probe_domain = parsed.hostname

                    if probe_domain:
                        probe_domain = str(probe_domain).strip()
                        sub_prefix = (
                            (server.extra_data or {}).get("sub_path_prefix")
                            or os.getenv("WHITE_INTERNET_SUB_PATH_PREFIX", "/sub/wl")
                        ).strip().rstrip("/")
                        if not sub_prefix.startswith("/"):
                            sub_prefix = f"/{sub_prefix}"
                        probe_url = f"https://{probe_domain}{sub_prefix}/ping"
                        try:
                            timeout = aiohttp.ClientTimeout(total=5.0)
                            async with aiohttp.ClientSession(timeout=timeout) as probe_sess:
                                async with probe_sess.get(
                                    probe_url,
                                    allow_redirects=False,
                                ) as probe_resp:
                                    if probe_resp.status == 200:
                                        ingress_probe_result = (True, "200")
                                    else:
                                        logger.warning(
                                            "Xray origin node %s (%s) subscription proxy returned HTTP %s on %s",
                                            server.id, probe_domain, probe_resp.status, probe_url,
                                        )
                                        ingress_probe_result = (False, str(probe_resp.status))
                        except Exception as probe_exc:
                            logger.warning(
                                "Origin node %s (%s) subscription proxy ping failed on %s: %s",
                                server.id, probe_domain, probe_url, probe_exc,
                            )
                            ingress_probe_result = (False, str(probe_exc))
            except Exception as outer_probe_exc:
                logger.warning(
                    "Unexpected error preparing ingress probe for server %s: %s",
                    server.id, outer_probe_exc,
                )

        alerts_to_send: list[dict] = []

        # Decoupled ingress alert evaluation and delivery (independent of core server CAS / health state)
        if ingress_probe_result is not None and probe_domain:
            ingress_ok, ingress_detail = ingress_probe_result
            lock_key = 8_000_000_000 + int(server.id)

            async with session_scope() as session:
                is_pg = False
                bind = getattr(session, "bind", None)
                if not bind and hasattr(session, "sync_session"):
                    bind = getattr(session.sync_session, "bind", None)
                if bind and getattr(bind, "dialect", None) and getattr(bind.dialect, "name", None) == "postgresql":
                    is_pg = True
                    got_lock = bool(await session.scalar(select(func.pg_try_advisory_lock(lock_key))))
                    if not got_lock:
                        logger.debug("Ingress alert evaluation for server %d locked by peer worker. Skipping alert dispatch.", server.id)
                else:
                    got_lock = True

                if got_lock:
                    try:
                        fresh_server = await get_server_by_id(session, server.id)
                        db_extra = dict((fresh_server.extra_data if fresh_server else server.extra_data) or {})
                        db_ingress_problem = bool(db_extra.get("ingress_problem"))

                        # Durable-by-Default: sync in-memory state with DB
                        if db_ingress_problem:
                            st.ingress_problem = True

                        has_problem = db_ingress_problem or st.ingress_problem

                        if not ingress_ok:
                            if not has_problem:
                                sent_ok = False
                                try:
                                    sent_ok = await _send_admin_alert_msg(
                                        bot,
                                        ALERT_INGRESS_PROBLEM.format(
                                            server_name=safe(server.name),
                                            server_id=server.id,
                                            domain=safe(probe_domain),
                                            status_or_err=safe(ingress_detail),
                                        ),
                                        reply_markup=get_node_monitor_alert_keyboard(server.id).as_markup(),
                                    )
                                except Exception as e:
                                    logger.error("Failed to deliver ingress problem alert: %s", e)
                                if sent_ok:
                                    st.ingress_problem = True
                                    if fresh_server:
                                        db_extra["ingress_problem"] = True
                                        await update_server(session, fresh_server, extra_data=db_extra)
                                        await session.commit()
                        else:
                            if has_problem:
                                sent_ok = False
                                try:
                                    sent_ok = await _send_admin_alert_msg(
                                        bot,
                                        ALERT_INGRESS_RESTORED.format(
                                            server_name=safe(server.name),
                                            server_id=server.id,
                                            domain=safe(probe_domain),
                                        ),
                                        reply_markup=get_node_monitor_alert_keyboard(server.id).as_markup(),
                                    )
                                except Exception as e:
                                    logger.error("Failed to deliver ingress restored alert: %s", e)
                                if sent_ok:
                                    st.ingress_problem = False
                                    if fresh_server:
                                        db_extra.pop("ingress_problem", None)
                                        await update_server(session, fresh_server, extra_data=db_extra)
                                        await session.commit()
                    finally:
                        if is_pg and got_lock:
                            try:
                                if session.in_transaction():
                                    await session.commit()
                                await session.scalar(select(func.pg_advisory_unlock(lock_key)))
                                await session.commit()
                            except Exception as unlock_err:
                                logger.debug("Error releasing ingress advisory lock %d: %s", lock_key, unlock_err)

        if is_healthy:
            if st.health_state == ServerHealthState.WAITING_CONFIRMATION:
                # Восстановление после кратковременной ошибки (FAIL #1)
                st.health_state = ServerHealthState.ONLINE
                st.consecutive_fails = 0
                st.consecutive_successes = 0
                st.next_check_at = None
                st.problem_started_at = None

            elif st.health_state == ServerHealthState.ONLINE:
                st.consecutive_fails = 0
                st.consecutive_successes += 1
                st.next_check_at = None
                st.problem_started_at = None

                if st.last_alert_sent_state in (ServerHealthState.PROBLEM, ServerHealthState.AUTO_DISABLED):
                    alerts_to_send.append({
                        "text": ALERT_SERVER_RESTORED.format(
                            server_name=safe(server.name),
                            server_id=server.id,
                        ),
                        "reply_markup": get_node_monitor_alert_keyboard(server.id).as_markup(),
                        "target_alert_state": ServerHealthState.ONLINE,
                    })

                # Проверка диска (с 1-часовым кулдауном) для Amnezia узлов
                if is_amnezia_node and client is not None:
                    try:
                        load_info = await client.get_server_load()
                        if load_info and isinstance(load_info, dict):
                            disk_percent = (
                                load_info.get("disk_percent")
                                or load_info.get("disk_used_percent")
                                or load_info.get("disk")
                            )
                            if disk_percent is not None and isinstance(disk_percent, (int, float)):
                                if disk_percent > 85.0:
                                    should_alert = False
                                    if st.disk_alert_last_sent is None or (now_m - st.disk_alert_last_sent) >= DISK_ALERT_COOLDOWN_SECONDS:
                                        should_alert = True

                                    if should_alert:
                                        alerts_to_send.append({
                                            "text": ALERT_SERVER_DISK_CRITICAL.format(
                                                server_name=safe(server.name),
                                                server_id=server.id,
                                                disk_percent=disk_percent,
                                            ),
                                            "reply_markup": get_node_monitor_alert_keyboard(server.id).as_markup(),
                                        })
                                        st.disk_alert_last_sent = now_m
                                elif disk_percent <= 80.0:
                                    st.disk_alert_last_sent = None
                    except Exception as exc:
                        logger.warning("Error reading server load for server %s: %s", server.id, exc)

            elif st.health_state == ServerHealthState.PROBLEM:
                st.consecutive_fails = 0
                st.consecutive_successes += 1

                if st.consecutive_successes >= REQUIRED_STABLE_SUCCESSES:
                    st.health_state = ServerHealthState.ONLINE
                    st.problem_started_at = None
                    st.next_check_at = None

                    alerts_to_send.append({
                        "text": ALERT_SERVER_RESTORED.format(
                            server_name=safe(server.name),
                            server_id=server.id,
                        ),
                        "reply_markup": get_node_monitor_alert_keyboard(server.id).as_markup(),
                        "target_alert_state": ServerHealthState.ONLINE,
                    })

            elif st.health_state == ServerHealthState.AUTO_DISABLED:
                st.consecutive_successes += 1
                # Запланировать следующую проверку в режиме AUTO_DISABLED через 15 минут
                st.next_check_at = now_m + AUTO_DISABLED_CHECK_INTERVAL

                if st.consecutive_successes >= REQUIRED_STABLE_SUCCESSES and not st.recovery_notice_sent:
                    alerts_to_send.append({
                        "text": ALERT_SERVER_AUTO_DISABLED_RECOVERED.format(
                            server_name=safe(server.name),
                            server_id=server.id,
                        ),
                        "reply_markup": get_node_monitor_alert_keyboard(server.id, include_enable_button=True).as_markup(),
                        "is_recovery_notice": True,
                    })

        else:
            # Ошибка проверки (FAIL)
            st.consecutive_successes = 0

            if st.health_state == ServerHealthState.ONLINE:
                # Переход в WAITING_CONFIRMATION, планирование проверки через 30 секунд БЕЗ СЛИПА!
                st.health_state = ServerHealthState.WAITING_CONFIRMATION
                st.consecutive_fails = 1
                st.next_check_at = now_m + CONFIRMATION_DELAY_SECONDS

            elif st.health_state == ServerHealthState.WAITING_CONFIRMATION:
                # 30 секунд прошли, FAIL #2 подтвержден -> Переход в PROBLEM!
                st.health_state = ServerHealthState.PROBLEM
                st.consecutive_fails = 2
                st.problem_started_at = now_m
                st.next_check_at = None

                alerts_to_send.append({
                    "text": ALERT_SERVER_PROBLEM.format(
                        server_name=safe(server.name),
                        server_id=server.id,
                    ),
                    "reply_markup": get_node_monitor_alert_keyboard(server.id).as_markup(),
                    "target_alert_state": ServerHealthState.PROBLEM,
                })

            elif st.health_state == ServerHealthState.PROBLEM:
                st.consecutive_fails += 1

                elapsed_problem_sec = 0.0
                if st.problem_started_at:
                    elapsed_problem_sec = now_m - st.problem_started_at

                if elapsed_problem_sec >= PROBLEM_OBSERVATION_TIMEOUT:
                    st.health_state = ServerHealthState.AUTO_DISABLED
                    st.next_check_at = now_m + AUTO_DISABLED_CHECK_INTERVAL
                    alerts_to_send.append({
                        "text": ALERT_SERVER_AUTO_DISABLED.format(
                            server_name=safe(server.name),
                            server_id=server.id,
                        ),
                        "reply_markup": get_node_monitor_alert_keyboard(server.id, include_enable_button=True).as_markup(),
                        "target_alert_state": ServerHealthState.AUTO_DISABLED,
                    })

            elif st.health_state == ServerHealthState.AUTO_DISABLED:
                st.next_check_at = now_m + AUTO_DISABLED_CHECK_INTERVAL

        # Повторная попытка отправки не доставленных алертов
        if not alerts_to_send:
            if st.health_state == ServerHealthState.PROBLEM and st.last_alert_sent_state != ServerHealthState.PROBLEM:
                alerts_to_send.append({
                    "text": ALERT_SERVER_PROBLEM.format(
                        server_name=safe(server.name),
                        server_id=server.id,
                    ),
                    "reply_markup": get_node_monitor_alert_keyboard(server.id).as_markup(),
                    "target_alert_state": ServerHealthState.PROBLEM,
                })
            elif st.health_state == ServerHealthState.AUTO_DISABLED and st.last_alert_sent_state != ServerHealthState.AUTO_DISABLED:
                alerts_to_send.append({
                    "text": ALERT_SERVER_AUTO_DISABLED.format(
                        server_name=safe(server.name),
                        server_id=server.id,
                    ),
                    "reply_markup": get_node_monitor_alert_keyboard(server.id, include_enable_button=True).as_markup(),
                    "target_alert_state": ServerHealthState.AUTO_DISABLED,
                })

        # 5. ЕДИНСТВЕННОЕ АТОМАРНОЕ ОБНОВЛЕНИЕ БД НА КАЖДУЮ ПРОВЕРКУ (СНАЧАЛА CAS UPDATE)
        update_kwargs = {
            "health_state": st.health_state,
            "consecutive_fails": st.consecutive_fails,
            "consecutive_successes": st.consecutive_successes,
            "recovery_notice_sent": st.recovery_notice_sent,
            "last_alert_sent_state": st.last_alert_sent_state,
        }

        if is_healthy:
            update_kwargs["last_successful_check"] = now_utc()
            if is_xray_node and xray_data:
                extra_update = {}
                if "relays" in xray_data:
                    extra_update["relays"] = xray_data["relays"]
                if "secret_base_path" in xray_data:
                    extra_update["secret_base_path"] = xray_data["secret_base_path"]
                if "sub_path_prefix" in xray_data and xray_data["sub_path_prefix"]:
                    extra_update["sub_path_prefix"] = xray_data["sub_path_prefix"]
                if extra_update:
                    update_kwargs["extra_data"] = extra_update

            if st.health_state == ServerHealthState.ONLINE:
                update_kwargs["problem_started_at"] = None
                update_kwargs["next_check_at"] = None

        if st.health_state in (ServerHealthState.WAITING_CONFIRMATION, ServerHealthState.AUTO_DISABLED) and st.next_check_at:
            rem = st.next_check_at - now_m
            next_dt = datetime.fromtimestamp(time.time() + max(0.0, rem), tz=timezone.utc)
            update_kwargs["next_check_at"] = next_dt

        if st.health_state == ServerHealthState.PROBLEM and st.consecutive_fails == 2:
            update_kwargs["problem_started_at"] = now_utc()
            update_kwargs["next_check_at"] = None

        if st.health_state == ServerHealthState.AUTO_DISABLED and not is_healthy:
            update_kwargs["is_active"] = False
            update_kwargs["disabled_reason"] = "AUTO_UNAVAILABLE"
            update_kwargs["disabled_at"] = now_utc()

        async with session_scope() as session:
            # Monotonic CAS update for Xray generation
            if is_healthy and is_xray_node and xray_epoch:
                boot_id = xray_data.get("boot_id") if xray_data else None
                starttime = xray_data.get("starttime") if xray_data else None
                cas_ok, updated_srv = await update_server_xray_epoch_cas(
                    session,
                    server.id,
                    expected_boot_id=server.xray_instance_boot_id,
                    expected_starttime=server.xray_instance_starttime,
                    new_epoch=xray_epoch,
                    new_boot_id=boot_id,
                    new_starttime=starttime,
                )
                if cas_ok and updated_srv:
                    server.xray_instance_epoch = updated_srv.xray_instance_epoch
                    server.xray_instance_boot_id = updated_srv.xray_instance_boot_id
                    server.xray_instance_starttime = updated_srv.xray_instance_starttime

                    # Синхронизация динамических метаданных (релеи, cdn_domain) при изменениях на ноде
                    if xray_data and isinstance(xray_data, dict):
                        extra = dict(updated_srv.extra_data or {})
                        extra_changed = False
                        for key in ("cdn_domain", "relays", "secret_base_path"):
                            if key in xray_data and xray_data[key] and extra.get(key) != xray_data[key]:
                                extra[key] = xray_data[key]
                                extra_changed = True
                        if extra_changed:
                            updated_srv.extra_data = extra
                            server.extra_data = extra
                            await session.flush()

            db_server, applied = await update_server_health_snapshot(
                session,
                server.id,
                expected_health_state=expected_health_state,
                expected_consecutive_fails=expected_consecutive_fails,
                expected_consecutive_successes=expected_consecutive_successes,
                new_health_state=st.health_state,
                **update_kwargs,
            )
            if db_server and not applied:
                st.sync_from_db_server(db_server)

        # 6. ВНЕШНИЕ ПОБОЧНЫЕ ЭФФЕКТЫ (TELEGRAM ALERT) ВЫПОЛНЯЮТСЯ СТРОГО ПОСЛЕ УСПЕШНОГО CAS
        if applied and alerts_to_send:
            for alert_to_send in alerts_to_send:
                target_state = alert_to_send.get("target_alert_state")
                is_rec_notice = alert_to_send.get("is_recovery_notice", False)
                sent_ok = False
                try:
                    sent_ok = await _send_admin_alert_msg(
                        bot,
                        alert_to_send["text"],
                        reply_markup=alert_to_send.get("reply_markup"),
                    )
                except Exception as e:
                    logger.error("Failed to deliver node monitor alert: %s", e)
                    sent_ok = False

                if sent_ok:
                    if target_state:
                        st.last_alert_sent_state = target_state
                    if is_rec_notice:
                        st.recovery_notice_sent = True

                    async with session_scope() as session:
                        fresh_server = await get_server_by_id(session, server.id)
                        if fresh_server and fresh_server.health_state != ServerHealthState.MANUAL_DISABLED:
                            await update_server(
                                session,
                                fresh_server,
                                last_alert_sent_state=st.last_alert_sent_state,
                                recovery_notice_sent=st.recovery_notice_sent,
                            )

    # Bounded parallel fan-out: one hung node must not freeze monitoring of
    # the remaining nodes in this cycle.
    for batch_start in range(0, len(servers), CHECK_PARALLELISM):
        batch = servers[batch_start:batch_start + CHECK_PARALLELISM]
        await asyncio.gather(*(_check_one(s) for s in batch))


async def node_monitor_loop(bot: Bot, shutdown_event: asyncio.Event):
    logger.info("Starting node_monitor_loop background worker...")
    while not shutdown_event.is_set():
        try:
            await check_node_resources_and_alerts(bot)
        except Exception as exc:
            logger.error("Error in node_monitor_loop: %s", exc, exc_info=True)

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=CHECK_INTERVAL_SECONDS)
            break
        except asyncio.TimeoutError:
            pass
    logger.info("Stopped node_monitor_loop background worker.")
