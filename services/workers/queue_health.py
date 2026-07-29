"""Periodic non-blocking alerts for unhealthy durable payment queues."""
from __future__ import annotations

import asyncio
import html
import logging
import time
from dataclasses import dataclass
from typing import Callable

from aiogram import Bot

from config.settings import get_settings
from database.connection import session_scope
from services.payment_queue_health import QueueSnapshot, get_payment_queue_health_snapshot

logger = logging.getLogger(__name__)
CHECK_INTERVAL_SECONDS = 60.0
REMINDER_COOLDOWN_SECONDS = 3600.0
ALERT_TIMEOUT_SECONDS = 5.0

@dataclass
class _Episode:
    unhealthy: bool = False
    last_alert_at: float | None = None

class QueueHealthMonitor:
    """In-memory episode state; intentionally resets when the process restarts."""
    def __init__(self, bot: Bot, *, cooldown: float = REMINDER_COOLDOWN_SECONDS,
                 clock: Callable[[], float] = time.monotonic,
                 alert_timeout: float = ALERT_TIMEOUT_SECONDS):
        self.bot = bot
        self.cooldown = cooldown
        self.clock = clock
        self.alert_timeout = alert_timeout
        self.episodes: dict[str, _Episode] = {}
        self.alert_tasks: set[asyncio.Task] = set()

    def observe(self, snapshot) -> None:
        now = self.clock()
        for queue in snapshot.queues:
            episode = self.episodes.setdefault(queue.name, _Episode())
            if not queue.healthy:
                should_alert = (not episode.unhealthy or episode.last_alert_at is None
                                or now - episode.last_alert_at >= self.cooldown)
                episode.unhealthy = True
                if should_alert:
                    episode.last_alert_at = now
                    self._schedule(self._unhealthy_message(queue))
            elif episode.unhealthy:
                episode.unhealthy = False
                episode.last_alert_at = None
                self._schedule(self._recovery_message(queue))

    def _schedule(self, message: str) -> None:
        # One delivery at a time bounds tasks even when Telegram hangs. Health
        # checks remain independent and continue updating episode state.
        async def deliver():
            try:
                async def send_all():
                    for admin_id in get_settings().ADMIN_IDS:
                        await self.bot.send_message(admin_id, message, parse_mode="HTML")
                await asyncio.wait_for(send_all(), timeout=self.alert_timeout)
            except asyncio.TimeoutError:
                logger.warning("Queue health alert delivery timed out")
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.error("Queue health alert delivery failed type=%s", type(error).__name__)
        task = asyncio.create_task(deliver(), name="queue_health_alert")
        self.alert_tasks.add(task)
        task.add_done_callback(self.alert_tasks.discard)

    @staticmethod
    def _unhealthy_message(queue: QueueSnapshot) -> str:
        problems = []
        if queue.dead: problems.append(f"dead={queue.dead} oldest={queue.oldest_dead_age_seconds}s")
        if queue.overdue: problems.append(f"overdue={queue.overdue} oldest={queue.oldest_due_age_seconds}s")
        if queue.stale_processing: problems.append(f"stale_processing={queue.stale_processing} oldest={queue.oldest_stale_age_seconds}s")
        examples = []
        for item in queue.examples:
            payment = f" payment={item.payment_id}" if item.payment_id is not None else ""
            code = f" code={html.escape(item.last_error_code)}" if item.last_error_code else ""
            examples.append(f"<code>id={item.operation_id}{payment} type={html.escape(item.operation_type)} status={item.status} attempts={item.attempts}/{item.max_attempts} age={item.age_seconds}s{code}</code>")
        suffix = "\n" + "\n".join(examples) if examples else ""
        return f"🚨 <b>Durable queue unhealthy</b>\nQueue: <code>{queue.name}</code>\nProblems: {', '.join(problems)}{suffix}"

    @staticmethod
    def _recovery_message(queue: QueueSnapshot) -> str:
        return f"✅ <b>Durable queue recovered</b>\nQueue: <code>{queue.name}</code>"

    async def close(self) -> None:
        tasks = list(self.alert_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.alert_tasks.clear()

async def queue_health_loop(bot: Bot, shutdown_event: asyncio.Event, *,
                            interval: float = CHECK_INTERVAL_SECONDS,
                            cooldown: float = REMINDER_COOLDOWN_SECONDS,
                            clock: Callable[[], float] = time.monotonic,
                            snapshot_clock=None) -> None:
    monitor = QueueHealthMonitor(bot, cooldown=cooldown, clock=clock)
    try:
        while not shutdown_event.is_set():
            try:
                async with session_scope() as session:
                    snapshot = await get_payment_queue_health_snapshot(session, clock=snapshot_clock)
                monitor.observe(snapshot)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Only the exception class is logged; database messages can contain data.
                logger.error("Queue health check failed type=%s", type(error).__name__)
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
    finally:
        await monitor.close()
