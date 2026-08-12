"""Lifecycle supervision and in-memory health for background workers."""
from bot import texts

import asyncio
import logging
import time
from dataclasses import asdict, dataclass
from typing import Awaitable, Callable

import aiohttp
from aiogram import Bot

from config.settings import get_settings


from .api_operations import api_operations_loop
from .account_balance import account_balance_notifications_loop
from .cleanup import cleanup_dangling_peers_loop
from .heartbeat import heartbeat_loop
from .notifications import subscription_notifications_loop
from .payment_pipeline import payment_pipeline_loop
from .queue_health import queue_health_loop
from .payments import stale_payments_checker_loop
from .traffic import traffic_sync_loop
from .node_monitor import node_monitor_loop


class _ExpectedNodeMonitorNetworkWarningFilter(logging.Filter):
    """Keep expected healthcheck network failures out of WARNING logs."""

    _EXPECTED = (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError, OSError)

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "services.workers.node_monitor" or record.levelno < logging.WARNING:
            return True
        if record.msg in {
            "Healthcheck exception for server %s (%s): %s",
            "Error reading server load for server %s: %s",
        }:
            args = record.args
            exc = args[-1] if args else None
            if isinstance(exc, self._EXPECTED):
                return False
        return True


_node_monitor_logger = logging.getLogger("services.workers.node_monitor")


if not any(isinstance(f, _ExpectedNodeMonitorNetworkWarningFilter) for f in _node_monitor_logger.filters):
    _node_monitor_logger.addFilter(_ExpectedNodeMonitorNetworkWarningFilter())

logger = logging.getLogger(__name__)
shutdown_event = asyncio.Event()

WorkerFactory = Callable[[Bot], Awaitable[None]]


@dataclass(frozen=True)
class WorkerDefinition:
    name: str
    factory: WorkerFactory
    critical: bool
    max_consecutive_failures: int = 10
    stability_window: float = 300.0


@dataclass
class WorkerHealth:
    state: str
    last_started_at: float | None
    last_finished_at: float | None
    consecutive_failures: int
    last_error_type: str | None
    critical: bool


_worker_tasks: dict[str, asyncio.Task] = {}
_worker_health: dict[str, WorkerHealth] = {}
_supervisor_task: asyncio.Task | None = None
_alert_tasks: set[asyncio.Task] = set()
_alert_keys: set[str] = set()
_fatal_shutdown = False
_supervisor_healthy = False
_started_at: float | None = None

_SUPERVISOR_CHECK_INTERVAL = 15.0
_STARTUP_GRACE_PERIOD = 30.0
_ALERT_DELIVERY_TIMEOUT = 5.0
_ALERT_SHUTDOWN_GRACE = 1.0


def _traffic(bot): return traffic_sync_loop(shutdown_event)
def _cleanup(bot): return cleanup_dangling_peers_loop(shutdown_event)
def _stale_payments(bot): return stale_payments_checker_loop(bot, shutdown_event)
def _notifications(bot): return subscription_notifications_loop(bot, shutdown_event)
def _heartbeat(bot): return heartbeat_loop(shutdown_event, heartbeat_allowed)
def _api_operations(bot): return api_operations_loop(shutdown_event)
def _account_balance(bot): return account_balance_notifications_loop(bot, shutdown_event)
def _payment_pipeline(bot): return payment_pipeline_loop(bot, shutdown_event)
def _queue_health(bot): return queue_health_loop(bot, shutdown_event)
def _node_monitor(bot): return node_monitor_loop(bot, shutdown_event)


WORKERS: tuple[WorkerDefinition, ...] = (
    WorkerDefinition("traffic", _traffic, False),
    WorkerDefinition("cleanup", _cleanup, False),
    WorkerDefinition("stale_payments", _stale_payments, False),
    WorkerDefinition("notifications", _notifications, False),
    WorkerDefinition("account_balance", _account_balance, False),
    WorkerDefinition("heartbeat", _heartbeat, False),
    WorkerDefinition("queue_health", _queue_health, False),
    WorkerDefinition("node_monitor", _node_monitor, False),
    WorkerDefinition("api_operations", _api_operations, True),
    WorkerDefinition("payment_pipeline", _payment_pipeline, True),
)

