"""Periodic health, disk usage (>85%), and availability monitor for VPN nodes.

Unified Server Health Monitor with state machine: ONLINE -> PROBLEM -> AUTO_DISABLED.
"""
import asyncio
import logging
import time
from typing import Dict, Optional

from aiogram import Bot
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config.settings import get_settings
from database.connection import session_scope
from database.repositories.servers_repo import (
    get_all_servers,
    get_server_by_id,
    update_server,
)

from services.amnezia_client import AmneziaClient
from utils.datetime_helpers import now_utc
from utils.telegram import safe

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 30.0
PROBLEM_OBSERVATION_TIMEOUT = 15 * 60.0  # 15 минут наблюдения
AUTO_DISABLED_CHECK_INTERVAL = 15 * 60.0  # 15 минут между тихими проверками
REQUIRED_STABLE_SUCCESSES = 3  # Требуется 3 успешных ответа подряд для подтверждения восстановления


class ServerHealthState:
    ONLINE = "ONLINE"
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
        self.last_check_monotonic: float = 0.0
        self.auto_disabled_recovery_alert_sent = False
        self.disk_alert_sent = False


_server_states: Dict[int, ServerMonitorState] = {}


def get_server_monitor_state(server_id: int) -> ServerMonitorState:
    if server_id not in _server_states:
        _server_states[server_id] = ServerMonitorState(server_id)
    return _server_states[server_id]


def clear_server_monitor_state(server_id: int) -> None:
    _server_states.pop(server_id, None)


def reset_server_monitor_state(server_id: int, new_state: str = ServerHealthState.ONLINE) -> None:
    st = get_server_monitor_state(server_id)
    st.health_state = new_state
    st.consecutive_fails = 0
    st.consecutive_successes = 0
    st.problem_started_at = None
    st.auto_disabled_recovery_alert_sent = False
    st.disk_alert_sent = False


def _build_alert_keyboard(server_id: int, include_enable_button: bool = False) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    if include_enable_button:
        builder.button(
            text="🟢 Включить сервер",
            callback_data=f"admin_server_toggle:{server_id}",
        )
    builder.button(
        text="🖥 Карточка сервера",
        callback_data=f"admin_server_card:{server_id}",
    )
    builder.button(
        text="🗑 Прочитано",
        callback_data="admin_dismiss_alert",
    )
    builder.adjust(1)
    return builder


async def _send_admin_alert_msg(bot: Bot, text: str, reply_markup=None):
    settings = get_settings()
    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error("Failed to send node monitor alert to admin %s: %s", admin_id, e)


