from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import timedelta

from services.api_operations_executor import execute_claimed_api_operation
from services.api_operations_finalizer import finalize_operation_failure
from services.api_operations_queue import (
    claim_api_operations,
    recover_stale_api_operations,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)
PROCESS_ID = uuid.uuid4()
MAX_CONCURRENCY = 5

_wake_event: asyncio.Event | None = None
_bot: Bot | None = None
_alerted_dead_ops: set[int] = set()


def set_api_operations_bot(bot: Bot | None) -> None:
    global _bot
    _bot = bot


def get_api_operations_bot() -> Bot | None:
    return _bot


def clear_alerted_dead_ops() -> None:
    _alerted_dead_ops.clear()


async def notify_dead_operation(
    *,
    operation_id: int,
    operation_type: str,
    server_id: int | None,
    server_name: str | None,
    profile_id: int | None,
    client_name: str | None,
    error_code: str,
    error_message: str,
) -> None:
    """Notify administrators via Telegram when an API operation permanently fails (dead-lettered)."""
    if operation_id in _alerted_dead_ops:
        return
    bot = get_api_operations_bot()
    if not bot:
        return
    try:
        from bot.texts.runtime.alerts import ALERT_API_OPERATION_DEAD
        from config.settings import get_settings
        from utils.telegram import safe, safe_send_message

        settings = get_settings()
        admin_ids = getattr(settings, "ADMIN_IDS", None)
        if not admin_ids:
            return

        text = ALERT_API_OPERATION_DEAD.format(
            server_name=safe(server_name or "—"),
            server_id=server_id or "—",
            op_type=safe(operation_type),
            op_id=operation_id,
            profile_id=profile_id or "—",
            client_name=safe(client_name or "—"),
            error_code=safe(error_code or "unknown"),
            error_details=safe((error_message or "—")[:300]),
        )

        for admin_id in admin_ids:
            try:
                await safe_send_message(bot, chat_id=admin_id, text=text, parse_mode="HTML")
            except Exception:
                logger.exception("Failed to send dead operation alert to admin %s", admin_id)

        _alerted_dead_ops.add(operation_id)
        if len(_alerted_dead_ops) > 1000:
            for old_id in list(_alerted_dead_ops)[:500]:
                _alerted_dead_ops.discard(old_id)
    except Exception:
        logger.exception("Unexpected error in notify_dead_operation for op_id=%s", operation_id)


def get_wake_event() -> asyncio.Event:
    global _wake_event
    if _wake_event is None:
        _wake_event = asyncio.Event()
    return _wake_event


def notify_api_operation_enqueued() -> None:
    event = get_wake_event()
    event.set()


async def api_operations_loop(shutdown_event: asyncio.Event, bot: Bot | None = None) -> None:
    if bot is not None:
        set_api_operations_bot(bot)
    worker_id = f"api-operations-{PROCESS_ID}"
    in_flight: set[asyncio.Task] = set()
    async def run(operation):
        try:
            await execute_claimed_api_operation(operation)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.exception("operation failed operation_id=%s type=%s error=%s",
                             operation.id, operation.operation_type, type(error).__name__)
            try:
                await finalize_operation_failure(operation.id,
                    worker_id=operation.locked_by,
                    expected_attempt_number=operation.attempt_number,
                    retryable=True, error_code="executor_exception",
                    error_message="executor_exception")
            except Exception:
                logger.error("could not release failed operation_id=%s", operation.id)
    try:
        while not shutdown_event.is_set():
            await recover_stale_api_operations(lease_timeout=timedelta(minutes=5))
            available = MAX_CONCURRENCY - len(in_flight)
            if available == 0:
                shutdown_task = asyncio.create_task(shutdown_event.wait())
                done, _ = await asyncio.wait(in_flight | {shutdown_task},
                                             return_when=asyncio.FIRST_COMPLETED)
                if shutdown_task not in done:
                    shutdown_task.cancel()
                continue
            operations = await claim_api_operations(worker_id=worker_id, limit=available)
            for operation in operations:
                task = asyncio.create_task(run(operation))
                in_flight.add(task)
                task.add_done_callback(in_flight.discard)
            if operations:
                shutdown_task = asyncio.create_task(shutdown_event.wait())
                done, _ = await asyncio.wait(in_flight | {shutdown_task},
                                             return_when=asyncio.FIRST_COMPLETED)
                if shutdown_task not in done:
                    shutdown_task.cancel()
            else:
                wake_event = get_wake_event()
                wake_task = asyncio.create_task(wake_event.wait())
                shutdown_task = asyncio.create_task(shutdown_event.wait())
                try:
                    done, _ = await asyncio.wait(
                        {wake_task, shutdown_task},
                        timeout=0.5,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if wake_task in done:
                        wake_event.clear()
                finally:
                    if not wake_task.done():
                        wake_task.cancel()
                    if not shutdown_task.done():
                        shutdown_task.cancel()
    finally:
        if in_flight:
            await asyncio.gather(*in_flight, return_exceptions=True)
