import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from database.models import Server, VPNProfile
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
        session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=profile)))
        session.get = AsyncMock(return_value=server)
        session.delete = AsyncMock()

        with patch("services.device_service.ensure_delete_operation", new_callable=AsyncMock):
            result = await DeviceService.delete_device(session, profile, force=True)

            self.assertTrue(result)
            session.delete.assert_called_once_with(profile)

    async def test_repo_excludes_deleting_but_keeps_create_cleanup_pending_profiles(self):
        """Problem create states should remain visible instead of disappearing."""
        p_cleanup = VPNProfile(id=1, user_id=1, provisioning_status="create_cleanup_pending")

        session = AsyncMock()
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
        self.assertIn("provisioning_status != 'deleting'", compiled)
        self.assertNotIn("create_cleanup_pending", compiled)
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
        session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=mock_scalars)))
        session.delete = AsyncMock()

        count = await ProfileDeletionService.delete_profiles_for_user(session, user_id=1, reason="test_ban")
        self.assertEqual(count, 1)
        session.delete.assert_called_once_with(p_no_peer)
