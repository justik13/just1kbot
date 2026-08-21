import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from database.models import APIOperation, Server, VPNProfile
from database.repositories.profiles_repo import get_user_profiles
from services.device_service import DeviceService


class DeviceDeletionAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_device_without_peer_id_removes_profile(self):
        """Profile without peer_id should be deleted from session directly."""
        profile = VPNProfile(
            id=10,
            user_id=1,
            server_id=2,
            device_name="Test Phone",
            peer_id=None,
            provisioning_status="active",
        )

        session = AsyncMock()
        mock_ctx = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock()
        mock_ctx.__aenter__ = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=session)
        mock_ctx.__aexit__ = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=None)
        session.begin_nested = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock(return_value=mock_ctx)
        session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=profile)))
        session.delete = AsyncMock()

        result = await DeviceService.delete_device(session, profile)

        self.assertTrue(result)
        session.delete.assert_called_once_with(profile)

    async def test_delete_device_with_force_deletes_profile(self):
        """Force deletion by admin should immediately remove profile from session."""
        profile = VPNProfile(
            id=11,
            user_id=1,
            server_id=2,
            device_name="Admin Laptop",
            peer_id="peer-123",
            provisioning_status="active",
        )
        server = Server(id=2, name="US Server", api_url="https://us.vpn", api_key="secret")

        session = AsyncMock()
        mock_ctx = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock()
        mock_ctx.__aenter__ = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=session)
        mock_ctx.__aexit__ = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=None)
        session.begin_nested = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock(return_value=mock_ctx)
        session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=profile)))
        session.get = AsyncMock(return_value=server)
        session.delete = AsyncMock()

        with patch("services.device_service.ensure_delete_operation", new_callable=AsyncMock):
            result = await DeviceService.delete_device(session, profile, force=True)

            self.assertTrue(result)
            session.delete.assert_called_once_with(profile)

    async def test_force_delete_uses_create_operation_peer_identity(self):
        profile = VPNProfile(
            id=12,
            user_id=1,
            server_id=2,
            device_name="Cleanup",
            peer_id=None,
            client_name="tg_1_p12",
            provisioning_status="create_cleanup_pending",
        )
        create_operation = APIOperation(
            id=20,
            operation_type="create_peer",
            idempotency_key="create-peer:12:v1",
            status="dead",
            peer_id="created-peer",
            client_name=profile.client_name,
            attempts=1,
        )
        session = AsyncMock()
        mock_ctx = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock()
        mock_ctx.__aenter__ = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=session)
        mock_ctx.__aexit__ = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=None)
        session.begin_nested = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock(return_value=mock_ctx)
        session.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=profile)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=create_operation)),
            ]
        )

        with patch(
            "services.device_service.resolve_profile_endpoint_snapshot",
            new=AsyncMock(return_value=(2, "US", "https://us.vpn", "secret")),
        ), patch(
            "services.device_service.ensure_delete_operation",
            new=AsyncMock(),
        ) as ensure_delete:
            await DeviceService.delete_device(session, profile, force=True)

        self.assertEqual(ensure_delete.await_args.kwargs["peer_id"], "created-peer")
        session.delete.assert_awaited_once_with(profile)

    async def test_force_delete_keeps_anchor_for_attempted_create_without_peer_id(self):
        profile = VPNProfile(
            id=13,
            user_id=1,
            server_id=2,
            device_name="Creating",
            peer_id=None,
            client_name="tg_1_p13",
            provisioning_status="pending_create",
            desired_is_active=True,
            is_active=True,
        )
        create_operation = APIOperation(
            id=21,
            operation_type="create_peer",
            idempotency_key="create-peer:13:v1",
            status="processing",
            attempts=1,
            client_name=profile.client_name,
        )
        session = AsyncMock()
        mock_ctx = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock()
        mock_ctx.__aenter__ = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=session)
        mock_ctx.__aexit__ = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=None)
        session.begin_nested = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock(return_value=mock_ctx)
        session.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=profile)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=create_operation)),
            ]
        )

        with patch(
            "services.device_service.resolve_profile_endpoint_snapshot",
            new=AsyncMock(return_value=(2, "US", "https://us.vpn", "secret")),
        ):
            await DeviceService.delete_device(session, profile, force=True)

        session.delete.assert_not_awaited()
        self.assertEqual(profile.provisioning_status, "create_cleanup_pending")
        self.assertFalse(profile.desired_is_active)
        self.assertFalse(profile.is_active)

    async def test_force_delete_cancels_retry_create_before_deleting_known_peer(self):
        profile = VPNProfile(
            id=14,
            user_id=1,
            server_id=2,
            device_name="Retrying",
            peer_id=None,
            client_name="tg_1_p14",
            provisioning_status="create_cleanup_pending",
        )
        create_operation = APIOperation(
            id=22,
            operation_type="create_peer",
            idempotency_key="create-peer:14:v1",
            status="retry",
            peer_id="known-peer",
            client_name=profile.client_name,
            attempts=1,
        )
        session = AsyncMock()
        mock_ctx = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock()
        mock_ctx.__aenter__ = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=session)
        mock_ctx.__aexit__ = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=None)
        session.begin_nested = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock(return_value=mock_ctx)
        session.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=profile)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=create_operation)),
            ]
        )

        with patch(
            "services.device_service.resolve_profile_endpoint_snapshot",
            new=AsyncMock(return_value=(2, "US", "https://us.vpn", "secret")),
        ), patch(
            "services.device_service.ensure_delete_operation",
            new=AsyncMock(),
        ):
            await DeviceService.delete_device(session, profile, force=True)

        self.assertEqual(create_operation.status, "cancelled")
        session.delete.assert_awaited_once_with(profile)

    async def test_force_delete_keeps_processing_create_anchor_even_with_operation_peer(self):
        profile = VPNProfile(
            id=15,
            user_id=1,
            server_id=2,
            device_name="Processing",
            peer_id=None,
            client_name="tg_1_p15",
            provisioning_status="create_cleanup_pending",
            desired_is_active=True,
            is_active=True,
        )
        create_operation = APIOperation(
            id=23,
            operation_type="create_peer",
            idempotency_key="create-peer:15:v1",
            status="processing",
            peer_id="known-peer",
            client_name=profile.client_name,
            attempts=1,
        )
        session = AsyncMock()
        mock_ctx = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock()
        mock_ctx.__aenter__ = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=session)
        mock_ctx.__aexit__ = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=None)
        session.begin_nested = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock(return_value=mock_ctx)
        session.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=profile)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=create_operation)),
            ]
        )

        with patch(
            "services.device_service.resolve_profile_endpoint_snapshot",
            new=AsyncMock(return_value=(2, "US", "https://us.vpn", "secret")),
        ), patch(
            "services.device_service.ensure_delete_operation",
            new=AsyncMock(),
        ) as ensure_delete:
            await DeviceService.delete_device(session, profile, force=True)

        ensure_delete.assert_not_awaited()
        session.delete.assert_not_awaited()
        self.assertEqual(profile.provisioning_status, "create_cleanup_pending")

    async def test_repo_excludes_deleting_but_keeps_create_cleanup_pending_profiles(self):
        """Problem create states should remain visible instead of disappearing."""
        p_cleanup = VPNProfile(id=1, user_id=1, provisioning_status="create_cleanup_pending")

        session = AsyncMock()
        mock_ctx = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock()
        mock_ctx.__aenter__ = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=session)
        mock_ctx.__aexit__ = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=None)
        session.begin_nested = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock(return_value=mock_ctx)
        session.execute = AsyncMock(
            return_value=MagicMock(
                scalars=MagicMock(
                    return_value=MagicMock(all=MagicMock(return_value=[p_cleanup]))
                )
            )
        )

        profiles = await get_user_profiles(session, user_id=1)

        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        self.assertTrue("not in" in compiled.lower() or "notin" in compiled.lower() or "!= 'deleting'" in compiled)
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].id, 1)

    async def test_profile_deletion_service_deletes_non_peer_id_profile(self):
        """ProfileDeletionService should delete profiles without peer_id from session directly."""
        from services.profile_deletion_service import ProfileDeletionService

        p_no_peer = VPNProfile(id=12, user_id=1, peer_id=None, provisioning_status="active")
        mock_scalars = MagicMock()
        mock_scalars.__iter__ = MagicMock(return_value=iter([p_no_peer]))
        mock_scalars.all = MagicMock(return_value=[p_no_peer])
        session = AsyncMock()
        mock_ctx = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock()
        mock_ctx.__aenter__ = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=session)
        mock_ctx.__aexit__ = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=None)
        session.begin_nested = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock(return_value=mock_ctx)
        session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=mock_scalars)))
        session.delete = AsyncMock()

        count = await ProfileDeletionService.delete_profiles_for_user(session, user_id=1, reason="test_ban")
        self.assertEqual(count, 1)
        session.delete.assert_called_once_with(p_no_peer)
