"""Periodic health, disk usage (>85%), and availability monitor for VPN nodes."""
import asyncio
import logging
import time
from typing import Dict


from aiogram import Bot

from config.settings import get_settings
from database.connection import session_scope
from database.repositories.servers_repo import get_active_servers

from services.amnezia_client import AmneziaClient
from utils.telegram import safe

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 60.0
ALERT_COOLDOWN_SECONDS = 3600.0  # 1 час между повторяющимися алертами для одного сервера

_last_alert_time: Dict[str, float] = {}


async def _send_admin_alert(bot: Bot, text: str, alert_key: str):
    now = time.monotonic()
    # Periodic cleanup of expired alert keys
    expired_keys = [k for k, v in _last_alert_time.items() if now - v > ALERT_COOLDOWN_SECONDS * 2]
    for k in expired_keys:
        _last_alert_time.pop(k, None)

    last_time = _last_alert_time.get(alert_key, 0.0)
    if now - last_time < ALERT_COOLDOWN_SECONDS:
        return

    _last_alert_time[alert_key] = now
    settings = get_settings()

    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error("Failed to send node monitor alert to admin %s: %s", admin_id, e)



async def check_node_resources_and_alerts(bot: Bot):
    async with session_scope() as session:
        servers = await get_active_servers(session)


    for server in servers:
        client = AmneziaClient(server.api_url, server.api_key)
        try:
            is_healthy = await client.healthcheck()
            if not is_healthy:
                await _send_admin_alert(
                    bot,
                    f"🚨 <b>ВНИМАНИЕ: VPN-нода недоступна!</b>\n\n"
                    f"Сервер: <b>{safe(server.name)}</b> (ID: {server.id})\n"
                    f"URL: <code>{safe(server.api_url)}</code>\n"
                    f"Статус: <b>API не отвечает на /healthz!</b>",
                    alert_key=f"down_{server.id}",
                )
                continue

            load_info = await client.get_server_load()
            if not load_info:
                continue

            # Извлечение метрик диска из ответа API
            disk_percent = None
            if isinstance(load_info, dict):
                disk_percent = (
                    load_info.get("disk_percent")
                    or load_info.get("disk_used_percent")
                    or load_info.get("disk")
                )

            if disk_percent is not None and isinstance(disk_percent, (int, float)):
                if disk_percent > 85.0:
                    await _send_admin_alert(
                        bot,
                        f"⚠️ <b>ВНИМАНИЕ: Диск VPN-ноды забит > 85%!</b>\n\n"
                        f"Сервер: <b>{safe(server.name)}</b> (ID: {server.id})\n"
                        f"Использование диска: <b>{disk_percent:.1f}%</b>\n"
                        f"Рекомендуется очистить логи или расширить диск.",
                        alert_key=f"disk_{server.id}",
                    )

        except Exception as exc:
            logger.warning("Error checking node monitor for server %s: %s", server.id, exc)


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
