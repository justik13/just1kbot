import asyncio
import dataclasses
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from services.payment_queue_health import PaymentQueueHealthSnapshot, QueueExample, QueueSnapshot
from services.workers.queue_health import QueueHealthMonitor, queue_health_loop

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

def queue(name="provider_operations", *, pending=0, retry=0, due=0, overdue=0,
          processing=0, stale=0, dead=0, examples=()):
    return QueueSnapshot(name, pending, retry, due, overdue, processing, stale, dead,
                         120 if due else None, 150 if stale else None,
                         300 if dead else None, tuple(examples))

def snapshot(*queues):
    return PaymentQueueHealthSnapshot(NOW, tuple(queues))

class QueueSnapshotTests(unittest.TestCase):
    def test_empty_queues_are_healthy(self):
        self.assertTrue(snapshot(queue(), queue("fulfillment"), queue("webhook")).healthy)

    def test_future_pending_and_recent_due_are_healthy(self):
        self.assertTrue(queue(pending=1).healthy)
        self.assertTrue(queue(pending=1, due=1).healthy)

    def test_old_pending_and_retry_are_overdue(self):
        self.assertFalse(queue(pending=1, due=1, overdue=1).healthy)
        self.assertFalse(queue(retry=1, due=1, overdue=1).healthy)

    def test_processing_inside_lease_is_healthy_and_stale_is_not(self):
        self.assertTrue(queue(processing=1).healthy)
        self.assertFalse(queue(processing=1, stale=1).healthy)

    def test_any_dead_is_unhealthy(self):
        self.assertFalse(queue(dead=1).healthy)

    def test_snapshot_is_immutable_safe_and_has_sanitized_example(self):
        example = QueueExample(7, 9, "create_payment", "dead", 3, 3,
                               "token=[REDACTED]", 500)
        value = snapshot(queue(dead=1, examples=(example,)))
        rendered = repr(dataclasses.asdict(value))
        self.assertIn("operation_id", rendered)
        self.assertIn("last_error_code", rendered)
        for forbidden in ("payload", "last_error'", "idempotency_key", "SECRET_CANARY"):
            self.assertNotIn(forbidden, rendered)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            value.queues[0].dead = 0

class QueueHealthMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = AsyncMock()
        self.now = 0.0
        self.settings = patch("services.workers.queue_health.get_settings",
                              return_value=type("S", (), {"ADMIN_IDS": [1]})())
        self.settings.start()
        self.monitor = QueueHealthMonitor(self.bot, cooldown=60,
                                          clock=lambda: self.now, alert_timeout=.05)

    async def asyncTearDown(self):
        await self.monitor.close()
        self.settings.stop()

    async def flush(self):
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    async def test_episode_dedup_reminder_recovery_and_new_episode(self):
        bad = snapshot(queue(dead=1))
        good = snapshot(queue())
        self.monitor.observe(bad); await self.flush()
        self.assertEqual(1, self.bot.send_message.await_count)
        self.now = 59; self.monitor.observe(bad); await self.flush()
        self.assertEqual(1, self.bot.send_message.await_count)
        self.now = 60; self.monitor.observe(bad); await self.flush()
        self.assertEqual(2, self.bot.send_message.await_count)
        self.monitor.observe(good); await self.flush()
        self.assertEqual(3, self.bot.send_message.await_count)
        self.monitor.observe(good); await self.flush()
        self.assertEqual(3, self.bot.send_message.await_count)
        self.monitor.observe(bad); await self.flush()
        self.assertEqual(4, self.bot.send_message.await_count)

    async def test_existing_dead_row_alerts_on_first_observation(self):
        self.monitor.observe(snapshot(queue(dead=10)))
        await self.flush()
        self.assertEqual(1, self.bot.send_message.await_count)

    async def test_alert_contains_only_safe_bounded_example_fields(self):
        item = QueueExample(1, 2, "grant_subscription", "dead", 2, 2, "safe_code", 99)
        self.monitor.observe(snapshot(queue("fulfillment", dead=1, examples=(item,))))
        await self.flush()
        message = self.bot.send_message.await_args.args[1]
        self.assertIn("id=1", message); self.assertIn("code=safe_code", message)
        self.assertNotIn("payload", message); self.assertNotIn("last_error=", message)

    async def test_hung_alert_does_not_block_checks_or_shutdown(self):
        blocker = asyncio.Event()
        async def hung(*args, **kwargs):
            await blocker.wait()
        self.bot.send_message.side_effect = hung
        self.monitor.observe(snapshot(queue(dead=1)))
        await asyncio.sleep(0)
        self.now = 60
        self.monitor.observe(snapshot(queue(dead=1)))
        self.assertEqual(2, len(self.monitor.alert_tasks))
        await self.monitor.close()
        self.assertEqual(set(), self.monitor.alert_tasks)
        pending = [t for t in asyncio.all_tasks() if t.get_name() == "queue_health_alert" and not t.done()]
        self.assertEqual([], pending)

    async def test_loop_shutdown_leaves_no_alert_tasks(self):
        shutdown = asyncio.Event(); shutdown.set()
        await queue_health_loop(self.bot, shutdown, interval=0)
        pending = [t for t in asyncio.all_tasks() if t.get_name() == "queue_health_alert" and not t.done()]
        self.assertEqual([], pending)
