import asyncio
import logging
import uuid
from datetime import timedelta

from services.api_operations_executor import execute_claimed_api_operation
from services.api_operations_queue import (
    claim_api_operations, recover_stale_api_operations,
)
from services.api_operations_finalizer import finalize_operation_failure

logger = logging.getLogger(__name__)
PROCESS_ID = uuid.uuid4()
MAX_CONCURRENCY = 5


async def api_operations_loop(shutdown_event: asyncio.Event) -> None:
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
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
    finally:
        if in_flight:
            await asyncio.gather(*in_flight, return_exceptions=True)
