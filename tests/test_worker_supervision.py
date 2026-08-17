import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import services.workers as workers
from services.workers import heartbeat


class WorkerSupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        workers.shutdown_event.clear()
        workers._worker_tasks.clear()
        workers._worker_health.clear()
        workers._alert_keys.clear()
        workers._alert_tasks.clear()
        workers._fatal_shutdown = False
        workers._supervisor_healthy = True
        workers._started_at = 0.0
        self.original_definitions = workers._WORKERS_BY_NAME
        self.bot = AsyncMock()
        self.settings = patch.object(
            workers, "get_settings", return_value=SimpleNamespace(ADMIN_IDS=[1])
        )
        self.settings.start()

    async def asyncTearDown(self):
        workers.shutdown_event.set()
        tasks = [task for task in [*workers._worker_tasks.values(), *workers._alert_tasks]
                 if isinstance(task, asyncio.Task)]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        workers._worker_tasks.clear()
        workers._alert_tasks.clear()
        workers._WORKERS_BY_NAME = self.original_definitions
        self.settings.stop()

    def install(self, definition, factory_task):
        workers._WORKERS_BY_NAME = {definition.name: definition}
        workers._worker_health[definition.name] = workers.WorkerHealth(
            "running", 0.0, None, 0, None, definition.critical
        )
        workers._worker_tasks[definition.name] = factory_task

    async def wait_until(self, predicate):
        for _ in range(100):
            if predicate():
                return
            await asyncio.sleep(0)
        self.fail("condition was not reached")

    async def test_worker_crash_restarts_and_counts_fast_failures(self):
        calls = 0

        async def factory(_bot):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise RuntimeError("secret must not leave process")
            await asyncio.Future()

        definition = workers.WorkerDefinition("test", factory, False, 4, 100)
        first = asyncio.create_task(factory(self.bot))
        self.install(definition, first)
        supervisor = asyncio.create_task(workers._supervise_workers(
            self.bot, check_interval=0, clock=lambda: 1, backoff_delay=lambda _: 0
        ))
        await self.wait_until(
            lambda: calls == 3 and not workers._worker_tasks["test"].done()
        )
        self.assertFalse(workers._worker_tasks["test"].done())
        self.assertEqual(2, workers._worker_health["test"].consecutive_failures)
        workers.shutdown_event.set()
        await supervisor

    async def test_failure_counter_resets_after_stability_window(self):
        now = [0.0]

        async def running(_bot):
            await workers.shutdown_event.wait()

        definition = workers.WorkerDefinition("stable", running, False, 3, 5)
        task = asyncio.create_task(running(self.bot))
        self.install(definition, task)
        workers._worker_health["stable"].consecutive_failures = 2
        now[0] = 6.0
        supervisor = asyncio.create_task(workers._supervise_workers(
            self.bot, check_interval=0, clock=lambda: now[0]
        ))
        await self.wait_until(
            lambda: workers._worker_health["stable"].consecutive_failures == 0
        )
        workers.shutdown_event.set()
        await supervisor

    async def test_critical_worker_exhaustion_is_fatal_and_deduplicated(self):
        async def failing(_bot):
            raise ValueError("payment token=secret")

        definition = workers.WorkerDefinition("critical", failing, True, 0, 10)
        task = asyncio.create_task(failing(self.bot))
        self.install(definition, task)
        await workers._supervise_workers(
            self.bot, check_interval=0, clock=lambda: 1, backoff_delay=lambda _: 0
        )
        health = workers._worker_health["critical"]
        self.assertEqual("failed", health.state)
        self.assertIn("critical", workers._worker_tasks)
        self.assertTrue(workers._fatal_shutdown)
        self.assertTrue(workers.shutdown_event.is_set())
        await self.wait_until(lambda: self.bot.send_message.await_count == 2)
        self.assertEqual(2, self.bot.send_message.await_count)
        workers._fatal(self.bot, "critical", 2, "ValueError")
        await asyncio.sleep(0)
        self.assertEqual(2, self.bot.send_message.await_count)

    async def test_hung_alert_does_not_block_noncritical_restart(self):
        alert_started = asyncio.Event()
        release_alert = asyncio.Event()
        restarted = asyncio.Event()
        calls = 0

        async def hung_send(*_args, **_kwargs):
            alert_started.set()
            await release_alert.wait()

        async def factory(_bot):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("private")
            restarted.set()
            await asyncio.Future()

        self.bot.send_message.side_effect = hung_send
        definition = workers.WorkerDefinition("hung_optional", factory, False, 2, 10)
        self.install(definition, asyncio.create_task(factory(self.bot)))
        supervisor = asyncio.create_task(workers._supervise_workers(
            self.bot, check_interval=0, clock=lambda: 1, backoff_delay=lambda _: 0
        ))
        await alert_started.wait()
        await restarted.wait()
        self.assertFalse(supervisor.done())
        self.assertFalse(workers.shutdown_event.is_set())
        release_alert.set()
        workers.shutdown_event.set()
        await supervisor

    async def test_hung_alert_does_not_block_critical_fatal_shutdown(self):
        alert_started = asyncio.Event()
        release_alert = asyncio.Event()

        async def hung_send(*_args, **_kwargs):
            alert_started.set()
            await release_alert.wait()

        async def failing(_bot):
            raise RuntimeError("private")

        self.bot.send_message.side_effect = hung_send
        definition = workers.WorkerDefinition("hung_critical", failing, True, 0, 10)
        self.install(definition, asyncio.create_task(failing(self.bot)))
        await workers._supervise_workers(
            self.bot, check_interval=0, clock=lambda: 1, backoff_delay=lambda _: 0
        )
        self.assertTrue(workers._fatal_shutdown)
        self.assertTrue(workers.shutdown_event.is_set())
        self.assertEqual("failed", workers._worker_health["hung_critical"].state)
        await alert_started.wait()
        self.assertTrue(any(not task.done() for task in workers._alert_tasks))
        release_alert.set()

    async def test_noncritical_worker_stops_without_process_shutdown(self):
        async def failing(_bot):
            raise LookupError("private")

        definition = workers.WorkerDefinition("optional", failing, False, 0, 10)
        task = asyncio.create_task(failing(self.bot))
        self.install(definition, task)
        supervisor = asyncio.create_task(workers._supervise_workers(
            self.bot, check_interval=0, clock=lambda: 1
        ))
        await self.wait_until(lambda: workers._worker_health["optional"].state == "cooldown")
        count = workers._worker_health["optional"].consecutive_failures
        for _ in range(10):
            await asyncio.sleep(0)
        self.assertEqual(count, workers._worker_health["optional"].consecutive_failures)
        self.assertIn("optional", workers._worker_tasks)
        self.assertEqual(
            "cooldown",
            workers.get_worker_health_snapshot()["workers"]["optional"]["state"],
        )
        self.assertFalse(workers.shutdown_event.is_set())
        supervisor.cancel()
        await asyncio.gather(supervisor, return_exceptions=True)

    async def test_crash_alert_episode_reopens_after_stability_window(self):
        calls = 0
        stable_release = asyncio.Event()
        now = [0.0]

        async def factory(_bot):
            nonlocal calls
            calls += 1
            if calls <= 2:
                raise RuntimeError("private")
            if calls == 3:
                await stable_release.wait()
                raise RuntimeError("another private value")
            await asyncio.Future()

        definition = workers.WorkerDefinition("episode", factory, False, 5, 5)
        self.install(definition, asyncio.create_task(factory(self.bot)))
        supervisor = asyncio.create_task(workers._supervise_workers(
            self.bot,
            check_interval=0,
            clock=lambda: now[0],
            backoff_delay=lambda _: 0,
        ))
        await self.wait_until(
            lambda: calls == 3 and not workers._worker_tasks["episode"].done()
        )
        self.assertEqual(1, self.bot.send_message.await_count)
        now[0] = 6.0
        await self.wait_until(
            lambda: workers._worker_health["episode"].consecutive_failures == 0
        )
        stable_release.set()
        await self.wait_until(lambda: self.bot.send_message.await_count == 2)
        self.assertEqual(2, self.bot.send_message.await_count)
        workers.shutdown_event.set()
        await supervisor

    async def test_unexpected_critical_cancellation_restarts_then_fails_fatal(self):
        restarted = asyncio.Event()
        cancel_restarted = asyncio.Event()

        async def factory(_bot):
            restarted.set()
            await cancel_restarted.wait()
            asyncio.current_task().cancel()
            await asyncio.sleep(0)

        definition = workers.WorkerDefinition("critical_cancel", factory, True, 1, 10)
        first = asyncio.create_task(asyncio.sleep(100))
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        self.install(definition, first)
        supervisor = asyncio.create_task(workers._supervise_workers(
            self.bot, check_interval=0, clock=lambda: 1, backoff_delay=lambda _: 0
        ))
        await restarted.wait()
        self.assertEqual(1, workers._worker_health["critical_cancel"].consecutive_failures)
        self.assertFalse(workers.shutdown_event.is_set())
        cancel_restarted.set()
        await self.wait_until(lambda: workers.shutdown_event.is_set())
        self.assertEqual("failed", workers._worker_health["critical_cancel"].state)
        self.assertEqual(2, workers._worker_health["critical_cancel"].consecutive_failures)
        await supervisor

    async def test_unexpected_noncritical_cancellation_restarts_then_stops(self):
        restarted = asyncio.Event()
        cancel_restarted = asyncio.Event()

        async def factory(_bot):
            restarted.set()
            await cancel_restarted.wait()
            asyncio.current_task().cancel()
            await asyncio.sleep(0)

        definition = workers.WorkerDefinition("optional_cancel", factory, False, 1, 10)
        first = asyncio.create_task(asyncio.sleep(100))
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        self.install(definition, first)
        supervisor = asyncio.create_task(workers._supervise_workers(
            self.bot, check_interval=0, clock=lambda: 1, backoff_delay=lambda _: 0
        ))
        await restarted.wait()
        self.assertEqual(1, workers._worker_health["optional_cancel"].consecutive_failures)
        self.assertFalse(workers.shutdown_event.is_set())
        cancel_restarted.set()
        await self.wait_until(
            lambda: workers._worker_health["optional_cancel"].state == "cooldown"
        )
        self.assertIn("optional_cancel", workers._worker_tasks)
        self.assertFalse(workers.shutdown_event.is_set())
        supervisor.cancel()
        await asyncio.gather(supervisor, return_exceptions=True)

    async def test_supervisor_failure_requests_shutdown_but_cancellation_does_not(self):
        async def fail():
            raise RuntimeError("supervisor secret")

        failed = asyncio.create_task(fail())
        await asyncio.gather(failed, return_exceptions=True)
        workers._supervisor_done(failed, self.bot)
        await self.wait_until(lambda: workers.shutdown_event.is_set())
        self.assertTrue(workers._fatal_shutdown)

        workers.shutdown_event.clear()
        workers._fatal_shutdown = False
        cancelled = asyncio.create_task(asyncio.sleep(10))
        cancelled.cancel()
        await asyncio.gather(cancelled, return_exceptions=True)
        workers._supervisor_done(cancelled, self.bot)
        await asyncio.sleep(0)
        self.assertFalse(workers.shutdown_event.is_set())

    async def test_supervisor_failure_shutdown_is_immediate_with_hung_alert(self):
        alert_started = asyncio.Event()
        release_alert = asyncio.Event()

        async def hung_send(*_args, **_kwargs):
            alert_started.set()
            await release_alert.wait()

        async def fail():
            raise RuntimeError("supervisor private")

        self.bot.send_message.side_effect = hung_send
        failed = asyncio.create_task(fail())
        await asyncio.gather(failed, return_exceptions=True)
        workers._supervisor_done(failed, self.bot)
        self.assertTrue(workers.shutdown_event.is_set())
        self.assertFalse(workers._supervisor_healthy)
        await alert_started.wait()
        self.assertTrue(any(not task.done() for task in workers._alert_tasks))
        release_alert.set()

    async def test_stop_times_out_and_cleans_up_hung_alert(self):
        alert_started = asyncio.Event()
        release_alert = asyncio.Event()

        async def hung_send(*_args, **_kwargs):
            alert_started.set()
            await release_alert.wait()

        self.bot.send_message.side_effect = hung_send
        workers._schedule_alert(
            self.bot, "cleanup", "title", "worker", 1, "RuntimeError", timeout=100
        )
        await alert_started.wait()
        alert_tasks = list(workers._alert_tasks)
        await workers.stop_background_workers(alert_grace_timeout=0)
        self.assertEqual(set(), workers._alert_tasks)
        self.assertTrue(all(task.done() for task in alert_tasks))
        active_alerts = [
            task for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and task.get_name().startswith("worker_alert_")
            and not task.done()
        ]
        self.assertEqual([], active_alerts)

    def test_heartbeat_health_requires_live_critical_workers(self):
        critical = [item for item in workers.WORKERS if item.critical]
        workers._started_at = -100.0
        for definition in critical:
            workers._worker_health[definition.name] = workers.WorkerHealth(
                "running", 0, None, 0, None, True
            )
            workers._worker_tasks[definition.name] = Mock(done=lambda: False)
        self.assertTrue(workers.heartbeat_allowed(now=100))
        missing = critical[0].name
        workers._worker_health.pop(missing)
        self.assertFalse(workers.heartbeat_allowed(now=100))
        workers._worker_health[missing] = workers.WorkerHealth(
            "failed", 0, 1, 11, "RuntimeError", True
        )
        self.assertFalse(workers.heartbeat_allowed(now=100))

    def test_snapshot_contains_type_but_no_exception_message(self):
        workers._worker_health["safe"] = workers.WorkerHealth(
            "failed", 1, 2, 1, "SecretError", True
        )
        snapshot = workers.get_worker_health_snapshot()
        rendered = repr(snapshot)
        self.assertIn("SecretError", rendered)
        self.assertNotIn("password", rendered)
        self.assertNotIn("message", rendered)

    async def test_stop_leaves_no_worker_tasks(self):
        task = asyncio.create_task(asyncio.sleep(100), name="worker_test_cleanup")
        workers._worker_tasks["test"] = task
        workers._worker_health["test"] = workers.WorkerHealth(
            "running", 0, None, 0, None, False
        )
        workers._supervisor_task = asyncio.create_task(asyncio.sleep(100))
        initial_count = workers._worker_health["test"].consecutive_failures
        await workers.stop_background_workers()
        self.assertTrue(task.done())
        self.assertEqual({}, workers._worker_tasks)
        self.assertEqual(initial_count, workers._worker_health["test"].consecutive_failures)
        self.assertEqual(0, self.bot.send_message.await_count)


class HeartbeatLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_writes_only_while_health_allows_it(self):
        event = asyncio.Event()
        allowed = [True]
        writes = []

        def write(final=False):
            writes.append(final)

        with patch.object(heartbeat, "_write_heartbeat", side_effect=write), patch.object(
            heartbeat, "_check_circuit_breakers", new=AsyncMock()
        ):
            task = asyncio.create_task(
                heartbeat.heartbeat_loop(event, lambda: allowed[0], interval=0)
            )
            for _ in range(10):
                await asyncio.sleep(0)
            self.assertTrue(writes)
            allowed[0] = False
            count = len(writes)
            for _ in range(10):
                await asyncio.sleep(0)
            event.set()
            await task
            self.assertEqual(count, len(writes))
