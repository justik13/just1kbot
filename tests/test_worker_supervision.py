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
        self.assertEqual(2, self.bot.send_message.await_count)
        await workers._fatal(self.bot, "critical", 2, "ValueError")
        self.assertEqual(2, self.bot.send_message.await_count)

    async def test_noncritical_worker_stops_without_process_shutdown(self):
        async def failing(_bot):
            raise LookupError("private")

        definition = workers.WorkerDefinition("optional", failing, False, 0, 10)
        task = asyncio.create_task(failing(self.bot))
        self.install(definition, task)
        supervisor = asyncio.create_task(workers._supervise_workers(
            self.bot, check_interval=0, clock=lambda: 1
        ))
        await self.wait_until(lambda: workers._worker_health["optional"].state == "failed")
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
        await workers.stop_background_workers()
        self.assertTrue(task.done())
        self.assertEqual({}, workers._worker_tasks)


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
