import os
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import APIOperation, Server, User, VPNProfile
from services.api_operations_queue import enqueue_api_operation
from services.device_service import (
    DeviceCreationError,
    DeviceMigrationInProgress,
    DeviceService,
    MigrationCooldownActive,
)
from services.slots_cache import ServerPeerSnapshot
from utils.datetime_helpers import now_utc


class TestDeviceMigrationService(unittest.IsolatedAsyncioTestCase):
    """Unit tests for DeviceService.migrate_device and finalizer grace deletion."""

    def setUp(self):
        super().setUp()
        self.now = now_utc()

    @patch("services.device_service.ensure_server_capacity", new_callable=AsyncMock)
    @patch("services.device_service.AuditService.log_action", new_callable=AsyncMock)
    @patch("services.device_service.enqueue_api_operation", new_callable=AsyncMock)
    async def test_migrate_device_success_and_payload(
        self, mock_enqueue, mock_audit, mock_ensure_capacity
    ):
        mock_session = AsyncMock()

        # Mock User
        user = User(
            id=1,
            telegram_id=12345,
            device_limit=1,
            subscription_end=self.now + timedelta(days=30),
            is_banned=False,
            device_creations_today=0,
            last_creation_date=self.now.date(),
        )

        # Mock Old Profile (created 20 mins ago, cooldown passed)
        old_profile = VPNProfile(
            id=10,
            user_id=1,
            server_id=100,
            device_name="Phone #1",
            peer_id="peer-old-123",
            provisioning_status="active",
            created_at=self.now - timedelta(minutes=20),
        )

        # Mock Target Server
        target_server = Server(
            id=200,
            name="Germany",
            protocol="amneziawg2",
            is_active=True,
            max_clients=50,
            api_url="https://vpn.example.com",
            api_key="secret",
        )

        # Set up execute scalar results
        async def mock_execute(query, *args, **kwargs):
            m = MagicMock()
            m.scalar_one_or_none.return_value = None
            q_str = str(query).lower()
            if "from users" in q_str:
                m.scalar_one.return_value = user
            elif "from vpn_profiles" in q_str and "where vpn_profiles.id =" in q_str:
                m.scalar_one_or_none.return_value = old_profile
            elif "from servers" in q_str:
                m.scalar_one_or_none.return_value = target_server
            elif "count(vpn_profiles.id)" in q_str:
                # Quota count or server count
                m.scalar_one.return_value = 0
            elif "vpn_profiles.peer_id" in q_str:
                m.scalars.return_value.all.return_value = []
            elif "lower(vpn_profiles.device_name)" in q_str:
                m.scalar_one_or_none.return_value = None
            return m

        mock_session.execute = mock_execute
        mock_session.add = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_session.begin_nested = MagicMock(return_value=mock_ctx)

        snapshot = ServerPeerSnapshot(
            server_id=200,
            peer_ids=set(),
            captured_at=self.now,
        )

        new_profile = await DeviceService.migrate_device(
            mock_session,
            user_id=1,
            profile_id=10,
            target_server_id=200,
            snapshot=snapshot,
        )

        self.assertEqual(new_profile.server_id, 200)
        self.assertEqual(new_profile.device_name, "Phone #1")
        self.assertEqual(new_profile.provisioning_status, "pending_create")

        # Verify old profile was NOT modified to deleting yet (remains active until finalizer)
        self.assertEqual(old_profile.provisioning_status, "active")

        # Verify enqueue_api_operation was called with migrating_from_id in payload
        mock_enqueue.assert_called_once()
        _, kwargs = mock_enqueue.call_args
        self.assertEqual(kwargs["operation_type"], "create_peer")
        self.assertEqual(kwargs["server_id"], 200)
        self.assertEqual(kwargs["payload"]["migrating_from_id"], 10)

    @patch.object(DeviceService, "get_last_migration_time", new_callable=AsyncMock)
    @patch.object(DeviceService, "has_active_migration", new_callable=AsyncMock)
    async def test_migrate_device_cooldown_rejection(
        self, mock_has_active, mock_get_last_migration
    ):
        mock_has_active.return_value = False
        mock_get_last_migration.return_value = self.now - timedelta(minutes=5)
        mock_session = AsyncMock()

        user = User(
            id=1,
            telegram_id=12345,
            device_limit=1,
            subscription_end=self.now + timedelta(days=30),
            is_banned=False,
        )

        old_profile = VPNProfile(
            id=10,
            user_id=1,
            server_id=100,
            device_name="Phone #1",
            provisioning_status="active",
            created_at=self.now - timedelta(minutes=20),
        )

        target_server = Server(
            id=200,
            name="Germany",
            protocol="amneziawg2",
            is_active=True,
            max_clients=50,
            api_url="https://vpn.example.com",
            api_key="secret",
        )

        async def mock_execute(query, *args, **kwargs):
            m = MagicMock()
            q_str = str(query).lower()
            if "from users" in q_str:
                m.scalar_one.return_value = user
            elif "from vpn_profiles" in q_str:
                m.scalar_one_or_none.return_value = old_profile
            elif "from servers" in q_str:
                m.scalar_one_or_none.return_value = target_server
            return m

        mock_session.execute = mock_execute

        snapshot = ServerPeerSnapshot(
            server_id=200,
            peer_ids=set(),
            captured_at=self.now,
        )

        with self.assertRaises(MigrationCooldownActive) as ctx:
            await DeviceService.migrate_device(
                mock_session,
                user_id=1,
                profile_id=10,
                target_server_id=200,
                snapshot=snapshot,
            )
        self.assertTrue(ctx.exception.remaining_seconds > 0)

    @patch("services.device_service.ensure_server_capacity", new_callable=AsyncMock)
    @patch("services.device_service.AuditService.log_action", new_callable=AsyncMock)
    @patch("services.device_service.enqueue_api_operation", new_callable=AsyncMock)
    @patch.object(DeviceService, "get_last_migration_time", new_callable=AsyncMock)
    @patch.object(DeviceService, "has_active_migration", new_callable=AsyncMock)
    async def test_first_migration_allowed_for_recent_device(
        self,
        mock_has_active,
        mock_get_last_migration,
        mock_enqueue,
        mock_audit,
        mock_ensure_capacity,
    ):
        mock_has_active.return_value = False
        mock_get_last_migration.return_value = None  # Never migrated before

        mock_session = AsyncMock()
        user = User(
            id=1,
            telegram_id=12345,
            device_limit=1,
            subscription_end=self.now + timedelta(days=30),
            is_banned=False,
            device_creations_today=0,
            last_creation_date=self.now.date(),
        )
        # Created only 1 minute ago, but first migration is allowed immediately
        old_profile = VPNProfile(
            id=10,
            user_id=1,
            server_id=100,
            device_name="Phone #1",
            peer_id="peer-old-123",
            provisioning_status="active",
            created_at=self.now - timedelta(minutes=1),
        )
        target_server = Server(
            id=200,
            name="Germany",
            protocol="amneziawg2",
            is_active=True,
            max_clients=50,
            api_url="https://vpn.example.com",
            api_key="secret",
        )

        async def mock_execute(query, *args, **kwargs):
            m = MagicMock()
            q_str = str(query).lower()
            if "from users" in q_str:
                m.scalar_one.return_value = user
            elif "from vpn_profiles" in q_str and "where vpn_profiles.id =" in q_str:
                m.scalar_one_or_none.return_value = old_profile
            elif "from servers" in q_str:
                m.scalar_one_or_none.return_value = target_server
            elif "count(vpn_profiles.id)" in q_str:
                m.scalar_one.return_value = 0
            elif "vpn_profiles.peer_id" in q_str:
                m.scalars.return_value.all.return_value = []
            elif "lower(vpn_profiles.device_name)" in q_str:
                m.scalar_one_or_none.return_value = None
            return m

        mock_session.execute = mock_execute
        mock_session.add = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_session.begin_nested = MagicMock(return_value=mock_ctx)

        snapshot = ServerPeerSnapshot(
            server_id=200,
            peer_ids=set(),
            captured_at=self.now,
        )

        new_profile = await DeviceService.migrate_device(
            mock_session,
            user_id=1,
            profile_id=10,
            target_server_id=200,
            snapshot=snapshot,
        )
        self.assertEqual(new_profile.server_id, 200)

    @patch.object(DeviceService, "has_active_migration", new_callable=AsyncMock)
    async def test_migrate_device_blocked_when_already_migrating(
        self, mock_has_active
    ):
        mock_has_active.return_value = True

        mock_session = AsyncMock()
        user = User(
            id=1,
            telegram_id=12345,
            device_limit=1,
            subscription_end=self.now + timedelta(days=30),
            is_banned=False,
        )
        old_profile = VPNProfile(
            id=10,
            user_id=1,
            server_id=100,
            device_name="Phone #1",
            provisioning_status="active",
            created_at=self.now - timedelta(minutes=20),
        )

        async def mock_execute(query, *args, **kwargs):
            m = MagicMock()
            q_str = str(query).lower()
            if "from users" in q_str:
                m.scalar_one.return_value = user
            elif "from vpn_profiles" in q_str:
                m.scalar_one_or_none.return_value = old_profile
            return m

        mock_session.execute = mock_execute
        snapshot = ServerPeerSnapshot(
            server_id=200, peer_ids=set(), captured_at=self.now
        )

        with self.assertRaises(DeviceMigrationInProgress):
            await DeviceService.migrate_device(
                mock_session,
                user_id=1,
                profile_id=10,
                target_server_id=200,
                snapshot=snapshot,
            )

    @patch.object(DeviceService, "has_active_migration", new_callable=AsyncMock)
    @patch(
        "services.device_service.resolve_profile_endpoint_snapshot",
        new_callable=AsyncMock,
    )
    @patch("services.device_service.ensure_delete_operation", new_callable=AsyncMock)
    @patch("services.device_service.AuditService.log_action", new_callable=AsyncMock)
    async def test_delete_device_blocked_during_active_migration(
        self, mock_audit, mock_ensure_delete, mock_resolve_snapshot, mock_has_active
    ):
        mock_session = AsyncMock()
        mock_has_active.return_value = True
        mock_resolve_snapshot.return_value = (
            100,
            "Server1",
            "https://srv.test",
            "key",
        )

        user = User(id=1, telegram_id=12345)
        profile = VPNProfile(
            id=10,
            user_id=1,
            server_id=100,
            device_name="Phone #1",
            peer_id="peer-10",
            provisioning_status="active",
        )

        mock_exec = MagicMock()
        mock_exec.scalar_one_or_none.return_value = profile
        mock_session.execute = AsyncMock(return_value=mock_exec)

        # Non-force delete must raise DeviceMigrationInProgress
        with self.assertRaises(DeviceMigrationInProgress):
            await DeviceService.delete_device(mock_session, profile, actor_id=user.id, force=False)

        # Force delete must bypass the active migration check
        mock_session.delete = AsyncMock()
        await DeviceService.delete_device(mock_session, profile, actor_id=user.id, force=True)
        mock_ensure_delete.assert_called_once()

    async def test_migrate_device_same_server_rejection(self):
        mock_session = AsyncMock()

        user = User(id=1, telegram_id=12345, subscription_end=self.now + timedelta(days=30))
        old_profile = VPNProfile(
            id=10,
            user_id=1,
            server_id=100,
            device_name="Phone #1",
            provisioning_status="active",
            created_at=self.now - timedelta(minutes=20),
        )

        async def mock_execute(query, *args, **kwargs):
            m = MagicMock()
            q_str = str(query).lower()
            if "from users" in q_str:
                m.scalar_one.return_value = user
            elif "from vpn_profiles" in q_str:
                m.scalar_one_or_none.return_value = old_profile
            return m

        mock_session.execute = mock_execute

        snapshot = ServerPeerSnapshot(
            server_id=100,
            peer_ids=set(),
            captured_at=self.now,
        )

        with self.assertRaises(DeviceCreationError) as ctx:
            await DeviceService.migrate_device(
                mock_session,
                user_id=1,
                profile_id=10,
                target_server_id=100,
                snapshot=snapshot,
            )
        self.assertIn("cannot be the same", str(ctx.exception).lower())


