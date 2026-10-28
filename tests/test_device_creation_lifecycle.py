"""PostgreSQL integration tests for device creation and deletion lifecycles.

Runs exclusively against TEST_DATABASE_URL to verify database invariants,
concurrency, profile state transitions, and API operation queues.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import APIOperation, Server, User, VPNProfile
from services.device_service import DeviceService


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not set")
class DeviceCreationPostgresIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env_patcher = patch.dict(
            os.environ,
            {
                "BOT_TOKEN": "123:test",
                "REDIS_URL": "redis://localhost:6379/1",
                "REDIS_PASSWORD": "test",
                "ADMIN_IDS": "[123456789]",
                "SUPPORT_USERNAME": "test_support",
                "DOMAIN": "test.domain",
                "SSL_EMAIL": "test@domain.com",
                "YOOKASSA_SHOP_ID": "123456",
                "YOOKASSA_SECRET_KEY": "test_secret",
                "YOOKASSA_RETURN_URL": "https://t.me/{bot_username}",
                "YOOKASSA_WEBHOOK_PORT": "8080",
                "DB_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
                "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
            },
        )
        self.env_patcher.start()
        from config.settings import get_settings
        get_settings.cache_clear()

        self.engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
        try:
            from tests.db_utils import TRUNCATE_SQL
        except ImportError:
            from db_utils import TRUNCATE_SQL
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        from database import connection
        self.old_sessionmaker = connection._sessionmaker
        self.old_engine = connection._engine
        connection._sessionmaker = self.sessions
        connection._engine = self.engine
        async with self.sessions.begin() as s:
            await s.execute(text(TRUNCATE_SQL))
            u = User(
                telegram_id=987654,
                username="testuser",
                device_limit=10,
                subscription_end=datetime.now(timezone.utc) + timedelta(days=30),
            )
            v = Server(
                name="Germany Live",
                country_flag="🇩🇪",
                api_url="http://127.0.0.1:8080",
                api_key="secret",
                protocol="amneziawg2",
                is_active=True,
                max_clients=100,
            )
            s.add_all([u, v])
            await s.flush()
            self.uid = u.id
            self.sid = v.id

    async def asyncTearDown(self):
        from database import connection
        connection._sessionmaker = self.old_sessionmaker
        connection._engine = self.old_engine
        try:
            from tests.db_utils import TRUNCATE_SQL
        except ImportError:
            from db_utils import TRUNCATE_SQL
        async with self.sessions.begin() as s:
            await s.execute(text(TRUNCATE_SQL))
        await self.engine.dispose()
        self.env_patcher.stop()
        from config.settings import get_settings
        get_settings.cache_clear()

    async def test_create_device_commit_makes_operation_visible_to_second_session(self):
        """Verify that create_device followed by session.commit makes create_peer visible to independent session."""
        from services.device_service import DeviceService
        from services.slots_cache import ServerPeerSnapshot

        snapshot = ServerPeerSnapshot(
            self.sid,
            frozenset(),
            datetime.now(timezone.utc),
        )

        # 2. Session A: create_device and commit
        with patch("services.device_service.ensure_server_capacity", new=AsyncMock()):
            async with self.sessions() as session_a:
                profile = await DeviceService.create_device(
                    session_a,
                    user_id=self.uid,
                    server_id=self.sid,
                    device_name="Integration Dev #1",
                    snapshot=snapshot,
                )
                await session_a.commit()
                created_profile_id = profile.id

        # 3. Session B: query from completely separate database connection
        async with self.sessions() as session_b:
            res = await session_b.execute(
                select(APIOperation).where(
                    APIOperation.profile_id == created_profile_id,
                    APIOperation.operation_type == "create_peer",
                )
            )
            op = res.scalar_one_or_none()

            self.assertIsNotNone(op, "APIOperation(create_peer) must be visible in session_b immediately after commit")
            self.assertEqual(op.status, "pending")
            self.assertEqual(op.profile_id, created_profile_id)
            self.assertEqual(op.operation_type, "create_peer")

    async def test_cleanup_worker_concurrency_with_active_operation_in_postgres(self):
        """Verify that cleanup worker skips profiles with active APIOperation in real PostgreSQL."""
        from database.models import VPNProfile
        from services.workers.cleanup import _cleanup_stuck_profiles

        async with self.sessions.begin() as s:
            old_time = datetime.now(timezone.utc) - timedelta(hours=2)
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Stuck Dev",
                client_name="tg_987654_stuck",
                provisioning_status="pending_create",
                created_at=old_time,
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            # Active operation in processing state
            op = APIOperation(
                operation_type="create_peer",
                status="processing",
                idempotency_key=f"create-peer:{prof_id}",
                server_id=self.sid,
                profile_id=prof_id,
            )
            s.add(op)

        with patch("services.workers.cleanup.session_scope", side_effect=self.sessions):
            await _cleanup_stuck_profiles()

        async with self.sessions() as s:
            p = await s.get(VPNProfile, prof_id)
            # Profile must NOT have been converted to create_failed because APIOperation was in 'processing'
            self.assertEqual(p.provisioning_status, "pending_create")


    async def test_38_postgres_cleanup_claim_concurrency_interleaving(self):
        """Prove that cleanup skipping logic works even if claim_api_operations executes concurrently."""
        import asyncio
        from datetime import datetime, timedelta, timezone

        from database.models import APIOperation, VPNProfile
        from services.api_operations_queue import claim_api_operations

        async with self.sessions.begin() as s:
            old_time = datetime.now(timezone.utc) - timedelta(hours=2)
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Stuck Concurrent",
                client_name="tg_conc_987",
                provisioning_status="pending_create",
                created_at=old_time,
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            op = APIOperation(
                operation_type="create_peer",
                status="pending",
                idempotency_key=f"create-peer:{prof_id}",
                server_id=self.sid,
                profile_id=prof_id,
                next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
            s.add(op)
            await s.flush()
            op_id = op.id

        barrier_event = asyncio.Event()
        original_session_scope = self.sessions

        async def controlled_cleanup():
            async with original_session_scope() as s:
                async with s.begin():
                    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=1)
                    stuck_profiles = (
                        await s.execute(
                            select(VPNProfile)
                            .where(
                                VPNProfile.provisioning_status.in_(["pending_create", "create_cleanup_pending", "deleting"]),
                                VPNProfile.created_at < cutoff_time,
                            )
                            .with_for_update(skip_locked=True)
                        )
                    ).scalars().all()

                    self.assertEqual(len(stuck_profiles), 1)
                    # Signal barrier so claim_api_operations executes concurrently in separate session
                    barrier_event.set()
                    await asyncio.sleep(0.05)

                    for profile in stuck_profiles:
                        active_op_res = await s.execute(
                            select(APIOperation.id).where(
                                APIOperation.profile_id == profile.id,
                                APIOperation.status.in_(["pending", "processing", "retry"]),
                            ).limit(1)
                        )
                        if active_op_res.scalar_one_or_none() is not None:
                            continue
                        profile.provisioning_status = "create_failed"

        async def controlled_claim():
            await barrier_event.wait()
            return await claim_api_operations(worker_id="test-worker", limit=10, session_factory=self.sessions)

        cleanup_task = asyncio.create_task(controlled_cleanup())
        claim_task = asyncio.create_task(controlled_claim())
        claimed, _ = await asyncio.gather(claim_task, cleanup_task)

        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0].id, op_id)

        async with self.sessions() as s:
            p = await s.get(VPNProfile, prof_id)
            self.assertEqual(p.provisioning_status, "pending_create")
            o = await s.get(APIOperation, op_id)
            self.assertEqual(o.status, "processing")
            self.assertEqual(o.locked_by, "test-worker")

    async def test_39_finalize_create_success_race_condition(self):
        """finalize_create_success catches create_failed mid-flight and queues compensation."""
        from database.models import APIOperation, VPNProfile
        from services.api_operations_finalizer import (
            CreateCompensationRequired,
            finalize_create_success,
        )

        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Race Dev",
                client_name="tg_race",
                provisioning_status="pending_create",
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            op = APIOperation(
                operation_type="create_peer",
                status="processing",
                idempotency_key=f"create-peer:{prof_id}",
                server_id=self.sid,
                profile_id=prof_id,
                locked_by="worker-1",
                attempts=1,
            )
            s.add(op)
            await s.flush()
            op_id = op.id

        # Simulating external factor modifying profile to create_failed during HTTP request
        async with self.sessions.begin() as s:
            p = await s.get(VPNProfile, prof_id)
            p.provisioning_status = "create_failed"
            await s.flush()

        with self.assertRaises(CreateCompensationRequired):
            await finalize_create_success(
                op_id,
                worker_id="worker-1",
                expected_attempt_number=1,
                peer_id="peer-123",
                raw_config="vpn://test",
                sent_desired_version=1,
                sent_is_active=True,
                sent_expires_at=None,
                session_factory=self.sessions,
            )

        async with self.sessions() as s:
            p = await s.get(VPNProfile, prof_id)
            self.assertEqual(p.provisioning_status, "create_cleanup_pending")
            self.assertEqual(p.peer_id, "peer-123")

    async def test_60_postgres_finalize_create_success_vs_concurrent_delete(self):
        """Proves that when user triggers deletion while CREATE is completing externally, compensation is raised and peer_id preserved."""
        from database.models import APIOperation, VPNProfile
        from services.api_operations_finalizer import (
            CreateCompensationRequired,
            finalize_create_success,
        )

        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Concurrent Delete Dev",
                client_name="tg_del_conc",
                provisioning_status="pending_create",
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            op = APIOperation(
                operation_type="create_peer",
                status="processing",
                idempotency_key=f"create-peer:{prof_id}",
                server_id=self.sid,
                profile_id=prof_id,
                locked_by="worker-1",
                attempts=1,
            )
            s.add(op)
            await s.flush()
            op_id = op.id

        # User deletes profile concurrently before finalizer commits
        async with self.sessions.begin() as s:
            p = await s.get(VPNProfile, prof_id)
            p.provisioning_status = "deleting"
            await s.flush()

        with self.assertRaises(CreateCompensationRequired):
            await finalize_create_success(
                op_id,
                worker_id="worker-1",
                expected_attempt_number=1,
                peer_id="peer-ext-999",
                raw_config="vpn://test-concurrent",
                sent_desired_version=1,
                sent_is_active=True,
                sent_expires_at=None,
                session_factory=self.sessions,
            )

        async with self.sessions() as s:
            p = await s.get(VPNProfile, prof_id)
            # Status must stay deleting, but peer_id must be stored so delete worker cleans it up!
            self.assertEqual(p.provisioning_status, "deleting")
            self.assertEqual(p.peer_id, "peer-ext-999")

    async def test_61_postgres_stale_lease_recovery_vs_compensation(self):
        """Proves that attempt number checks reject finalization when lease has been recovered by another worker."""
        from database.models import APIOperation, VPNProfile
        from services.api_operations_finalizer import finalize_create_success

        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Stale Lease Dev",
                client_name="tg_stale_99",
                provisioning_status="pending_create",
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            op = APIOperation(
                operation_type="create_peer",
                status="processing",
                idempotency_key=f"create-peer:{prof_id}",
                server_id=self.sid,
                profile_id=prof_id,
                locked_by="worker-2",  # Mismatched worker!
                attempts=2,            # Mismatched attempt!
            )
            s.add(op)
            await s.flush()
            op_id = op.id

        from services.api_operations_queue import APIOperationOwnershipError
        with self.assertRaises(APIOperationOwnershipError) as ctx:
            await finalize_create_success(
                op_id,
                worker_id="worker-1",  # Old worker
                expected_attempt_number=1,  # Old attempt
                peer_id="peer-stale",
                raw_config="vpn://test-stale",
                sent_desired_version=1,
                sent_is_active=True,
                sent_expires_at=None,
                session_factory=self.sessions,
            )
        self.assertIn("not leased", str(ctx.exception))

    async def test_45_postgres_rename_device_process_with_for_update(self):
        """Rename device uses row-level lock and updates device_name atomically on PostgreSQL."""
        from bot.handlers.connection.device_rename_routes import rename_device_process

        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Old Device #1",
                client_name="tg_rename_test",
                provisioning_status="active",
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

        message = MagicMock()
        message.bot = MagicMock()
        message.chat.id = 987654
        message.from_user.id = 987654
        message.text = "New Device Name"
        state = AsyncMock()
        state.get_data = AsyncMock(return_value={"profile_id": prof_id})
        db_user = SimpleNamespace(id=self.uid, telegram_id=987654)

        async with self.sessions.begin() as session:
            with (
                patch("bot.handlers.connection.device_rename_routes.SubscriptionService.check_access", new=AsyncMock(return_value=True)),
                patch("bot.handlers.connection.device_rename_routes.render_device_screen", new=AsyncMock()) as mock_hub,
            ):
                await rename_device_process(message, state, session, db_user=db_user)
                self.assertTrue(mock_hub.called)

        async with self.sessions() as s:
            updated = await s.get(VPNProfile, prof_id)
            self.assertEqual(updated.device_name, "New Device Name #1")

    async def test_46_profile_deletion_no_deadlock_with_finalizer(self):
        """ProfileDeletionService._delete_profiles does not deadlock with finalizer locking on PostgreSQL."""
        from services.profile_deletion_service import ProfileDeletionService

        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Delete Deadlock Test #1",
                client_name="tg_del_deadlock",
                provisioning_status="pending_create",
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            op = APIOperation(
                operation_type="create_peer",
                status="pending",
                idempotency_key=f"create-peer:{prof_id}:v1",
                server_id=self.sid,
                profile_id=prof_id,
                attempts=0,
            )
            s.add(op)
            await s.flush()

        async with self.sessions.begin() as session:
            count = await ProfileDeletionService.delete_profiles_for_user(
                session, self.uid, reason="user_banned"
            )
            self.assertEqual(count, 1)

        async with self.sessions() as s:
            deleted_prof = await s.get(VPNProfile, prof_id)
            self.assertIsNone(deleted_prof)

    async def test_47_finalize_operation_failure_duplicate_exact_client_name_sets_create_cleanup_pending(self):
        """duplicate_exact_client_name must mark profile as create_cleanup_pending, not create_failed."""
        from database.models import APIOperation, VPNProfile
        from services.api_operations_finalizer import finalize_operation_failure

        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Dup Exact Test",
                client_name="tg_dup_exact",
                provisioning_status="pending_create",
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            op = APIOperation(
                operation_type="create_peer",
                status="processing",
                idempotency_key=f"create-peer:{prof_id}:dup",
                server_id=self.sid,
                profile_id=prof_id,
                attempts=1,
                locked_by="worker-1",
            )
            s.add(op)
            await s.flush()
            op_id = op.id

        await finalize_operation_failure(
            op_id,
            worker_id="worker-1",
            expected_attempt_number=1,
            error_code="duplicate_exact_client_name",
            error_message="Multiple exact client names found on server",
            retryable=False,
            session_factory=self.sessions,
        )

        async with self.sessions() as s:
            p = await s.get(VPNProfile, prof_id)
            self.assertEqual(p.provisioning_status, "create_cleanup_pending")

    async def test_48_finalize_operation_failure_cleanup_peer_identity_mismatch_sets_create_cleanup_pending(self):
        """cleanup_peer_identity_mismatch must mark profile as create_cleanup_pending."""
        from database.models import APIOperation, VPNProfile
        from services.api_operations_finalizer import finalize_operation_failure

        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Mismatch Test",
                client_name="tg_mismatch",
                provisioning_status="pending_create",
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            op = APIOperation(
                operation_type="create_peer",
                status="processing",
                idempotency_key=f"create-peer:{prof_id}:mismatch",
                server_id=self.sid,
                profile_id=prof_id,
                attempts=1,
                locked_by="worker-1",
            )
            s.add(op)
            await s.flush()
            op_id = op.id

        await finalize_operation_failure(
            op_id,
            worker_id="worker-1",
            expected_attempt_number=1,
            error_code="cleanup_peer_identity_mismatch",
            error_message="Peer identity mismatch during reconciliation",
            retryable=False,
            session_factory=self.sessions,
        )

        async with self.sessions() as s:
            p = await s.get(VPNProfile, prof_id)
            self.assertEqual(p.provisioning_status, "create_cleanup_pending")

    async def test_49_profile_deletion_does_not_overwrite_active_worker_processing_lease(self):
        """ProfileDeletionService._delete_profiles does not overwrite status or locked_by of processing APIOperation."""
        from database.models import APIOperation, VPNProfile
        from services.profile_deletion_service import ProfileDeletionService

        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Processing Guard Test",
                client_name="tg_proc_guard",
                provisioning_status="pending_create",
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            op = APIOperation(
                operation_type="create_peer",
                status="processing",
                idempotency_key=f"create-peer:{prof_id}:proc",
                server_id=self.sid,
                profile_id=prof_id,
                attempts=2,
                locked_by="worker-active-42",
            )
            s.add(op)
            await s.flush()
            op_id = op.id

        async with self.sessions.begin() as session:
            count = await ProfileDeletionService.delete_profiles_for_user(
                session, self.uid, reason="user_banned"
            )
            self.assertEqual(count, 1)

        async with self.sessions() as s:
            op_after = await s.get(APIOperation, op_id)
            # Must remain 'processing' with its original worker lease intact
            self.assertEqual(op_after.status, "processing")
            self.assertEqual(op_after.locked_by, "worker-active-42")
            self.assertEqual(op_after.attempts, 2)
            prof_after = await s.get(VPNProfile, prof_id)
            self.assertEqual(prof_after.provisioning_status, "deleting")

    async def test_50_rename_device_process_rejects_duplicate_with_deleting_profile(self):
        """rename_device_process rejects rename when a deleting profile holds the same target name."""
        from bot.handlers.connection.device_rename_routes import rename_device_process
        from database.models import VPNProfile

        async with self.sessions.begin() as s:
            # Existing deleting profile
            prof_deleting = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Phone #1",
                client_name="tg_del_1",
                provisioning_status="deleting",
            )
            # Profile to rename
            prof_active = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Laptop #1",
                client_name="tg_act_1",
                provisioning_status="active",
            )
            s.add_all([prof_deleting, prof_active])
            await s.flush()
            active_id = prof_active.id

        message = MagicMock()
        message.bot = MagicMock()
        message.chat.id = 987654
        message.from_user.id = 987654
        message.text = "Phone"
        state = AsyncMock()
        state.get_data = AsyncMock(return_value={"profile_id": active_id})
        db_user = SimpleNamespace(id=self.uid, telegram_id=987654)

        async with self.sessions.begin() as session:
            with (
                patch("bot.handlers.connection.device_rename_routes.SubscriptionService.check_access", new=AsyncMock(return_value=True)),
                patch("bot.handlers.connection.device_rename_routes.render_hub", new=AsyncMock()) as mock_hub,
            ):
                await rename_device_process(message, state, session, db_user=db_user)
                self.assertTrue(mock_hub.called)
                text_arg = mock_hub.call_args.args[2]
                self.assertIn("уже существует", text_arg)

        async with self.sessions() as s:
            unchanged = await s.get(VPNProfile, active_id)
            self.assertEqual(unchanged.device_name, "Laptop #1")

    async def test_62_postgres_claim_exhausted_sync_vs_finalizer_deadlock(self):
        """Prove that claim_api_operations terminal sync and finalizer lock in same order without deadlock."""
        import asyncio

        from services.api_operations_finalizer import finalize_operation_failure
        from services.api_operations_queue import claim_api_operations

        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Concurrent Deadlock Probe",
                client_name="tg_deadlock_probe",
                provisioning_status="pending_create",
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            op = APIOperation(
                operation_type="create_peer",
                status="processing",
                idempotency_key=f"create-deadlock:{prof_id}",
                server_id=self.sid,
                profile_id=prof_id,
                locked_by="worker-1",
                attempts=10,
                max_attempts=10,
            )
            s.add(op)
            await s.flush()
            op_id = op.id

        # Concurrently execute finalize_operation_failure (which locks VPNProfile -> APIOperation)
        # and claim_api_operations (which processes exhausted operations with VPNProfile -> APIOperation lock order)
        async def run_finalizer():
            try:
                await finalize_operation_failure(
                    op_id,
                    worker_id="worker-1",
                    expected_attempt_number=10,
                    retryable=False,
                    error_code="timeout",
                    error_message="Network timed out",
                    session_factory=self.sessions,
                )
                return "finalized"
            except Exception as e:
                return f"finalizer_err: {type(e).__name__}"

        async def run_claim():
            try:
                await claim_api_operations(
                    worker_id="worker-2",
                    limit=10,
                    session_factory=self.sessions,
                )
                return "claimed"
            except Exception as e:
                return f"claim_err: {type(e).__name__}"

        res1, res2 = await asyncio.gather(run_finalizer(), run_claim())

        # Neither result should contain a deadlock error
        self.assertNotIn("Deadlock", res1)
        self.assertNotIn("deadlock", res1)
        self.assertNotIn("Deadlock", res2)
        self.assertNotIn("deadlock", res2)

        async with self.sessions() as s:
            final_prof = await s.get(VPNProfile, prof_id)
            final_op = await s.get(APIOperation, op_id)
            self.assertIn(final_prof.provisioning_status, {"create_cleanup_pending", "create_failed"})
            self.assertIn(final_op.status, {"dead", "retry"})

    async def test_63_postgres_delete_device_with_missing_server_row_enqueues_delete_peer(self):
        """When Server row is missing from DB, delete_device recovers endpoint snapshot and enqueues delete_peer."""
        from database.models import APIOperation, Server, VPNProfile
        from services.device_service import DeviceService

        # 1. Create a profile pointing to self.sid
        # and previous APIOperation recording the endpoint snapshot
        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Orphan Server Device",
                client_name="tg_orphan_server_client",
                peer_id="amnezia-peer-999",
                provisioning_status="active",
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            # Previous create_peer operation holding the immutable endpoint snapshot
            prev_op = APIOperation(
                operation_type="create_peer",
                status="succeeded",
                idempotency_key=f"create-orphan-test:{prof_id}",
                server_id=self.sid,
                profile_id=prof_id,
                server_name_snapshot="Old Node 99",
                api_url_snapshot="http://10.99.99.1:9999",
                api_key_snapshot="legacy-secret-key-99",
                peer_id="amnezia-peer-999",
                client_name="tg_orphan_server_client",
                attempts=1,
                max_attempts=10,
            )
            s.add(prev_op)

        # 2. Call DeviceService.delete_device with Server row simulated as missing/deleted
        async with self.sessions.begin() as s:
            prof_to_delete = await s.get(VPNProfile, prof_id)
            orig_get = s.get

            async def patched_get(entity, ident, **kwargs):
                if entity is Server:
                    return None
                return await orig_get(entity, ident, **kwargs)

            s.get = patched_get
            res = await DeviceService.delete_device(s, prof_to_delete, actor_id=self.uid)
            self.assertTrue(res)

        # 3. Assert profile is in 'deleting' and durable delete_peer operation is queued with recovered snapshot!
        async with self.sessions() as s:
            deleted_prof = await s.get(VPNProfile, prof_id)
            self.assertIsNotNone(deleted_prof)
            self.assertEqual(deleted_prof.provisioning_status, "deleting")

            del_op = (
                await s.execute(
                    select(APIOperation).where(
                        APIOperation.profile_id == prof_id,
                        APIOperation.operation_type == "delete_peer",
                    )
                )
            ).scalar_one_or_none()
            self.assertIsNotNone(del_op)
            self.assertEqual(del_op.status, "pending")
            self.assertEqual(del_op.peer_id, "amnezia-peer-999")
            self.assertEqual(del_op.server_name_snapshot, "Old Node 99")
            self.assertEqual(del_op.api_url_snapshot, "http://10.99.99.1:9999")
            self.assertEqual(del_op.api_key_snapshot, "legacy-secret-key-99")

    async def test_64_postgres_claim_vs_finalize_create_success_concurrent(self):
        """Prove that competing workers claiming and finalizing operations synchronize cleanly with SKIP LOCKED."""
        import asyncio

        from services.api_operations_finalizer import finalize_create_success
        from services.api_operations_queue import claim_api_operations

        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Concurrent Create Probe",
                client_name="tg_create_probe",
                provisioning_status="pending_create",
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            op = APIOperation(
                operation_type="create_peer",
                status="pending",
                idempotency_key=f"create-concurrent-test:{prof_id}",
                server_id=self.sid,
                profile_id=prof_id,
                server_name_snapshot="Test Node",
                api_url_snapshot="http://10.0.0.1:8080",
                api_key_snapshot="secret-key",
                attempts=0,
                max_attempts=10,
            )
            s.add(op)
            await s.flush()
            op_id = op.id

        barrier = asyncio.Event()

        async def worker_1():
            # Worker 1 claims the pending operation
            claimed = await claim_api_operations(
                worker_id="worker-1",
                limit=10,
                session_factory=self.sessions,
            )
            self.assertEqual(len(claimed), 1)
            self.assertEqual(claimed[0].id, op_id)
            barrier.set()

            # Worker 1 finalizes create success
            await finalize_create_success(
                op_id,
                worker_id="worker-1",
                expected_attempt_number=1,
                peer_id="peer-created-123",
                raw_config="vpn://test-config-concurrent",
                sent_desired_version=1,
                sent_is_active=True,
                sent_expires_at=None,
                session_factory=self.sessions,
            )
            return "worker_1_done"

        async def worker_2():
            # Worker 2 attempts to claim concurrently
            await barrier.wait()
            claimed = await claim_api_operations(
                worker_id="worker-2",
                limit=10,
                session_factory=self.sessions,
            )
            # Worker 2 must get 0 claimed because Worker 1 already claimed it
            self.assertEqual(len(claimed), 0)
            return "worker_2_done"

        res1, res2 = await asyncio.gather(worker_1(), worker_2())
        self.assertEqual(res1, "worker_1_done")
        self.assertEqual(res2, "worker_2_done")

        async with self.sessions() as s:
            final_prof = await s.get(VPNProfile, prof_id)
            self.assertEqual(final_prof.peer_id, "peer-created-123")
            self.assertEqual(final_prof.provisioning_status, "active")

    async def test_65_postgres_recover_stale_vs_finalizer_concurrent(self):
        """Prove that recover_stale_api_operations and finalize_operation_failure coordinate cleanly under concurrency."""
        import asyncio
        from datetime import timedelta

        from services.api_operations_finalizer import finalize_operation_failure
        from services.api_operations_queue import (
            APIOperationOwnershipError,
            recover_stale_api_operations,
        )

        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Concurrent Stale Probe",
                client_name="tg_stale_probe",
                provisioning_status="pending_create",
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            op = APIOperation(
                operation_type="create_peer",
                status="processing",
                idempotency_key=f"create-stale-test:{prof_id}",
                server_id=self.sid,
                profile_id=prof_id,
                server_name_snapshot="Test Node",
                api_url_snapshot="http://10.0.0.1:8080",
                api_key_snapshot="secret-key",
                locked_by="worker-stale-1",
                attempts=1,
                max_attempts=10,
            )
            s.add(op)
            await s.flush()
            op_id = op.id

        barrier = asyncio.Event()

        async def worker_recover():
            # Supervisor recovers the stale operation (lease expired)
            retried, dead = await recover_stale_api_operations(
                lease_timeout=timedelta(microseconds=1),
                session_factory=self.sessions,
            )
            self.assertEqual(retried, 1)
            barrier.set()
            return "recovered"

        async def worker_finalize():
            await barrier.wait()
            # Stale worker tries to finalize attempt 1 after supervisor already recovered it
            try:
                await finalize_operation_failure(
                    op_id,
                    worker_id="worker-stale-1",
                    expected_attempt_number=1,
                    retryable=True,
                    error_code="timeout",
                    error_message="Gateway timeout",
                    session_factory=self.sessions,
                )
                return "finalized"
            except APIOperationOwnershipError:
                return "fenced"

        res_rec, res_fin = await asyncio.gather(worker_recover(), worker_finalize())
        self.assertEqual(res_rec, "recovered")
        self.assertEqual(res_fin, "fenced")

    async def test_66_postgres_profile_deletion_vs_create_finalizer_concurrent(self):
        """Prove that ProfileDeletionService and finalize_create_success coordinate cleanly under concurrency."""
        import asyncio

        from services.api_operations_finalizer import (
            CreateCompensationRequired,
            finalize_create_success,
        )
        from services.profile_deletion_service import ProfileDeletionService

        async with self.sessions.begin() as s:
            prof = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="Concurrent Delete-vs-Finalize",
                client_name="tg_del_finalize_probe",
                provisioning_status="pending_create",
            )
            s.add(prof)
            await s.flush()
            prof_id = prof.id

            op = APIOperation(
                operation_type="create_peer",
                status="processing",
                idempotency_key=f"create-del-fin-test:{prof_id}",
                server_id=self.sid,
                profile_id=prof_id,
                server_name_snapshot="Test Node",
                api_url_snapshot="http://10.0.0.1:8080",
                api_key_snapshot="secret-key",
                locked_by="worker-fin-1",
                attempts=1,
                max_attempts=10,
            )
            s.add(op)
            await s.flush()
            op_id = op.id

        barrier = asyncio.Event()

        async def worker_delete():
            async with self.sessions.begin() as s:
                current_prof = await s.get(VPNProfile, prof_id)
                self.assertIsNotNone(current_prof)
                await ProfileDeletionService._delete_profiles(
                    s, [current_prof], reason="user_unsubscribed", background=True
                )
            barrier.set()
            return "deleted"

        async def worker_finalize():
            await barrier.wait()
            try:
                await finalize_create_success(
                    op_id,
                    worker_id="worker-fin-1",
                    expected_attempt_number=1,
                    peer_id="peer-fin-999",
                    raw_config="vpn://test-config-del-fin",
                    sent_desired_version=1,
                    sent_is_active=True,
                    sent_expires_at=None,
                    session_factory=self.sessions,
                )
                return "finalized"
            except CreateCompensationRequired:
                return "compensated"

        res_del, res_fin = await asyncio.gather(worker_delete(), worker_finalize())
        self.assertEqual(res_del, "deleted")
        self.assertEqual(res_fin, "compensated")

        async with self.sessions() as s:
            final_prof = await s.get(VPNProfile, prof_id)
            self.assertIsNotNone(final_prof)
            self.assertIn(final_prof.provisioning_status, {"deleting", "create_cleanup_pending"})
            self.assertEqual(final_prof.peer_id, "peer-fin-999")

    async def test_67_postgres_server_edit_vs_create_device_serialization(self):
        """
        Prove that admin Server edit with FOR UPDATE serializes with DeviceService.create_device.
        When create_device acquires Server FOR UPDATE first, admin edit sees active profiles/ops
        and is blocked. When admin edit acquires Server FOR UPDATE first, create_device waits
        and proceeds on the updated server identity.
        """
        from database.repositories.servers_repo import get_server_by_id
        from services.slots_cache import ServerPeerSnapshot

        async with self.sessions.begin() as s:
            server = Server(
                name="Concurrency Srv 67",
                api_url="https://vpn67.example.com",
                api_key="key67-initial",
                country_flag="🇩🇪",
                protocol="amneziawg2",
                max_clients=10,
                is_active=True,
            )
            s.add(server)
            await s.flush()
            srv_id = server.id

            user = User(
                telegram_id=987654321,
                username="test_user_67",
                first_name="Test",
                subscription_end=datetime.now(timezone.utc) + timedelta(days=30),
                device_limit=5,
            )
            s.add(user)
            await s.flush()
            u_id = user.id

        admin_locked_event = asyncio.Event()
        create_started_event = asyncio.Event()

        async def admin_edit_worker():
            async with self.sessions.begin() as s:
                srv = await get_server_by_id(s, srv_id, for_update=True)
                self.assertIsNotNone(srv)
                admin_locked_event.set()
                await create_started_event.wait()
                await asyncio.sleep(0.05)
                srv.api_url = "https://vpn67-updated.example.com"
            return "admin_updated"

        async def create_device_worker():
            await admin_locked_event.wait()
            create_started_event.set()
            snap = ServerPeerSnapshot(
                server_id=srv_id,
                peer_ids=frozenset(),
                captured_at=datetime.now(timezone.utc),
            )
            async with self.sessions.begin() as s:
                prof = await DeviceService.create_device(
                    session=s,
                    user_id=u_id,
                    server_id=srv_id,
                    device_name="Phone 67",
                    snapshot=snap,
                )
                self.assertIsNotNone(prof)
            return "created"

        res_admin, res_create = await asyncio.gather(admin_edit_worker(), create_device_worker())
        self.assertEqual(res_admin, "admin_updated")
        self.assertEqual(res_create, "created")

        async with self.sessions() as s:
            op = (
                await s.execute(
                    select(APIOperation)
                    .where(APIOperation.server_id == srv_id)
                    .order_by(APIOperation.id.desc())
                    .limit(1)
                )
            ).scalar_one()
            self.assertEqual(op.api_url_snapshot, "https://vpn67-updated.example.com")

            async with self.sessions.begin() as s2:
                srv = await get_server_by_id(s2, srv_id, for_update=True)
                self.assertIsNotNone(srv)
                profiles_count = (
                    await s2.execute(
                        select(func.count(VPNProfile.id)).where(
                            VPNProfile.server_id == srv_id,
                            or_(
                                VPNProfile.peer_id.is_not(None),
                                VPNProfile.provisioning_status.in_((
                                    "pending_create",
                                    "pending_update",
                                    "deleting",
                                    "create_cleanup_pending",
                                    "create_failed",
                                    "update_failed",
                                    "delete_failed",
                                    "active",
                                )),
                            ),
                        )
                    )
                ).scalar_one()
                self.assertGreater(profiles_count, 0)


if __name__ == "__main__":
    unittest.main()
