import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import APIOperation
from services.api_operations_queue import (
    APIOperationIdempotencyConflict,
    APIOperationValidationError,
    calculate_retry_delay,
    claim_api_operations,
    enqueue_api_operation,
    recover_stale_api_operations,
)


class RetryDelayTests(unittest.TestCase):
    def test_exponential_delay_is_capped(self):
        self.assertEqual(calculate_retry_delay(1), timedelta(seconds=30))
        self.assertEqual(calculate_retry_delay(2), timedelta(seconds=60))
        self.assertEqual(calculate_retry_delay(3), timedelta(seconds=120))
        self.assertEqual(calculate_retry_delay(1000), timedelta(seconds=3600))

    def test_nonpositive_attempt_is_rejected(self):
        with self.assertRaises(APIOperationValidationError):
            calculate_retry_delay(0)


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not set")
class APIOperationsPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if not os.environ["TEST_DATABASE_URL"].startswith(
            ("postgresql://", "postgresql+asyncpg://")
        ):
            self.fail("TEST_DATABASE_URL must point to PostgreSQL")
        self.engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions.begin() as session:
            await session.execute(delete(APIOperation))

    async def asyncTearDown(self):
        async with self.sessions.begin() as session:
            await session.execute(delete(APIOperation))
        await self.engine.dispose()

    def command(self, key, **overrides):
        values = dict(
            operation_type="create_peer",
            idempotency_key=key,
            api_url_snapshot="https://queue.test",
            api_key_snapshot="test-api-key",
            client_name="client",
            payload={"region": "test"},
        )
        values.update(overrides)
        return values

    async def enqueue(self, key, **overrides):
        async with self.sessions.begin() as session:
            return await enqueue_api_operation(
                session, **self.command(key, **overrides)
            )

    async def get(self, operation_id):
        async with self.sessions() as session:
            return await session.get(APIOperation, operation_id)

    async def test_enqueue_idempotency_copy_and_conflict(self):
        payload = {"nested": {"value": 1}}
        first = await self.enqueue("same", payload=payload)
        payload["nested"]["value"] = 2
        second = await self.enqueue("same", payload={"nested": {"value": 1}})
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.payload, {"nested": {"value": 1}})
        with self.assertRaises(APIOperationIdempotencyConflict):
            await self.enqueue("same", payload={"different": True})

    async def test_enqueue_validation(self):
        invalid = (
            self.command(None),
            self.command(123),
            self.command(" "),
            self.command("x" * 256),
            self.command("type", operation_type="unknown"),
            self.command("update", operation_type="update_peer", peer_id=None),
            self.command("delete", operation_type="delete_peer", peer_id=None),
            self.command("endpoint", api_url_snapshot=None, api_key_snapshot=None),
            self.command("client", client_name=None),
            self.command("secret", payload={"api_key": "no"}),
        )
        for command in invalid:
            with self.subTest(command=repr(command["idempotency_key"])):
                async with self.sessions.begin() as session:
                    with self.assertRaises(APIOperationValidationError):
                        await enqueue_api_operation(session, **command)

    async def test_claim_filters_increments_and_detaches(self):
        now = datetime.now(timezone.utc)
        states = [
            ("pending", now, True),
            ("retry", now - timedelta(seconds=1), True),
            ("retry", now + timedelta(hours=1), False),
            ("succeeded", now, False),
            ("dead", now, False),
            ("cancelled", now, False),
        ]
        expected = []
        async with self.sessions.begin() as session:
            for number, (status, next_at, ready) in enumerate(states):
                operation = APIOperation(
                    **self.command(f"state-{number}"),
                    status=status,
                    next_attempt_at=next_at,
                )
                session.add(operation)
                await session.flush()
                if ready:
                    expected.append(operation.id)
        claimed = await claim_api_operations(
            worker_id="worker-a", limit=20, session_factory=self.sessions
        )
        self.assertEqual({item.id for item in claimed}, set(expected))
        self.assertTrue(
            all(
                item.attempt_number == 1 and item.locked_by == "worker-a"
                for item in claimed
            )
        )
        self.assertEqual(claimed[0].payload["region"], "test")  # usable detached
        stored = await self.get(claimed[0].id)
        self.assertEqual(
            (stored.status, stored.attempts, stored.locked_by),
            ("processing", 1, "worker-a"),
        )

    async def test_claim_dead_letters_already_exhausted_pending(self):
        operation = await self.enqueue("already-exhausted", max_attempts=1)
        async with self.sessions.begin() as session:
            row = await session.get(APIOperation, operation.id)
            row.attempts = 1
        self.assertEqual(
            await claim_api_operations(
                worker_id="worker", session_factory=self.sessions
            ),
            [],
        )
        stored = await self.get(operation.id)
        self.assertEqual(
            (stored.status, stored.last_error_code), ("dead", "max_attempts_exhausted")
        )

    async def test_two_concurrent_claims_do_not_overlap(self):
        for number in range(20):
            await self.enqueue(f"concurrent-{number}")
        first, second = await asyncio.gather(
            claim_api_operations(
                worker_id="one", limit=10, session_factory=self.sessions
            ),
            claim_api_operations(
                worker_id="two", limit=10, session_factory=self.sessions
            ),
        )
        first_ids, second_ids = (
            {item.id for item in first},
            {item.id for item in second},
        )
        self.assertFalse(first_ids & second_ids)
        self.assertEqual(len(first_ids | second_ids), 20)

    async def test_attempt_number_fences_stale_same_worker_lease(self):
        operation = await self.enqueue("aba-fencing")
        first = (
            await claim_api_operations(
                worker_id="same-worker", session_factory=self.sessions
            )
        )[0]
        self.assertEqual(first.attempt_number, 1)

        async with self.sessions.begin() as session:
            row = await session.get(APIOperation, operation.id)
            row.locked_at = datetime.now(timezone.utc) - timedelta(hours=2)
        self.assertEqual(
            await recover_stale_api_operations(
                lease_timeout=timedelta(hours=1), session_factory=self.sessions
            ),
            (1, 0),
        )
        async with self.sessions.begin() as session:
            row = await session.get(APIOperation, operation.id)
            row.next_attempt_at = datetime.now(timezone.utc) - timedelta(hours=1)


        second = (
            await claim_api_operations(
                worker_id="same-worker", session_factory=self.sessions
            )
        )[0]

        self.assertEqual(second.attempt_number, 2)

        active = await self.get(operation.id)
        self.assertEqual(
            (active.status, active.locked_by, active.attempts),
            ("processing", "same-worker", 2),
        )

    async def test_recovery(self):
        now = datetime.now(timezone.utc)
        rows = [
            APIOperation(
                **self.command("fresh"),
                status="processing",
                attempts=1,
                locked_by="w",
                locked_at=now,
            ),
            APIOperation(
                **self.command("stale"),
                status="processing",
                attempts=1,
                locked_by="w",
                locked_at=now - timedelta(hours=2),
            ),
            APIOperation(
                **self.command("exhausted"),
                status="processing",
                attempts=2,
                max_attempts=2,
                locked_by="w",
                locked_at=now - timedelta(hours=2),
            ),
            APIOperation(
                **self.command("null-lock"),
                status="processing",
                attempts=1,
                locked_by="w",
                locked_at=None,
            ),
        ]
        async with self.sessions.begin() as session:
            session.add_all(rows)
        self.assertEqual(
            await recover_stale_api_operations(
                lease_timeout=timedelta(hours=1), session_factory=self.sessions
            ),
            (2, 1),
        )
        statuses = {}
        async with self.sessions() as session:
            for row in (
                await session.execute(
                    select(APIOperation).where(
                        APIOperation.id.in_([item.id for item in rows])
                    )
                )
            ).scalars():
                statuses[row.id] = (row.status, row.locked_by, row.last_error_code)
        self.assertEqual(statuses[rows[0].id][0], "processing")
        self.assertEqual(statuses[rows[1].id][:2], ("retry", None))
        self.assertEqual(
            statuses[rows[2].id], ("dead", None, "stale_lease_max_attempts")
        )
        self.assertEqual(statuses[rows[3].id][:2], ("retry", None))


if __name__ == "__main__":
    unittest.main()