class TestMigrationFinalizerHook(unittest.IsolatedAsyncioTestCase):
    """Test _schedule_migration_grace_deletion in api_operations_finalizer."""

    @patch("services.api_operations_queue.ensure_delete_operation", new_callable=AsyncMock)
    @patch("services.api_operations_queue.resolve_profile_endpoint_snapshot", new_callable=AsyncMock)
    async def test_migration_grace_deletion_scheduled(
        self, mock_resolve_snapshot, mock_ensure_delete
    ):
        from services.api_operations_finalizer import _schedule_migration_grace_deletion

        mock_session = AsyncMock()
        operation = SimpleNamespace(
            payload={"migrating_from_id": 55},
        )
        new_profile = SimpleNamespace(
            id=99,
            provisioning_status="active",
        )
        old_profile = VPNProfile(
            id=55,
            server_id=10,
            peer_id="peer-55",
            client_name="tg_123_p55",
            provisioning_status="active",
        )

        mock_execute_res = MagicMock()
        mock_execute_res.scalar_one_or_none.return_value = old_profile
        mock_session.execute = AsyncMock(return_value=mock_execute_res)

        mock_resolve_snapshot.return_value = (10, "OldServer", "https://old.server", "key")

        await _schedule_migration_grace_deletion(mock_session, operation, new_profile)

        # Old profile must be set to deleting
        self.assertEqual(old_profile.provisioning_status, "deleting")

        # ensure_delete_operation must be called with next_attempt_at in ~15 mins
        mock_ensure_delete.assert_called_once()
        _, kwargs = mock_ensure_delete.call_args
        self.assertEqual(kwargs["peer_id"], "peer-55")
        self.assertEqual(kwargs["server_id"], 10)
        self.assertEqual(kwargs["profile_id"], 55)
        self.assertEqual(kwargs["audit_reason"], "device_migration_grace_expired")
        self.assertIsNotNone(kwargs.get("next_attempt_at"))
        delta = kwargs["next_attempt_at"] - now_utc()
        self.assertTrue(14 * 60 <= delta.total_seconds() <= 16 * 60)

    async def test_migration_grace_ignored_if_new_profile_not_active(self):
        from services.api_operations_finalizer import _schedule_migration_grace_deletion

        mock_session = AsyncMock()
        operation = SimpleNamespace(
            payload={"migrating_from_id": 55},
        )
        # If new profile failed
        new_profile = SimpleNamespace(
            id=99,
            provisioning_status="create_failed",
        )

        await _schedule_migration_grace_deletion(mock_session, operation, new_profile)
        # Session should not even be queried
        mock_session.execute.assert_not_called()