_WORKERS_BY_NAME = {definition.name: definition for definition in WORKERS}


def get_worker_health_snapshot() -> dict[str, object]:
    """Return a read-only, secret-free copy suitable for health checks/tests."""
    return {
        "supervisor_healthy": _supervisor_healthy,
        "fatal_shutdown": _fatal_shutdown,
        "workers": {name: asdict(health) for name, health in _worker_health.items()},
    }


def heartbeat_allowed(*, now: float | None = None) -> bool:
    if _fatal_shutdown or not _supervisor_healthy or shutdown_event.is_set():
        return False
    current = time.monotonic() if now is None else now
    in_grace = _started_at is not None and current - _started_at < _STARTUP_GRACE_PERIOD
    for definition in WORKERS:
        if not definition.critical:
            continue
        health = _worker_health.get(definition.name)
        task = _worker_tasks.get(definition.name)
        if health is None or health.state in {"failed", "stopped"}:
            return False
        if not in_grace and (health.state != "running" or task is None or task.done()):
            return False
    return True


async def _send_alert(bot: Bot, title: str, worker: str,
                      failure_count: int, error_type: str) -> None:
    message = texts.RUNTIME_SERVICES_WORKERS_INIT_L119_1.format(value_0=title, value_1=worker, value_2=failure_count, value_3=error_type)
    try:
        for admin_id in get_settings().ADMIN_IDS:
            try:
                await bot.send_message(admin_id, message, parse_mode="HTML")
            except Exception:
                logger.exception("Failed to send worker alert to admin")
    except Exception:
        logger.exception("Failed to prepare worker alert")


def _schedule_alert(bot: Bot, key: str, title: str, worker: str,
                    failure_count: int, error_type: str, *,
                    timeout: float | None = None) -> None:
    if key in _alert_keys:
        return
    _alert_keys.add(key)
    delivery_timeout = _ALERT_DELIVERY_TIMEOUT if timeout is None else timeout

    async def deliver() -> None:
        try:
            await asyncio.wait_for(_send_alert(bot, title, worker, failure_count, error_type), timeout=delivery_timeout)
        except asyncio.TimeoutError:
            logger.warning("Worker alert delivery timed out: key=%s", key)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("Worker alert delivery failed: key=%s type=%s", key, type(error).__name__)

    task = asyncio.create_task(deliver(), name=f"worker_alert_{key}")
    _alert_tasks.add(task)
    task.add_done_callback(_alert_tasks.discard)


def _fatal(bot: Bot, worker: str, count: int, error_type: str) -> None:
    global _fatal_shutdown, _supervisor_healthy
    if _fatal_shutdown:
        return
    _fatal_shutdown = True
    _supervisor_healthy = False
    shutdown_event.set()
    logger.critical("Fatal background failure: worker=%s failures=%s type=%s", worker, count, error_type)
    _schedule_alert(bot, "fatal", texts.RUNTIME_SERVICES_WORKERS_INIT_L174_1, worker, count, error_type)


def _spawn(definition: WorkerDefinition, bot: Bot, now: float) -> None:
    health = _worker_health[definition.name]
    health.state = "running"
    health.last_started_at = now
    _worker_tasks[definition.name] = asyncio.create_task(definition.factory(bot), name=f"worker_{definition.name}")


