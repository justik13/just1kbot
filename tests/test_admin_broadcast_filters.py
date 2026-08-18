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

    def test_broadcast_resume_durable_checkpoint(self):
        from bot.handlers.admin.broadcast import _send_broadcast_to_users_with_resume
        from database.models import BroadcastProgress
        from unittest.mock import patch

        async def _test():
            mock_bot = AsyncMock()
            admin_id = 999
            progress_id = 42

            initial_progress = BroadcastProgress(
                id=progress_id,
                admin_id=admin_id,
                target_audience="all",
                broadcast_text="Hello",
                media_id=None,
                content_type="text",
                status="in_progress",
                last_processed_id=100,
                success_count=5,
                fail_count=0,
            )

            # Simulated users batch: (101, 1001), (102, 1002)
            batches = [[(101, 1001), (102, 1002)], []]
            batch_iter = iter(batches)

            class FakeSession:
                def __init__(self):
                    self.progress = initial_progress

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    pass

                async def get(self, model, ident):
                    if model == BroadcastProgress:
                        return self.progress
                    return None

                async def commit(self):
                    pass

            fake_sess = FakeSession()

            async def fake_get_next_batch(session, target_aud, last_id, limit=50):
                return next(batch_iter, [])

            with patch("bot.handlers.admin.broadcast.session_scope", return_value=fake_sess), \
                 patch("bot.handlers.admin.broadcast._get_next_batch", side_effect=fake_get_next_batch), \
                 patch("bot.handlers.admin.broadcast._dispatch_message", new_callable=AsyncMock) as mock_dispatch:

                await _send_broadcast_to_users_with_resume(mock_bot, admin_id, progress_id)

                self.assertEqual(mock_dispatch.call_count, 2)
                self.assertEqual(fake_sess.progress.last_processed_id, 102)
                self.assertEqual(fake_sess.progress.success_count, 7)
                self.assertEqual(fake_sess.progress.status, "completed")

        asyncio.run(_test())

