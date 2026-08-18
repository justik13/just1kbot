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

                await _send_broadcast_to_users_with_resume(mock_bot, progress_id, admin_id)

                self.assertEqual(mock_dispatch.call_count, 2)
                self.assertEqual(fake_sess.progress.last_processed_id, 102)
                self.assertEqual(fake_sess.progress.success_count, 7)
                self.assertEqual(fake_sess.progress.status, "completed")

        asyncio.run(_test())

    def test_broadcast_retry_after_and_retry_logging(self):
        from bot.handlers.admin.broadcast import _send_broadcast_to_users_with_resume
        from database.models import BroadcastProgress
        from aiogram.exceptions import TelegramRetryAfter
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
                success_count=0,
                fail_count=0,
            )

            batches = [[(101, 1001)], []]
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

            # First attempt raises RetryAfter, second attempt raises RuntimeError
            dispatch_calls = []

            async def fake_dispatch(*args, **kwargs):
                dispatch_calls.append(1)
                if len(dispatch_calls) == 1:
                    raise TelegramRetryAfter(method=MagicMock(), message="Flood control", retry_after=0)
                raise RuntimeError("Permanent failure on retry")

            with patch("bot.handlers.admin.broadcast.session_scope", return_value=fake_sess), \
                 patch("bot.handlers.admin.broadcast._get_next_batch", side_effect=lambda *a, **kw: next(batch_iter, [])), \
                 patch("bot.handlers.admin.broadcast._dispatch_message", side_effect=fake_dispatch), \
                 patch("asyncio.sleep", new_callable=AsyncMock), \
                 patch("bot.handlers.admin.broadcast.logger.error") as mock_log_err:

                await _send_broadcast_to_users_with_resume(mock_bot, progress_id, admin_id)

                self.assertEqual(len(dispatch_calls), 2)
                self.assertEqual(fake_sess.progress.fail_count, 1)
                self.assertEqual(fake_sess.progress.success_count, 0)
                # Verify retry error was logged
                mock_log_err.assert_called_with(
                    "Broadcast retry error for user %s: %s",
                    1001,
                    mock_log_err.call_args[0][2],
                )

        asyncio.run(_test())

    def test_broadcast_forbidden_marks_blocked(self):
        from bot.handlers.admin.broadcast import _send_broadcast_to_users_with_resume
        from database.models import BroadcastProgress, User
        from aiogram.exceptions import TelegramForbiddenError
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
                success_count=0,
                fail_count=0,
            )

            fake_user = User(id=101, telegram_id=1001, is_bot_blocked=False)
            batches = [[(101, 1001)], []]
            batch_iter = iter(batches)

            class FakeSession:
                def __init__(self):
                    self.progress = initial_progress
                    self.user = fake_user

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    pass

                async def get(self, model, ident):
                    if model == BroadcastProgress:
                        return self.progress
                    if model == User:
                        return self.user
                    return None

                async def commit(self):
                    pass

            fake_sess = FakeSession()

            async def fake_dispatch(*args, **kwargs):
                raise TelegramForbiddenError(method=MagicMock(), message="Forbidden: bot was blocked by the user")

            with patch("bot.handlers.admin.broadcast.session_scope", return_value=fake_sess), \
                 patch("bot.handlers.admin.broadcast._get_next_batch", side_effect=lambda *a, **kw: next(batch_iter, [])), \
                 patch("bot.handlers.admin.broadcast._dispatch_message", side_effect=fake_dispatch):

                await _send_broadcast_to_users_with_resume(mock_bot, progress_id, admin_id)

                self.assertEqual(fake_sess.progress.fail_count, 1)
                self.assertTrue(fake_user.is_bot_blocked)

        asyncio.run(_test())

    def test_broadcast_concurrent_workers_single_owner_protection(self):
        from unittest.mock import patch
        from bot.handlers.admin.broadcast import (
            _send_broadcast_to_users_with_resume,
            _active_broadcast_progress_ids,
            _broadcast_in_progress,
        )
        from database.models import BroadcastProgress

        async def _test():
            mock_bot = AsyncMock()
            admin_id = 999
            progress_id = 42

            initial_progress = BroadcastProgress(
                id=progress_id,
                admin_id=admin_id,
                target_audience="all",
                broadcast_text="Concurrent test",
                media_id=None,
                content_type="text",
                status="in_progress",
                last_processed_id=100,
                success_count=0,
                fail_count=0,
            )

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

            dispatch_count = 0
            async def fake_dispatch(*args, **kwargs):
                nonlocal dispatch_count
                dispatch_count += 1
                await asyncio.sleep(0.05)

            batches = [[(101, 1001), (102, 1002)], []]
            batch_iter = iter(batches)

            # Ensure clean state
            _active_broadcast_progress_ids.clear()
            _broadcast_in_progress.clear()

            with patch("bot.handlers.admin.broadcast.session_scope", return_value=fake_sess), \
                 patch("bot.handlers.admin.broadcast._get_next_batch", side_effect=lambda *a, **kw: next(batch_iter, [])), \
                 patch("bot.handlers.admin.broadcast._dispatch_message", side_effect=fake_dispatch):

                # Launch Worker 1 and Worker 2 concurrently
                task1 = asyncio.create_task(_send_broadcast_to_users_with_resume(mock_bot, progress_id, admin_id))
                await asyncio.sleep(0.01) # ensure task1 starts first
                task2 = asyncio.create_task(_send_broadcast_to_users_with_resume(mock_bot, progress_id, admin_id))

                await asyncio.gather(task1, task2)

                # Worker 2 was rejected/skipped, so exactly 2 messages dispatched (not 4)
                self.assertEqual(dispatch_count, 2)
                self.assertEqual(fake_sess.progress.success_count, 2)
                self.assertEqual(len(_active_broadcast_progress_ids), 0)
                self.assertEqual(len(_broadcast_in_progress), 0)

        asyncio.run(_test())



