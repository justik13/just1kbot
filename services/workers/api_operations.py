import asyncio
import logging
import uuid
from datetime import timedelta

from services.api_operations_executor import execute_claimed_api_operation
from services.api_operations_queue import claim_api_operations, recover_stale_api_operations

logger = logging.getLogger(__name__)
PROCESS_ID = uuid.uuid4()


async def api_operations_loop(shutdown_event: asyncio.Event) -> None:
    worker_id = f"api-operations-{PROCESS_ID}"
    semaphore = asyncio.Semaphore(5)
    in_flight: set[asyncio.Task] = set()
    async def run(operation):
        async with semaphore:
            try:
                await execute_claimed_api_operation(operation)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception("operation failed operation_id=%s type=%s error=%s",
                                 operation.id, operation.operation_type, type(error).__name__)
    try:
        while not shutdown_event.is_set():
            await recover_stale_api_operations(lease_timeout=timedelta(minutes=5))
            operations = await claim_api_operations(worker_id=worker_id, limit=10)
            for operation in operations:
                task = asyncio.create_task(run(operation))
                in_flight.add(task)
                task.add_done_callback(in_flight.discard)
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=2 if operations else 5)
            except asyncio.TimeoutError:
                pass
    finally:
        if in_flight:
            await asyncio.gather(*in_flight, return_exceptions=True)
