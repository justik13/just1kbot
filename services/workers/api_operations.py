import asyncio
import logging
import uuid
from datetime import timedelta

from aiogram import Bot

from bot.texts.runtime.alerts import ALERT_API_OPERATION_DEAD
from config.settings import get_settings
from database.connection import session_scope
from database.models import APIOperation
from services.api_operations_executor import execute_claimed_api_operation
from services.api_operations_finalizer import finalize_operation_failure
from services.api_operations_queue import (
    claim_api_operations,
    recover_stale_api_operations,
)
from utils.telegram import safe, safe_send_message

logger = logging.getLogger(__name__)
PROCESS_ID = uuid.uuid4()
MAX_CONCURRENCY = 5

_wake_event: asyncio.Event | None = None
_dead_alerts_sent: set[int] = set()


def get_wake_event() -> asyncio.Event:
    global _wake_event
    if _wake_event is None:
        _wake_event = asyncio.Event()
    return _wake_event


def notify_api_operation_enqueued() -> None:
    event = get_wake_event()
    event.set()


async def _send_dead_operation_alert(bot: Bot | None, operation: APIOperation) -> None:
    if not bot or operation.id in _dead_alerts_sent:
        return
    _dead_alerts_sent.add(operation.id)
    if len(_dead_alerts_sent) > 5000:
        _dead_alerts_sent.clear()

    settings = get_settings()
    admin_ids = settings.ADMIN_IDS
    if not admin_ids:
        return

    text = ALERT_API_OPERATION_DEAD.format(
        operation_type=safe(operation.operation_type or "unknown"),
        operation_id=operation.id,
        profile_id=operation.profile_id or "N/A",
        server_name=safe(operation.server_name_snapshot or "N/A"),
        server_id=operation.server_id or 0,
        error_code=safe(operation.last_error_code or "unknown"),
        error_message=safe((operation.last_error or "")[:300]),
        attempts=operation.attempts,
    )
    for aid in admin_ids:
        try:
            await safe_send_message(bot, chat_id=aid, text=text, parse_mode="HTML")
        except Exception as e:
            logger.warning("Failed to send dead operation alert to admin %s: %s", aid, e)


async def _check_and_alert_if_dead(bot: Bot | None, operation_id: int) -> None:
    if not bot or operation_id in _dead_alerts_sent:
        return
    try:
        async with session_scope() as session:
            op = await session.get(APIOperation, operation_id)
            if op and op.status == "dead":
                await _send_dead_operation_alert(bot, op)
    except Exception as exc:
        logger.warning("Failed to check dead operation status for op_id=%s: %s", operation_id, exc)


async def api_operations_loop(
    bot_or_event: Bot | asyncio.Event | None = None,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    if isinstance(bot_or_event, asyncio.Event) and shutdown_event is None:
        event = bot_or_event
        bot = None
    elif isinstance(bot_or_event, Bot):
        bot = bot_or_event
        event = shutdown_event or asyncio.Event()
    else:
        bot = None
        event = shutdown_event or asyncio.Event()

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
                    error_message=str(error))
            except Exception:
                logger.error("could not release failed operation_id=%s", operation.id)
        finally:
            if bot:
                await _check_and_alert_if_dead(bot, operation.id)

    try:
        while not event.is_set():
            recovered = await recover_stale_api_operations(lease_timeout=timedelta(minutes=5))
            if isinstance(recovered, (tuple, list)) and len(recovered) == 2:
                _, dead_recovered = recovered
                if dead_recovered > 0:
                    logger.warning("Recovered %d dead stale operations", dead_recovered)

            available = MAX_CONCURRENCY - len(in_flight)
            if available == 0:
                shutdown_task = asyncio.create_task(event.wait())
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
                shutdown_task = asyncio.create_task(event.wait())
                done, _ = await asyncio.wait(in_flight | {shutdown_task},
                                             return_when=asyncio.FIRST_COMPLETED)
                if shutdown_task not in done:
                    shutdown_task.cancel()
            else:
                wake_event = get_wake_event()
                wake_task = asyncio.create_task(wake_event.wait())
                shutdown_task = asyncio.create_task(event.wait())
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
