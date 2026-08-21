"""Periodic health, disk usage (>85%), and availability monitor for VPN nodes.

Unified Server Health Monitor with persistent state machine:
ONLINE -> WAITING_CONFIRMATION -> PROBLEM -> AUTO_DISABLED -> MANUAL_DISABLED
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Optional

from aiogram import Bot
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config.settings import get_settings
from database.connection import session_scope
from database.models import Server
from database.repositories.servers_repo import (
    get_all_servers,
    get_server_by_id,
    update_server,
    update_server_health_snapshot,
)

from services.amnezia_client import AmneziaClient
from utils.datetime_helpers import now_utc
from utils.telegram import safe

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 15.0  # Частота тиков фонового воркера
CONFIRMATION_DELAY_SECONDS = 30.0  # Пауза между FAIL #1 и FAIL #2 (неблокирующая)
PROBLEM_OBSERVATION_TIMEOUT = 15 * 60.0  # 15 минут наблюдения за сервером в статусе PROBLEM
AUTO_DISABLED_CHECK_INTERVAL = 900.0  # 15 минут между тихими проверками в режиме AUTO_DISABLED
REQUIRED_STABLE_SUCCESSES = 3  # 3 успешных ответа подряд для подтверждения восстановления
DISK_ALERT_COOLDOWN_SECONDS = 3600.0  # 1 час между повторными уведомлениями о диске


class ServerHealthState:
    ONLINE = "ONLINE"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    PROBLEM = "PROBLEM"
    AUTO_DISABLED = "AUTO_DISABLED"
    MANUAL_DISABLED = "MANUAL_DISABLED"


class ServerMonitorState:
    def __init__(self, server_id: int):
        self.server_id = server_id
        self.health_state = ServerHealthState.ONLINE
        self.consecutive_fails = 0
        self.consecutive_successes = 0
        self.problem_started_at: Optional[float] = None
        self.next_check_at: Optional[float] = None
        self.last_check_monotonic: float = 0.0
        self.last_alert_sent_state: Optional[str] = None
        self.disk_alert_last_sent: Optional[float] = None
        self.recovery_notice_sent: bool = False

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


_monitor_states: Dict[int, ServerMonitorState] = {}
_server_states = _monitor_states


def get_server_monitor_state(server_id: int, server: Optional[Server] = None) -> ServerMonitorState:
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


def clear_monitor_states():
    _monitor_states.clear()


def _build_alert_keyboard(server_id: int, include_enable_button: bool = False) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    if include_enable_button:
        kb.button(
            text="🔘 Включить сервер",
            callback_data=f"admin_server_toggle_apply:{server_id}",
        )
    kb.button(
        text="⚙️ К серверу",
        callback_data=f"admin_server_card:{server_id}",
    )
    kb.button(
        text="📋 Список серверов",
        callback_data="admin_servers",
    )
    kb.button(
        text="🗑 Прочитано",
        callback_data=f"admin_dismiss_alert:{server_id}",
    )
    kb.adjust(1)
    return kb


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

    for server in servers:
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
            continue

        # 3. Неблокирующая проверка временных интервалов (WAITING_CONFIRMATION или AUTO_DISABLED)
        if st.health_state in (ServerHealthState.WAITING_CONFIRMATION, ServerHealthState.AUTO_DISABLED):
            if st.next_check_at and now_m < st.next_check_at:
                continue

        # Захватываем ожидаемое состояние ТОЧНО перед выполнением сетевой проверки
        expected_health_state = st.health_state
        expected_consecutive_fails = st.consecutive_fails
        expected_consecutive_successes = st.consecutive_successes

        st.last_check_monotonic = now_m
        client = AmneziaClient(server.api_url, server.api_key)

        # 4. Исполнение проверки с гарантированным отловом любых сетевых ошибок/таймаутов
        is_healthy = False
        try:
            is_healthy = await client.healthcheck()
        except Exception as exc:
            logger.warning("Healthcheck exception for server %s (%s): %s", server.id, server.name, exc)
            is_healthy = False

        alerts_to_send: list[dict] = []

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
                    kb = _build_alert_keyboard(server.id).as_markup()
                    alerts_to_send.append({
                        "text": (
                            f"✅ <b>VPN-сервер восстановлен</b>\n\n"
                            f"🌍 Сервер: <b>{safe(server.name)}</b> (ID: {server.id})\n"
                            f"API снова стабильно доступен."
                        ),
                        "reply_markup": kb,
                        "target_alert_state": ServerHealthState.ONLINE,
                    })

                # Проверка диска (с 1-часовым кулдауном)
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
                                if st.disk_alert_last_sent is None:
                                    should_alert = True
                                elif (now_m - st.disk_alert_last_sent) >= DISK_ALERT_COOLDOWN_SECONDS:
                                    should_alert = True

                                if should_alert:
                                    kb = _build_alert_keyboard(server.id).as_markup()
                                    alerts_to_send.append({
                                        "text": (
                                            f"⚠️ <b>ВНИМАНИЕ: Диск VPN-ноды забит > 85%!</b>\n\n"
                                            f"Сервер: <b>{safe(server.name)}</b> (ID: {server.id})\n"
                                            f"Использование диска: <b>{disk_percent:.1f}%</b>\n"
                                            f"Рекомендуется очистить логи или расширить диск."
                                        ),
                                        "reply_markup": kb,
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

                    kb = _build_alert_keyboard(server.id).as_markup()
                    alerts_to_send.append({
                        "text": (
                            f"✅ <b>VPN-сервер восстановлен</b>\n\n"
                            f"🌍 Сервер: <b>{safe(server.name)}</b> (ID: {server.id})\n"
                            f"API снова стабильно доступен."
                        ),
                        "reply_markup": kb,
                        "target_alert_state": ServerHealthState.ONLINE,
                    })

            elif st.health_state == ServerHealthState.AUTO_DISABLED:
                st.consecutive_successes += 1
                # Запланировать следующую проверку в режиме AUTO_DISABLED через 15 минут
                st.next_check_at = now_m + AUTO_DISABLED_CHECK_INTERVAL

                if st.consecutive_successes >= REQUIRED_STABLE_SUCCESSES and not st.recovery_notice_sent:
                    kb = _build_alert_keyboard(server.id, include_enable_button=True).as_markup()
                    alerts_to_send.append({
                        "text": (
                            f"✅ <b>Сервер восстановлен</b>\n\n"
                            f"🌍 Сервер: <b>{safe(server.name)}</b> (ID: {server.id})\n"
                            f"API стабильно отвечает.\n\n"
                            f"Сервер остаётся отключённым. При необходимости включите его вручную."
                        ),
                        "reply_markup": kb,
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

                kb = _build_alert_keyboard(server.id).as_markup()
                alerts_to_send.append({
                    "text": (
                        f"⚠️ <b>Проблема с VPN-сервером</b>\n\n"
                        f"🌍 Сервер: <b>{safe(server.name)}</b> (ID: {server.id})\n"
                        f"API не отвечает после повторной проверки.\n\n"
                        f"Возможна недоступность или нестабильное соединение.\n\n"
                        f"🔍 <b>Проверьте сервер.</b>\n"
                        f"Автоматический мониторинг продолжается."
                    ),
                    "reply_markup": kb,
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
                    kb = _build_alert_keyboard(server.id, include_enable_button=True).as_markup()
                    alerts_to_send.append({
                        "text": (
                            f"🔴 <b>Сервер автоматически отключён</b>\n\n"
                            f"🌍 Сервер: <b>{safe(server.name)}</b> (ID: {server.id})\n"
                            f"Сервер не восстановил стабильное соединение в течение 15 минут.\n\n"
                            f"Причина: API недоступен / соединение нестабильно.\n"
                            f"Сервер исключён из работы.\n\n"
                            f"🔕 Повторных уведомлений не будет.\n"
                            f"Доступность будет проверяться автоматически каждые 15 минут."
                        ),
                        "reply_markup": kb,
                        "target_alert_state": ServerHealthState.AUTO_DISABLED,
                    })

            elif st.health_state == ServerHealthState.AUTO_DISABLED:
                st.next_check_at = now_m + AUTO_DISABLED_CHECK_INTERVAL

        # Повторная попытка отправки не доставленных алертов
        if not alerts_to_send:
            if st.health_state == ServerHealthState.PROBLEM and st.last_alert_sent_state != ServerHealthState.PROBLEM:
                kb = _build_alert_keyboard(server.id).as_markup()
                alerts_to_send.append({
                    "text": (
                        f"⚠️ <b>Проблема с VPN-сервером</b>\n\n"
                        f"🌍 Сервер: <b>{safe(server.name)}</b> (ID: {server.id})\n"
                        f"API не отвечает после повторной проверки.\n\n"
                        f"Возможна недоступность или нестабильное соединение.\n\n"
                        f"🔍 <b>Проверьте сервер.</b>\n"
                        f"Автоматический мониторинг продолжается."
                    ),
                    "reply_markup": kb,
                    "target_alert_state": ServerHealthState.PROBLEM,
                })
            elif st.health_state == ServerHealthState.AUTO_DISABLED and st.last_alert_sent_state != ServerHealthState.AUTO_DISABLED:
                kb = _build_alert_keyboard(server.id, include_enable_button=True).as_markup()
                alerts_to_send.append({
                    "text": (
                        f"🔴 <b>Сервер автоматически отключён</b>\n\n"
                        f"🌍 Сервер: <b>{safe(server.name)}</b> (ID: {server.id})\n"
                        f"Сервер не восстановил стабильное соединение в течение 15 минут.\n\n"
                        f"Причина: API недоступен / соединение нестабильно.\n"
                        f"Сервер исключён из работы.\n\n"
                        f"🔕 Повторных уведомлений не будет.\n"
                        f"Доступность будет проверяться автоматически каждые 15 минут."
                    ),
                    "reply_markup": kb,
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
