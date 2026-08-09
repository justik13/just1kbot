import unittest
from unittest.mock import AsyncMock, MagicMock
import asyncio
from bot.handlers.admin.broadcast import _get_next_batch


class TestAdminBroadcastFilters(unittest.TestCase):
    def test_get_next_batch_executes_queries(self):
        async def _test():
            session = AsyncMock()
            mock_result = MagicMock()
            mock_result.all.return_value = [(1, 1000), (2, 2000)]
            session.execute.return_value = mock_result

            audiences = ["all", "active", "expiring_3d", "expired", "never", "test_12345"]
            for aud in audiences:
                batch = await _get_next_batch(session, aud, last_id=0, limit=50)
                self.assertEqual(len(batch), 2)
                self.assertEqual(batch[0], (1, 1000))

        asyncio.run(_test())
