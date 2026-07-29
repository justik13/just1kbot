import asyncio, unittest
from unittest.mock import Mock, patch
from services.workers import _WORKER_FACTORIES
from services.workers.payment_pipeline import payment_pipeline_loop
class PaymentPipelineWorkerTests(unittest.IsolatedAsyncioTestCase):
 def test_payment_pipeline_worker_is_registered(self): self.assertIn("payment_pipeline",_WORKER_FACTORIES)
 async def test_payment_worker_graceful_shutdown(self):
  event=asyncio.Event(); event.set(); await asyncio.wait_for(payment_pipeline_loop(Mock(),event),1)
