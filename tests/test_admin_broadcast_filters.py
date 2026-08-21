import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from bot.handlers.admin.broadcast import _get_next_batch


class TestAdminBroadcastFilters(unittest.TestCase):
    def test_get_next_batch_applies_audience_predicates(self):
        async def _test():
            session = AsyncMock()
            session.begin_nested.return_value.__aenter__.return_value = session
            session.begin_nested.return_value.__aexit__.return_value = None
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
            session.begin_nested.return_value.__aenter__.return_value = session
            session.begin_nested.return_value.__aexit__.return_value = None
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

    def test_broadcast_ui_launch_executes_successfully(self):
        from unittest.mock import patch
        from bot.handlers.admin.broadcast import (
            _start_broadcast_process,
            _active_broadcast_progress_ids,
            _broadcast_in_progress,
        )
        from database.models import BroadcastProgress, User

        async def _test():
            _active_broadcast_progress_ids.clear()
            _broadcast_in_progress.clear()

            callback = AsyncMock()
            callback.from_user.id = 12345
            callback.bot = AsyncMock()
            callback.message.edit_text = AsyncMock()

            state = AsyncMock()
            state.get_data.return_value = {
                "broadcast_text": "UI launch test",
                "media_id": None,
                "content_type": "text",
            }

            progress_record = None

            class FakeSession:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    pass

                async def scalar(self, stmt):
                    return 0

                async def execute(self, stmt):
                    res = MagicMock()
                    res.scalar_one.return_value = 1
                    res.all.return_value = [(1, 1001)]
                    return res

                def add(self, obj):
                    nonlocal progress_record
                    obj.id = 77
                    progress_record = obj

                async def commit(self):
                    pass

                async def flush(self):
                    pass

                async def refresh(self, obj):
                    pass

                async def get(self, model, ident):
                    if model == BroadcastProgress:
                        return progress_record
                    if model == User:
                        return User(id=1, telegram_id=1001, is_bot_blocked=False)
                    return None

            dispatch_called = []

            async def fake_dispatch(*args, **kwargs):
                dispatch_called.append(1)

            with patch("bot.handlers.admin.broadcast.session_scope", side_effect=FakeSession), \
                 patch("bot.handlers.admin.broadcast._get_next_batch", side_effect=[[(1, 1001)], []]), \
                 patch("bot.handlers.admin.broadcast._dispatch_message", side_effect=fake_dispatch):

                await _start_broadcast_process(callback, state, FakeSession(), "all")
                await asyncio.sleep(0.05)

            self.assertEqual(len(dispatch_called), 1)
            self.assertEqual(len(_active_broadcast_progress_ids), 0)
            self.assertEqual(len(_broadcast_in_progress), 0)

        asyncio.run(_test())

    def test_broadcast_resume_pending_executes_successfully(self):
        from unittest.mock import patch
        from bot.handlers.admin.broadcast import (
            resume_pending_broadcasts,
            _active_broadcast_progress_ids,
            _broadcast_in_progress,
        )
        from database.models import BroadcastProgress

        async def _test():
            _active_broadcast_progress_ids.clear()
            _broadcast_in_progress.clear()

            mock_bot = AsyncMock()

            progress = BroadcastProgress(
                id=10,
                admin_id=12345,
                target_audience="all",
                broadcast_text="Resume test",
                media_id=None,
                content_type="text",
                status="in_progress",
                last_processed_id=0,
                success_count=0,
                fail_count=0,
            )

            class FakeSession:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    pass

                def add(self, obj):
                    pass

                async def flush(self):
                    pass

                async def refresh(self, obj):
                    pass

                async def execute(self, stmt):
                    res = MagicMock()
                    scalars = MagicMock()
                    scalars.all.return_value = [progress]
                    res.scalars.return_value = scalars
                    return res

                async def get(self, model, ident):
                    if model == BroadcastProgress:
                        return progress
                    return None

                async def commit(self):
                    pass

            dispatch_called = []

            async def fake_dispatch(*args, **kwargs):
                dispatch_called.append(1)

            with patch("bot.handlers.admin.broadcast.session_scope", side_effect=FakeSession), \
                 patch("bot.handlers.admin.broadcast._get_next_batch", side_effect=[[(1, 1001)], []]), \
                 patch("bot.handlers.admin.broadcast._dispatch_message", side_effect=fake_dispatch):

                await resume_pending_broadcasts(mock_bot)
                await asyncio.sleep(0.05)

            self.assertEqual(len(dispatch_called), 1)
            self.assertEqual(len(_active_broadcast_progress_ids), 0)
            self.assertEqual(len(_broadcast_in_progress), 0)

        asyncio.run(_test())

    def test_start_broadcast_process_concurrent_race_protection(self):
        """Verify that concurrent calls to _start_broadcast_process for the same admin
        are serialized/locked before the first await, creating exactly 1 progress record
        and returning BROADCAST_ALREADY_RUNNING to the duplicate invocation."""
        from unittest.mock import patch
        from bot.handlers.admin.broadcast import (
            _start_broadcast_process,
            _active_broadcast_progress_ids,
            _broadcast_in_progress,
        )
        from database.models import BroadcastProgress
        from bot import texts

        async def _test():
            _active_broadcast_progress_ids.clear()
            _broadcast_in_progress.clear()

            admin_id = 777888
            callback1 = AsyncMock()
            callback1.from_user.id = admin_id
            callback1.bot = AsyncMock()
            callback1.message.edit_text = AsyncMock()

            callback2 = AsyncMock()
            callback2.from_user.id = admin_id
            callback2.bot = AsyncMock()
            callback2.message.edit_text = AsyncMock()

            state = AsyncMock()
            state.get_data.return_value = {
                "broadcast_text": "Concurrent UI test",
                "media_id": None,
                "content_type": "text",
            }

            created_records = []

            class FakeSession:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    pass

                def add(self, obj):
                    if isinstance(obj, BroadcastProgress):
                        obj.id = 999
                        created_records.append(obj)

                async def flush(self):
                    pass

                async def refresh(self, obj):
                    pass

                async def commit(self):
                    pass

                async def scalar(self, stmt):
                    # Simulate DB check with small async delay to test pre-await lock
                    await asyncio.sleep(0.01)
                    return 0

                async def execute(self, stmt):
                    mock_res = MagicMock()
                    mock_res.scalar_one.return_value = 10
                    return mock_res

            def fake_start_task(coro):
                coro.close()
                return MagicMock()

            with patch("bot.handlers.admin.broadcast.session_scope", side_effect=FakeSession), \
                 patch("bot.handlers.admin.broadcast._start_background_task", side_effect=fake_start_task) as mock_start_task:

                # Launch two concurrent _start_broadcast_process calls
                await asyncio.gather(
                    _start_broadcast_process(callback1, state, FakeSession(), "all"),
                    _start_broadcast_process(callback2, state, FakeSession(), "all"),
                )

            # Exactly one progress record should be created
            self.assertEqual(len(created_records), 1)
            # Exactly one background worker task should be launched
            self.assertEqual(mock_start_task.call_count, 1)

            # One callback was answered with BROADCAST_ALREADY_RUNNING
            answers1 = [call.args[0] for call in callback1.answer.call_args_list if call.args]
            answers2 = [call.args[0] for call in callback2.answer.call_args_list if call.args]
            all_answers = answers1 + answers2
            self.assertIn(texts.BROADCAST_ALREADY_RUNNING, all_answers)

            _active_broadcast_progress_ids.clear()
            _broadcast_in_progress.clear()

        asyncio.run(_test())

    def test_start_broadcast_process_task_failure_cleans_up_state(self):
        """Verify that if _start_background_task fails during launch,
        BroadcastProgress is updated to status='stopped' and admin_id is discarded from memory."""
        from unittest.mock import patch
        from bot.handlers.admin.broadcast import (
            _start_broadcast_process,
            _active_broadcast_progress_ids,
            _broadcast_in_progress,
        )
        from database.models import BroadcastProgress

        async def _test():
            _active_broadcast_progress_ids.clear()
            _broadcast_in_progress.clear()

            admin_id = 999111
            callback = AsyncMock()
            callback.from_user.id = admin_id
            callback.bot = AsyncMock()

            state = AsyncMock()
            state.get_data.return_value = {
                "broadcast_text": "Launch failure test",
                "media_id": None,
                "content_type": "text",
            }

            stopped_ids = []

            class FakeSession:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    pass

                def add(self, obj):
                    if isinstance(obj, BroadcastProgress):
                        obj.id = 888

                async def refresh(self, obj):
                    pass

                async def commit(self):
                    pass

                async def scalar(self, stmt):
                    return 0

                async def execute(self, stmt):
                    mock_res = MagicMock()
                    mock_res.scalar_one.return_value = 5
                    # Check for update status='stopped'
                    if "UPDATE" in str(stmt).upper() or "broadcast_progress" in str(stmt).lower():
                        stopped_ids.append(888)
                    return mock_res

            def failing_start_task(coro):
                coro.close()
                raise RuntimeError("Simulated background task executor failure")

            with patch("bot.handlers.admin.broadcast.session_scope", side_effect=FakeSession), \
                 patch("bot.handlers.admin.broadcast._start_background_task", side_effect=failing_start_task):

                with self.assertRaises(RuntimeError):
                    await _start_broadcast_process(callback, state, FakeSession(), "all")

            # Must mark progress record as stopped in database
            self.assertIn(888, stopped_ids)
            # Must remove admin_id from in-progress set
            self.assertNotIn(admin_id, _broadcast_in_progress)

            _active_broadcast_progress_ids.clear()
            _broadcast_in_progress.clear()

        asyncio.run(_test())

    def test_resume_pending_broadcasts_task_failure_cleans_up_state(self):
        """Verify that if _start_background_task fails during resume,
        BroadcastProgress is updated to status='stopped' and admin_id is discarded from memory."""
        from unittest.mock import patch
        from bot.handlers.admin.broadcast import (
            resume_pending_broadcasts,
            _active_broadcast_progress_ids,
            _broadcast_in_progress,
        )
        from database.models import BroadcastProgress

        async def _test():
            _active_broadcast_progress_ids.clear()
            _broadcast_in_progress.clear()

            admin_id = 999222
            mock_bot = AsyncMock()

            progress = BroadcastProgress(
                id=777,
                admin_id=admin_id,
                target_audience="all",
                broadcast_text="Resume failure test",
                media_id=None,
                content_type="text",
                status="in_progress",
                last_processed_id=0,
                success_count=0,
                fail_count=0,
            )

            stopped_ids = []

            class FakeSession:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    pass

                def add(self, obj):
                    pass

                async def commit(self):
                    pass

                async def execute(self, stmt):
                    res = MagicMock()
                    scalars = MagicMock()
                    scalars.all.return_value = [progress]
                    res.scalars.return_value = scalars
                    if "UPDATE" in str(stmt).upper() or "broadcast_progress" in str(stmt).lower():
                        stopped_ids.append(777)
                    return res

            def failing_start_task(coro):
                coro.close()
                raise RuntimeError("Simulated resume executor failure")

            with patch("bot.handlers.admin.broadcast.session_scope", side_effect=FakeSession), \
                 patch("bot.handlers.admin.broadcast._start_background_task", side_effect=failing_start_task):

                await resume_pending_broadcasts(mock_bot)

            # Must mark progress record as stopped in database
            self.assertIn(777, stopped_ids)
            # Must remove admin_id from in-progress set
            self.assertNotIn(admin_id, _broadcast_in_progress)

            _active_broadcast_progress_ids.clear()
            _broadcast_in_progress.clear()

        asyncio.run(_test())





