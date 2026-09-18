import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers.admin.users.mass_bonus import _run_mass_bonus_background
from bot.handlers.connection.device_create_routes import _await_profile_ready
from database.models import Server
from database.repositories.profiles_repo import (
    PROFILE_LIST_HIDDEN_STATUSES,
    PROFILE_QUOTA_EXCLUDED_STATUSES,
)
from database.repositories.servers_repo import get_available_servers
from services.device_service import RESERVING_STATUSES


class TestServerOutageResilience(unittest.IsolatedAsyncioTestCase):

    def test_delete_failed_excluded_from_quota_and_hidden_from_list(self):
        """Verify delete_failed does not consume user quota and is hidden from UI."""
        self.assertIn("delete_failed", PROFILE_QUOTA_EXCLUDED_STATUSES)
        self.assertIn("delete_failed", PROFILE_LIST_HIDDEN_STATUSES)
        self.assertNotIn("delete_failed", RESERVING_STATUSES)

    async def test_get_available_servers_filters_unhealthy(self):
        """Available servers must exclude unhealthy or problem states."""
        from config.enums import ServerHealthState

        mock_session = AsyncMock(spec=AsyncSession)
        s_online = Server(
            id=1, name="NL", protocol="amneziawg2", is_active=True,
            health_state=ServerHealthState.ONLINE, max_clients=100, capabilities=[]
        )
        s_problem = Server(
            id=2, name="DE", protocol="amneziawg2", is_active=True,
            health_state=ServerHealthState.PROBLEM, max_clients=100, capabilities=[]
        )
        s_disabled = Server(
            id=3, name="EE", protocol="amneziawg2", is_active=True,
            health_state=ServerHealthState.AUTO_DISABLED, max_clients=100, capabilities=[]
        )

        with patch("database.repositories.servers_repo.get_active_servers", return_value=[s_online, s_problem, s_disabled]), \
             patch("database.repositories.servers_repo.get_server_peer_counts", return_value={1: 10}), \
             patch("database.repositories.servers_repo.get_cached_peer_count", return_value=10):
            available = await get_available_servers(mock_session)
            self.assertEqual(len(available), 1)
            self.assertEqual(available[0].id, 1)

    async def test_await_profile_ready_default_timeout(self):
        """Verify _await_profile_ready has a default 15s timeout for UI in-place waiting."""
        import inspect
        sig = inspect.signature(_await_profile_ready)
        self.assertEqual(sig.parameters["timeout_seconds"].default, 15.0)

    async def test_mass_bonus_server_audience_filters_by_server_id(self):
        """Mass bonus with server target filters users with devices on that server."""
        mock_bot = MagicMock(spec=Bot)
        mock_bot.send_message = AsyncMock()

        mock_session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.all.return_value = [(1, 1001), (2, 1002)]
        mock_session.execute.return_value = mock_result

        with patch("database.connection.session_scope") as mock_scope, \
             patch("bot.handlers.admin.users.mass_bonus.create_admin_adjustment", return_value=(MagicMock(), True)), \
             patch("services.audit_service.AuditService.log_action", return_value=None):
            mock_scope.return_value.__aenter__.return_value = mock_session

            await _run_mass_bonus_background(
                bot=mock_bot,
                admin_id=999,
                target_aud="server_2",
                amount=100,
                reason="Server outage compensation",
                batch_id="test_batch",
            )

            first_call_stmt = mock_session.execute.call_args_list[0][0][0]
            sql_str = str(first_call_stmt.compile(compile_kwargs={"literal_binds": True}))
            self.assertIn("vpn_profiles", sql_str)
            self.assertIn("white_internet_subscriptions", sql_str)
            self.assertIn("server_id = 2", sql_str)


if __name__ == "__main__":
    unittest.main()