class TestDeviceMigrateRoutes(unittest.IsolatedAsyncioTestCase):
    """Test start_migrate_device and route guards."""

    @patch("bot.handlers.connection.device_migrate_routes.SubscriptionService.check_access", new_callable=AsyncMock)
    @patch("bot.handlers.connection.device_migrate_routes.MaintenanceService.can_user_perform_action", new_callable=AsyncMock)
    @patch("bot.handlers.connection.device_migrate_routes.get_profile_by_id", new_callable=AsyncMock)
    @patch("bot.handlers.connection.device_migrate_routes.get_user_by_telegram_id", new_callable=AsyncMock)
    @patch.object(DeviceService, "has_active_migration", new_callable=AsyncMock)
    @patch.object(DeviceService, "get_last_migration_time", new_callable=AsyncMock)
    @patch("bot.handlers.connection.device_migrate_routes.get_available_servers", new_callable=AsyncMock)
    @patch("bot.handlers.connection.device_migrate_routes.get_server_by_id", new_callable=AsyncMock)
    @patch("bot.handlers.connection.device_migrate_routes.render_hub", new_callable=AsyncMock)
    async def test_start_migrate_device_allows_recent_device_and_renders_servers(
        self,
        mock_render,
        mock_get_server,
        mock_get_available,
        mock_get_last_migrated,
        mock_has_active,
        mock_get_user,
        mock_get_profile,
        mock_can_perform,
        mock_check_access,
    ):
        from bot.handlers.connection.device_migrate_routes import start_migrate_device

        mock_can_perform.return_value = True
        mock_check_access.return_value = True
        mock_has_active.return_value = False
        mock_get_last_migrated.return_value = None  # Never migrated before

        user = User(id=1, telegram_id=12345)
        profile = VPNProfile(
            id=10,
            user_id=1,
            server_id=100,
            device_name="Phone #1",
            provisioning_status="active",
            created_at=now_utc() - timedelta(minutes=1),  # Created 1 min ago
        )
        mock_get_user.return_value = user
        mock_get_profile.return_value = profile

        curr_server = Server(id=100, name="Server 1", country_flag="🇩🇪")
        target_server = Server(id=200, name="Server 2", country_flag="🇳🇱")
        mock_get_server.return_value = curr_server
        mock_get_available.return_value = [curr_server, target_server]

        callback = MagicMock()
        callback.from_user.id = 12345
        callback.data = "migrate_device:10"
        callback.answer = AsyncMock()
        state = AsyncMock()
        session = AsyncMock()

        await start_migrate_device(callback, state, session, db_user=user)

        # Must succeed and render target server selection
        mock_render.assert_called_once()
        callback.answer.assert_called_once_with(show_alert=False)

    @patch("bot.handlers.connection.device_migrate_routes.SubscriptionService.check_access", new_callable=AsyncMock)
    @patch("bot.handlers.connection.device_migrate_routes.MaintenanceService.can_user_perform_action", new_callable=AsyncMock)
    @patch("bot.handlers.connection.device_migrate_routes.get_profile_by_id", new_callable=AsyncMock)
    @patch("bot.handlers.connection.device_migrate_routes.get_user_by_telegram_id", new_callable=AsyncMock)
    @patch.object(DeviceService, "has_active_migration", new_callable=AsyncMock)
    async def test_start_migrate_device_blocked_during_active_migration(
        self,
        mock_has_active,
        mock_get_user,
        mock_get_profile,
        mock_can_perform,
        mock_check_access,
    ):
        from bot.handlers.connection.device_migrate_routes import start_migrate_device
        from bot import texts

        mock_can_perform.return_value = True
        mock_check_access.return_value = True
        mock_has_active.return_value = True  # In-flight migration

        user = User(id=1, telegram_id=12345)
        profile = VPNProfile(
            id=10,
            user_id=1,
            server_id=100,
            device_name="Phone #1",
            provisioning_status="active",
        )
        mock_get_user.return_value = user
        mock_get_profile.return_value = profile

        callback = MagicMock()
        callback.from_user.id = 12345
        callback.data = "migrate_device:10"
        callback.answer = AsyncMock()
        state = AsyncMock()
        session = AsyncMock()

        await start_migrate_device(callback, state, session, db_user=user)

        callback.answer.assert_called_once_with(texts.DEVICE_MIGRATE_IN_PROGRESS, show_alert=True)


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not set")
class TestDeviceMigrationPostgres(unittest.IsolatedAsyncioTestCase):
    """PostgreSQL integration tests verifying JSONB migration query semantics."""

    async def asyncSetUp(self):
        if not os.environ["TEST_DATABASE_URL"].startswith(
            ("postgresql://", "postgresql+asyncpg://")
        ):
            self.fail("TEST_DATABASE_URL must point to PostgreSQL")
        self.engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        try:
            from tests.db_utils import TRUNCATE_SQL
        except ImportError:
            from db_utils import TRUNCATE_SQL
        async with self.sessions.begin() as s:
            from sqlalchemy import text

            await s.execute(text(TRUNCATE_SQL))

    async def asyncTearDown(self):
        try:
            from tests.db_utils import TRUNCATE_SQL
        except ImportError:
            from db_utils import TRUNCATE_SQL
        async with self.sessions.begin() as s:
            from sqlalchemy import text

            await s.execute(text(TRUNCATE_SQL))
        await self.engine.dispose()

    async def test_has_active_migration_postgres_jsonb(self):
        async with self.sessions.begin() as session:
            # Initially no active migration for profile 42
            self.assertFalse(await DeviceService.has_active_migration(session, 42))

            # Enqueue a pending create_peer migrating from 42
            await enqueue_api_operation(
                session,
                operation_type="create_peer",
                idempotency_key="migrate-test-1",
                api_url_snapshot="https://test.server",
                api_key_snapshot="key",
                client_name="test_client",
                payload={"migrating_from_id": 42},
            )

        async with self.sessions.begin() as session:
            # Now active migration should be detected
            self.assertTrue(await DeviceService.has_active_migration(session, 42))
            # Different profile should still be false
            self.assertFalse(await DeviceService.has_active_migration(session, 99))

    async def test_get_last_migration_time_postgres_jsonb(self):
        async with self.sessions.begin() as session:
            u = User(telegram_id=987654)
            s = Server(
                name="TestServer",
                protocol="amneziawg2",
                is_active=True,
                max_clients=50,
                api_url="https://vpn.example.com",
                api_key="secret",
            )
            session.add_all([u, s])
            await session.flush()

            p = VPNProfile(
                user_id=u.id,
                server_id=s.id,
                device_name="TestDevice",
                provisioning_status="active",
            )
            session.add(p)
            await session.flush()

            # Initially None
            self.assertIsNone(await DeviceService.get_last_migration_time(session, p.id))

            # Add succeeded create_peer operation that created profile p.id with migrating_from_id
            t0 = now_utc() - timedelta(minutes=10)
            op = APIOperation(
                operation_type="create_peer",
                idempotency_key="migrate-test-2",
                profile_id=p.id,
                server_id=s.id,
                client_name="client",
                status="succeeded",
                payload={"migrating_from_id": 50},
                completed_at=t0,
            )
            session.add(op)

        async with self.sessions.begin() as session:
            last_time = await DeviceService.get_last_migration_time(session, p.id)
            self.assertIsNotNone(last_time)
            self.assertAlmostEqual(last_time.timestamp(), t0.timestamp(), delta=2)