async def check_node_resources_and_alerts(bot: Bot):
    async with session_scope() as session:
        servers = await get_all_servers(session)

    now_m = time.monotonic()

    for server in servers:
        st = get_server_monitor_state(server.id)

        # 1. Синхронизация состояния с БД
        if not server.is_active:
            if server.disabled_reason == "AUTO_UNAVAILABLE":
                if st.health_state != ServerHealthState.AUTO_DISABLED:
                    st.health_state = ServerHealthState.AUTO_DISABLED
            else:
                st.health_state = ServerHealthState.MANUAL_DISABLED
        else:
            if st.health_state in (ServerHealthState.AUTO_DISABLED, ServerHealthState.MANUAL_DISABLED):
                # Сервер включен администратором
                st.health_state = ServerHealthState.ONLINE
                st.consecutive_fails = 0
                st.consecutive_successes = 0
                st.problem_started_at = None

        # 2. Пропускаем серверы, выключенные вручную
        if st.health_state == ServerHealthState.MANUAL_DISABLED:
            continue

        # 3. Соблюдение 15-минутного интервала тихих проверок для AUTO_DISABLED серверов
        if st.health_state == ServerHealthState.AUTO_DISABLED:
            if st.last_check_monotonic > 0 and (now_m - st.last_check_monotonic < AUTO_DISABLED_CHECK_INTERVAL):
                continue

        st.last_check_monotonic = now_m
        client = AmneziaClient(server.api_url, server.api_key)

        try:
            is_healthy = await client.healthcheck()

            if is_healthy:
                # Обновляем время последней успешной проверки
                async with session_scope() as session:
                    db_server = await get_server_by_id(session, server.id)
                    if db_server:
                        await update_server(session, db_server, last_successful_check=now_utc())

                if st.health_state == ServerHealthState.ONLINE:
                    st.consecutive_fails = 0
                    st.consecutive_successes += 1

                    # Проверяем использование диска
                    load_info = await client.get_server_load()
                    if load_info and isinstance(load_info, dict):
                        disk_percent = (
                            load_info.get("disk_percent")
                            or load_info.get("disk_used_percent")
                            or load_info.get("disk")
                        )
                        if disk_percent is not None and isinstance(disk_percent, (int, float)):
                            if disk_percent > 85.0 and not st.disk_alert_sent:
                                kb = _build_alert_keyboard(server.id).as_markup()
                                await _send_admin_alert_msg(
                                    bot,
                                    f"⚠️ <b>ВНИМАНИЕ: Диск VPN-ноды забит > 85%!</b>\n\n"
                                    f"Сервер: <b>{safe(server.name)}</b> (ID: {server.id})\n"
                                    f"Использование диска: <b>{disk_percent:.1f}%</b>\n"
                                    f"Рекомендуется очистить логи или расширить диск.",
                                    reply_markup=kb,
                                )
                                st.disk_alert_sent = True
                            elif disk_percent <= 80.0:
                                st.disk_alert_sent = False

                elif st.health_state == ServerHealthState.PROBLEM:
                    st.consecutive_fails = 0
                    st.consecutive_successes += 1

                    if st.consecutive_successes >= REQUIRED_STABLE_SUCCESSES:
                        st.health_state = ServerHealthState.ONLINE
                        st.problem_started_at = None
                        kb = _build_alert_keyboard(server.id).as_markup()
                        await _send_admin_alert_msg(
                            bot,
                            f"✅ <b>VPN-сервер восстановлен</b>\n\n"
                            f"🌍 Сервер: <b>{safe(server.name)}</b> (ID: {server.id})\n"
                            f"API снова стабильно доступен.",
                            reply_markup=kb,
                        )

                elif st.health_state == ServerHealthState.AUTO_DISABLED:
                    st.consecutive_successes += 1
                    if st.consecutive_successes >= REQUIRED_STABLE_SUCCESSES and not st.auto_disabled_recovery_alert_sent:
                        st.auto_disabled_recovery_alert_sent = True
                        kb = _build_alert_keyboard(server.id, include_enable_button=True).as_markup()
                        await _send_admin_alert_msg(
                            bot,
                            f"✅ <b>Сервер восстановлен</b>\n\n"
                            f"🌍 Сервер: <b>{safe(server.name)}</b> (ID: {server.id})\n"
                            f"API стабильно отвечает.\n\n"
                            f"Сервер остаётся отключённым. При необходимости включите его вручную.",
                            reply_markup=kb,
                        )

            else:
                # Ошибка healthcheck (FAIL)
                st.consecutive_successes = 0

                if st.health_state == ServerHealthState.ONLINE:
                    # Повторная тихая проверка через 30 секунд для подтверждения FAIL #2
                    await asyncio.sleep(30.0)
                    confirm_healthy = await client.healthcheck()
                    if not confirm_healthy:
                        st.health_state = ServerHealthState.PROBLEM
                        st.consecutive_fails = 2
                        st.problem_started_at = time.monotonic()
                        kb = _build_alert_keyboard(server.id).as_markup()
                        await _send_admin_alert_msg(
                            bot,
                            f"⚠️ <b>Проблема с VPN-сервером</b>\n\n"
                            f"🌍 Сервер: <b>{safe(server.name)}</b> (ID: {server.id})\n"
                            f"API не отвечает после повторной проверки.\n\n"
                            f"Возможна недоступность или нестабильное соединение.\n\n"
                            f"🔍 <b>Проверьте сервер.</b>\n"
                            f"Автоматический мониторинг продолжается.",
                            reply_markup=kb,
                        )

                elif st.health_state == ServerHealthState.PROBLEM:
                    st.consecutive_fails += 1
                    # Проверяем истечение 15 минут наблюдения
                    if st.problem_started_at and (now_m - st.problem_started_at >= PROBLEM_OBSERVATION_TIMEOUT):
                        st.health_state = ServerHealthState.AUTO_DISABLED
                        async with session_scope() as session:
                            db_server = await get_server_by_id(session, server.id)
                            if db_server:
                                await update_server(
                                    session,
                                    db_server,
                                    is_active=False,
                                    disabled_reason="AUTO_UNAVAILABLE",
                                    disabled_at=now_utc(),
                                )
                        kb = _build_alert_keyboard(server.id, include_enable_button=True).as_markup()
                        await _send_admin_alert_msg(
                            bot,
                            f"🔴 <b>Сервер автоматически отключён</b>\n\n"
                            f"🌍 Сервер: <b>{safe(server.name)}</b> (ID: {server.id})\n"
                            f"Сервер не восстановил стабильное соединение в течение 15 минут.\n\n"
                            f"Причина: API недоступен / соединение нестабильно.\n"
                            f"Сервер исключён из работы.\n\n"
                            f"🔕 Повторных уведомлений не будет.\n"
                            f"Доступность будет проверяться автоматически каждые 15 минут.",
                            reply_markup=kb,
                        )

                elif st.health_state == ServerHealthState.AUTO_DISABLED:
                    # Раз за разом лежим в AUTO_DISABLED — ПОЛНАЯ ТИШИНА!
                    pass

        except Exception as exc:
            logger.warning("Error checking node monitor for server %s: %s", server.id, exc, exc_info=True)


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