async def _supervise_workers(bot: Bot, *, check_interval: float | None = None,
                             clock: Callable[[], float] = time.monotonic,
                             backoff_delay: Callable[[int], float] | None = None) -> None:
    global _supervisor_healthy
    interval = _SUPERVISOR_CHECK_INTERVAL if check_interval is None else check_interval
    _supervisor_healthy = True
    logger.info("Worker supervisor started")
    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            pass

        for name, task in list(_worker_tasks.items()):
            if shutdown_event.is_set():
                break
            definition = _WORKERS_BY_NAME[name]
            health = _worker_health[name]
            now = clock()
            if not task.done():
                if (health.consecutive_failures and health.last_started_at is not None
                        and now - health.last_started_at >= definition.stability_window):
                    health.consecutive_failures = 0
                    health.last_error_type = None
                    _alert_keys.discard(f"crash:{name}")
                continue
            if task.cancelled():
                error_type = "CancelledError"
            else:
                try:
                    exc = task.exception()
                except Exception as error:
                    exc = error
                error_type = type(exc).__name__ if exc is not None else "UnexpectedReturn"
            ran_for = now - (health.last_started_at if health.last_started_at is not None else now)
            if ran_for >= definition.stability_window:
                health.consecutive_failures = 0
                _alert_keys.discard(f"crash:{name}")
            health.consecutive_failures += 1
            health.last_finished_at = now
            health.last_error_type = error_type
            health.state = "backoff"
            count = health.consecutive_failures
            logger.critical("Worker %s died unexpectedly: %s (failure %s)", name, error_type, count)
            _schedule_alert(bot, f"crash:{name}", texts.RUNTIME_SERVICES_WORKERS_INIT_L233_1, name, count, error_type)

            if count > definition.max_consecutive_failures:
                health.state = "failed"
                if definition.critical:
                    _fatal(bot, name, count, error_type)
                    break
                logger.critical("Non-critical worker %s exhausted restart budget", name)
                _worker_tasks.pop(name, None)
                continue

            backoff = (backoff_delay(count) if backoff_delay is not None else min(30.0, 2.0 ** count))
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=backoff)
                break
            except asyncio.TimeoutError:
                pass
            if not shutdown_event.is_set():
                _spawn(definition, bot, clock())
    _supervisor_healthy = False
    logger.info("Worker supervisor stopped")


def _supervisor_done(task: asyncio.Task, bot: Bot) -> None:
    if task.cancelled() or shutdown_event.is_set():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    error_type = type(exc).__name__ if exc is not None else "UnexpectedReturn"
    _fatal(bot, "supervisor", 1, error_type)


async def start_background_workers(bot: Bot) -> list[asyncio.Task]:
    global _supervisor_task, _fatal_shutdown, _supervisor_healthy, _started_at
    shutdown_event.clear()
    _worker_tasks.clear()
    _worker_health.clear()
    _alert_keys.clear()
    _fatal_shutdown = False
    _supervisor_healthy = True
    _started_at = time.monotonic()
    for definition in WORKERS:
        _worker_health[definition.name] = WorkerHealth("starting", None, None, 0, None, definition.critical)
        _spawn(definition, bot, _started_at)
    _supervisor_task = asyncio.create_task(_supervise_workers(bot), name="worker_supervisor")
    _supervisor_task.add_done_callback(lambda task: _supervisor_done(task, bot))
    return [*_worker_tasks.values(), _supervisor_task]


async def stop_background_workers(*, alert_grace_timeout: float | None = None) -> None:
    global _supervisor_task, _supervisor_healthy
    shutdown_event.set()
    if _supervisor_task is not None:
        _supervisor_task.cancel()
        await asyncio.gather(_supervisor_task, return_exceptions=True)
        _supervisor_task = None
    tasks = list(_worker_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _worker_tasks.clear()
    for health in _worker_health.values():
        if health.state != "failed":
            health.state = "stopped"
    alerts = list(_alert_tasks)
    if alerts:
        grace = _ALERT_SHUTDOWN_GRACE if alert_grace_timeout is None else alert_grace_timeout
        _, pending = await asyncio.wait(alerts, timeout=grace)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    _alert_tasks.clear()
    _supervisor_healthy = False
    logger.info("Background workers stopped")
