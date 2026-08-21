import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from services.api_operations_queue import ClaimedAPIOperation
from services.workers import api_operations as worker


class WorkerBackpressureTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_backpressure_claims_only_available_capacity(self):
        stop = asyncio.Event()
        release = asyncio.Event()
        limits = []

        def operation(i):
            return ClaimedAPIOperation(
                i,
                "delete_peer",
                str(i),
                None,
                None,
                "s",
                "u",
                "k",
                "p",
                None,
                {"managed_workflow": True},
                1,
                10,
                "w",
                None,
                None,
            )

        async def claim(**kw):
            limits.append(kw["limit"])
            if len(limits) == 1:
                return [operation(i) for i in range(kw["limit"])]
            stop.set()
            return []

        async def execute(op):
            await release.wait()

        with (
            patch.object(worker, "claim_api_operations", side_effect=claim),
            patch.object(worker, "recover_stale_api_operations", new=AsyncMock()),
            patch.object(worker, "execute_claimed_api_operation", side_effect=execute),
        ):
            task = asyncio.create_task(worker.api_operations_loop(stop))
            await asyncio.sleep(0.05)
            self.assertEqual(limits, [worker.MAX_CONCURRENCY])
            release.set()
            await asyncio.wait_for(task, 1)
