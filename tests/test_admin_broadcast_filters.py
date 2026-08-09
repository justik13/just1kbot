import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from bot.handlers.admin.broadcast import _get_next_batch


class TestAdminBroadcastFilters(unittest.TestCase):
    def test_get_next_batch_applies_audience_predicates(self):
        async def _test():
            session = AsyncMock()
            mock_result = MagicMock()
            mock_result.all.return_value = [(1, 1000), (2, 2000)]
            session.execute.return_value = mock_result

            expected_fragments = {
                "all": ["users.id >"],
                "active": ["users.subscription_end >"],
                "expiring_3d": [
                    "users.subscription_end >",
                    "users.subscription_end <=",
                ],
                "expired": [
                    "users.subscription_end IS NOT NULL",
                    "users.subscription_end <=",
                ],
                "never": ["users.subscription_end IS NULL"],
                "test_12345": ["users.telegram_id ="],
            }

            for audience, fragments in expected_fragments.items():
                session.execute.reset_mock()
                batch = await _get_next_batch(session, audience, last_id=0, limit=50)

                self.assertEqual(batch, [(1, 1000), (2, 2000)])
                statement = session.execute.await_args.args[0]
                compiled = str(statement.compile(compile_kwargs={"literal_binds": False}))

                for fragment in fragments:
                    self.assertIn(fragment, compiled, msg=f"Missing {fragment!r} for {audience}")

                self.assertIn("users.id >", compiled)
                self.assertIn("LIMIT", compiled)

        asyncio.run(_test())

    def test_invalid_test_audience_does_not_broaden_query(self):
        async def _test():
            session = AsyncMock()
            mock_result = MagicMock()
            mock_result.all.return_value = []
            session.execute.return_value = mock_result

            await _get_next_batch(session, "test_not_an_integer", last_id=100, limit=10)
            statement = session.execute.await_args.args[0]
            compiled = str(statement.compile(compile_kwargs={"literal_binds": False}))

            self.assertIn("users.id >", compiled)
            self.assertNotIn("users.telegram_id =", compiled)

        asyncio.run(_test())
