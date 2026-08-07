"""Production supervisor loop for all durable payment queues."""

import asyncio
import logging
import uuid

from database.connection import session_scope
from services import payment_provider_operations as provider
from services import provider_refunds
from services.workers import webhook_inbox

logger = logging.getLogger(__name__)
CONCURRENCY = 4
POLL_SECONDS = 1.0


async def _claim(module, worker_id):
    async with session_scope() as session:
        await module.recover_stale(session)
        return await module.claim(session, worker_id)


async def _run_claim(module, claim, bot=None):
    try:
        if module is provider:
            result = await provider.perform_http(claim)
            async with session_scope() as session:
                await provider.finalize(session, claim, result)
        elif module is provider_refunds:
            result = await provider_refunds.perform_http(claim)
            async with session_scope() as session:
                await provider_refunds.finalize(session, claim, result)
        elif module is webhook_inbox:
            result = await webhook_inbox.fetch_provider(claim)
            async with session_scope() as session:
                await webhook_inbox.finalize(session, claim, result, bot=bot)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error(
            "Payment operation failed queue=%s id=%s error=%s",
            module.__name__,
            getattr(claim, "operation_id", getattr(claim, "inbox_id", None)),
            type(exc).__name__,
        )
        try:
            async with session_scope() as session:
                if module is provider:
                    await provider.finalize_provider_failure(
                        session, claim, error_code=type(exc).__name__, retryable=True
                    )
                elif module is provider_refunds:
                    await provider_refunds.finalize_provider_failure(
                        session, claim, error_code=type(exc).__name__, retryable=True
                    )
                elif module is webhook_inbox:
                    await webhook_inbox.finalize_webhook_failure(
                        session, claim, error_code=type(exc).__name__, retryable=True
                    )
        except Exception:
            logger.error(
                "Payment failure finalizer rejected stale ownership queue=%s",
                module.__name__,
            )


async def payment_pipeline_loop(bot, shutdown_event: asyncio.Event) -> None:
    worker_id = uuid.uuid4().hex
    active: set[asyncio.Task] = set()
    queues = (provider, provider_refunds, webhook_inbox)
    cursor = 0
    logger.info("Payment pipeline worker started worker=%s", worker_id[:8])
    try:
        while not shutdown_event.is_set():
            active = {task for task in active if not task.done()}
            for _ in range(CONCURRENCY - len(active)):
                module = queues[cursor % len(queues)]
                cursor += 1
                claim = await _claim(module, worker_id)
                if claim:
                    active.add(asyncio.create_task(_run_claim(module, claim, bot=bot)))
            if active:
                done, _ = await asyncio.wait(
                    active, timeout=POLL_SECONDS, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    try:
                        await task
                    except asyncio.CancelledError:
                        raise
            else:
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=POLL_SECONDS)
                except asyncio.TimeoutError:
                    pass
    except asyncio.CancelledError:
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)
        raise
    finally:
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        logger.info("Payment pipeline worker stopped worker=%s", worker_id[:8])
