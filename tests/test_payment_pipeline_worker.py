import asyncio
import unittest
from unittest.mock import Mock

from services.workers import WORKERS
from services.workers.payment_pipeline import payment_pipeline_loop


class PaymentPipelineWorkerTests(unittest.IsolatedAsyncioTestCase):
    def test_payment_pipeline_worker_is_registered(self):
        self.assertIn("payment_pipeline", {w.name for w in WORKERS})

    async def test_payment_worker_graceful_shutdown(self):
        event = asyncio.Event()
        event.set()
        await asyncio.wait_for(payment_pipeline_loop(Mock(), event), 1)
