"""Lifecycle supervision and in-memory health for background workers."""
from bot import texts

import asyncio
import logging
import time
from dataclasses import asdict, dataclass
from typing import Awaitable, Callable

from aiogram import Bot

from config.settings import get_settings
from .api_operations import api_operations_loop
from .account_balance import account_balance_notifications_loop
from .cleanup import cleanup_dangling_peers_loop
from .heartbeat import heartbeat_loop, set_bot_ref
from .notifications import subscription_notifications_loop
from .payment_pipeline import payment_pipeline_loop
from .queue_health import queue_health_loop
from .payments import stale_payments_checker_loop
from .traffic import traffic_sync_loop

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


# Durable API and financial workers are critical. The remaining workers provide periodic
# maintenance/notifications/telemetry and may stop after exhausting their budget
# without pretending that a durable customer operation is still being processed.
WORKERS: tuple[WorkerDefinition, ...] = (
    WorkerDefinition("traffic", _traffic, False),
    WorkerDefinition("cleanup", _cleanup, False),
    WorkerDefinition("stale_payments", _stale_payments, False),
    WorkerDefinition("notifications", _notifications, False),
    WorkerDefinition("account_balance", _account_balance, False),
    WorkerDefinition("heartbeat", _heartbeat, False),
    WorkerDefinition("queue_health", _queue_health, False),
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
