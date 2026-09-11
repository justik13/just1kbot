import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from database.models import Server, User, VPNProfile
from services.device_service import (
    DeviceCreationError,
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

    async def test_migrate_device_cooldown_rejection(self):
        mock_session = AsyncMock()

        user = User(
            id=1,
            telegram_id=12345,
            device_limit=1,
            subscription_end=self.now + timedelta(days=30),
            is_banned=False,
        )

        # Profile created 5 mins ago (cooldown 15m active)
        old_profile = VPNProfile(
            id=10,
            user_id=1,
            server_id=100,
            device_name="Phone #1",
            provisioning_status="active",
            created_at=self.now - timedelta(minutes=5),
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
