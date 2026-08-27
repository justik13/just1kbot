"""Periodic non-blocking alerts for unhealthy durable payment queues."""
from __future__ import annotations

import asyncio
import html
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from aiogram import Bot
from config.settings import get_settings
from database.connection import session_scope
from services.payment_queue_health import (
    QueueSnapshot,
    get_payment_queue_health_snapshot,
)

logger = logging.getLogger(__name__)
CHECK_INTERVAL_SECONDS = 60.0
REMINDER_COOLDOWN_SECONDS = 3600.0
FAILED_RETRY_COOLDOWN_SECONDS = 5.0
ALERT_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class _Fingerprint:
    problem_types: frozenset[str]
    dead_count: int
    overdue: int = 0
    stale_processing: int = 0


@dataclass
class _Episode:
    unhealthy: bool = False
    recovery_needed: bool = False
    generation: int = 0
    last_observed_fingerprint: _Fingerprint | None = None
    current_revision: int = 0
    delivered_revision: int = 0
    pending_alert_message: str | None = None
    last_attempt_at: float | None = None
    last_success_at: float | None = None
    last_attempt_succeeded: bool | None = None
    in_flight: asyncio.Task | None = None
    in_flight_revision: int | None = None


class QueueHealthMonitor:
    """In-memory episode state; intentionally resets when the process restarts."""

    def __init__(self, bot: Bot, *, cooldown: float = REMINDER_COOLDOWN_SECONDS,
                 retry_cooldown: float = FAILED_RETRY_COOLDOWN_SECONDS,
                 clock: Callable[[], float] = time.monotonic,
                 alert_timeout: float = ALERT_TIMEOUT_SECONDS):
        self.bot = bot
        self.cooldown = cooldown
        # A positive floor prevents an interval=0 test/configuration from spinning
        # Telegram retries after a failed delivery.
        self.retry_cooldown = max(0.001, retry_cooldown)
        self.clock = clock
        self.alert_timeout = alert_timeout
        self.episodes: dict[str, _Episode] = {}
        self.alert_tasks: set[asyncio.Task] = set()

    @staticmethod
    def _fingerprint(queue: QueueSnapshot) -> _Fingerprint:
        types = set()
        if queue.overdue:
            types.add("overdue")
        if queue.stale_processing:
            types.add("stale_processing")
        if queue.dead:
            types.add("dead")
        return _Fingerprint(frozenset(types), queue.dead, queue.overdue, queue.stale_processing)

    @staticmethod
    def _escalated(current: _Fingerprint,
                   previous: _Fingerprint | None) -> bool:
        if previous is None:
            return True
        return (current.dead_count > previous.dead_count or bool(current.problem_types - previous.problem_types) or current.overdue > (previous.overdue or 0) or current.stale_processing > (previous.stale_processing or 0))

    @staticmethod
    def _reset_delivery_state(episode: _Episode) -> None:
        episode.last_attempt_at = None
        episode.last_success_at = None
        episode.last_attempt_succeeded = None

    def observe(self, snapshot) -> None:
        now = self.clock()
        for queue in snapshot.queues:
            episode = self.episodes.setdefault(queue.name, _Episode())
            if queue.healthy:
                if episode.unhealthy:
                    episode.unhealthy = False
                    episode.recovery_needed = True
                    episode.generation += 1
                    episode.last_observed_fingerprint = None
                    episode.current_revision = 0
                    episode.delivered_revision = 0
                    episode.pending_alert_message = None
                    self._reset_delivery_state(episode)
                if episode.recovery_needed:
                    self._try_schedule(queue.name, episode,
                                       self._recovery_message(queue), None, now)
                continue

            fingerprint = self._fingerprint(queue)
            if not episode.unhealthy:
                episode.unhealthy = True
                episode.recovery_needed = False
                episode.generation += 1
                episode.last_observed_fingerprint = None
                episode.current_revision = 0
                episode.delivered_revision = 0
                episode.pending_alert_message = None
                self._reset_delivery_state(episode)

            if self._escalated(fingerprint, episode.last_observed_fingerprint):
                episode.current_revision += 1
                episode.pending_alert_message = self._unhealthy_message(queue)
            # Decreases are deliberately observed even though they do not create a
            # revision, so a later increase is compared with the lower state.
            episode.last_observed_fingerprint = fingerprint

            if episode.delivered_revision < episode.current_revision:
                self._try_schedule(
                    queue.name, episode, episode.pending_alert_message or
                    self._unhealthy_message(queue), episode.current_revision, now,
                )
                continue

            reminder_due = (episode.last_success_at is not None
                            and now - episode.last_success_at >= self.cooldown)
            if reminder_due:
                self._try_schedule(queue.name, episode,
                                   self._unhealthy_message(queue),
                                   episode.current_revision, now)

    def _try_schedule(self, queue_name: str, episode: _Episode, message: str,
                      revision: int | None, now: float) -> None:
        if episode.in_flight is not None and not episode.in_flight.done():
            return
        if (episode.last_attempt_at is not None
                and episode.last_attempt_succeeded is False
                and now - episode.last_attempt_at < self.retry_cooldown):
            return
        episode.last_attempt_at = now
        episode.last_attempt_succeeded = None
        episode.in_flight_revision = revision
        generation = episode.generation

        async def deliver() -> None:
            success = False
            try:
                success = await asyncio.wait_for(
                    self._send_to_admins(message), timeout=self.alert_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("Queue health alert delivery timed out queue=%s", queue_name)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.error("Queue health alert delivery failed queue=%s type=%s",
                             queue_name, type(error).__name__)
            finally:
                if episode.generation == generation:
                    episode.last_attempt_succeeded = success
                    if success:
                        episode.last_success_at = self.clock()
                        if revision is None:
                            episode.recovery_needed = False
                        else:
                            episode.delivered_revision = max(
                                episode.delivered_revision, revision,
                            )
                            if episode.delivered_revision >= episode.current_revision:
                                episode.pending_alert_message = None
                if episode.in_flight is asyncio.current_task():
                    episode.in_flight_revision = None

        task = asyncio.create_task(deliver(), name=f"queue_health_alert_{queue_name}")
        episode.in_flight = task
        self.alert_tasks.add(task)
        task.add_done_callback(self.alert_tasks.discard)

    async def _send_to_admins(self, message: str) -> bool:
        admin_ids = tuple(get_settings().ADMIN_IDS)
        if not admin_ids:
            logger.warning("Queue health alert has no configured recipients")
            return False

        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.button(text="✖ Скрыть", callback_data="dismiss_notification")
        reply_markup = builder.as_markup()

        async def send_one(admin_id: int) -> bool:
            try:
                await self.bot.send_message(
                    admin_id, message, parse_mode="HTML", reply_markup=reply_markup
                )
                return True
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.error("Queue health admin delivery failed admin=%s type=%s",
                             admin_id, type(error).__name__)
                return False

        results = await asyncio.gather(*(send_one(admin_id) for admin_id in admin_ids))
        return any(results)

    @staticmethod
    def _unhealthy_message(queue: QueueSnapshot) -> str:
        problems = []
        if queue.dead:
            problems.append(f"{queue.dead} dead (oldest {queue.oldest_dead_age_seconds}s)")
        if queue.overdue:
            problems.append(f"{queue.overdue} overdue (oldest {queue.oldest_due_age_seconds}s)")
        if queue.stale_processing:
            problems.append(f"{queue.stale_processing} stale (oldest {queue.oldest_stale_age_seconds}s)")
        examples = []
        for item in queue.examples:
            payment = f" payment={item.payment_id}" if item.payment_id is not None else ""
            code = f" code={html.escape(item.last_error_code)}" if item.last_error_code else ""
            examples.append(
                f"<code>id={item.operation_id}{payment} "
                f"type={html.escape(item.operation_type)} status={item.status} "
                f"attempts={item.attempts}/{item.max_attempts} "
                f"age={item.age_seconds}s{code}</code>"
            )
        suffix = "\n" + "\n".join(examples) if examples else ""
        return (
            f"🚨 <b>Durable queue unhealthy</b>\n"
            f"Queue: <code>{queue.name}</code>\n"
            f"Problems: {', '.join(problems)}{suffix}"
        )

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
        for episode in self.episodes.values():
            episode.in_flight = None


async def queue_health_loop(bot: Bot, shutdown_event: asyncio.Event, *,
                            interval: float = CHECK_INTERVAL_SECONDS,
                            cooldown: float = REMINDER_COOLDOWN_SECONDS,
                            retry_cooldown: float = FAILED_RETRY_COOLDOWN_SECONDS,
                            clock: Callable[[], float] = time.monotonic,
                            snapshot_clock=None) -> None:
    monitor = QueueHealthMonitor(bot, cooldown=cooldown,
                                 retry_cooldown=retry_cooldown, clock=clock)
    try:
        while not shutdown_event.is_set():
            try:
                async with session_scope() as session:
                    snapshot = await get_payment_queue_health_snapshot(
                        session, clock=snapshot_clock,
                    )
                monitor.observe(snapshot)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Only the exception class is logged; DB messages can contain data.
                logger.error("Queue health check failed type=%s", type(error).__name__)
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=max(0.001, interval))
            except asyncio.TimeoutError:
                pass
    finally:
        await monitor.close()
