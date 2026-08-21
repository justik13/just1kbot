import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from services.amnezia_client import _circuit_breakers, _get_circuit_breaker
from services.workers.cleanup import _cleanup_dangling_peers
from services.workers.traffic import _traffic_sync_once


class InactiveServerHandlingTests(unittest.IsolatedAsyncioTestCase):
    @patch("services.workers.traffic.session_scope")
    @patch("services.workers.traffic.AmneziaClient")
    async def test_traffic_sync_once_ignores_inactive_servers(self, mock_client_cls, mock_session_scope):
        mock_session = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = session
        session.begin_nested.return_value.__aexit__.return_value = None
        mock_session_scope.return_value.__aenter__.return_value = mock_session

        # Prepare 1 active server row for query #1
        active_row = (1, "http://active:8443", "key1", "Active Server", True)
        exec_servers = MagicMock()
        exec_servers.all.return_value = [active_row]

        # Query #2 for profiles
        exec_profiles = MagicMock()
        exec_profiles.all.return_value = []

        mock_session.execute.side_effect = [exec_servers, exec_profiles]

        mock_client_instance = AsyncMock()
        mock_client_instance.get_all_clients.return_value = []
        mock_client_cls.return_value = mock_client_instance

        await _traffic_sync_once()

        # Check the SQL query executed contains is_active filter
        first_call_args = mock_session.execute.call_args_list[0][0]
        stmt_str = str(first_call_args[0]).lower()
        self.assertIn("is_active", stmt_str)
        self.assertIn("true", stmt_str)

        # Ensure AmneziaClient was created ONLY for active server
        mock_client_cls.assert_called_once_with("http://active:8443", "key1")

    @patch("services.workers.cleanup.session_scope")
    @patch("services.workers.cleanup.AmneziaClient")
    async def test_cleanup_dangling_peers_ignores_inactive_servers(self, mock_client_cls, mock_session_scope):
        mock_session = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = session
        session.begin_nested.return_value.__aexit__.return_value = None
        mock_session_scope.return_value.__aenter__.return_value = mock_session

        active_server = SimpleNamespace(id=1, api_url="http://active:8443", api_key="key1", name="Active", is_active=True)

        exec_active = MagicMock()
        exec_active.scalars.return_value.all.return_value = [active_server]

        exec_peers = MagicMock()
        exec_peers.all.return_value = []

        mock_session.execute.side_effect = [exec_active, exec_peers]

        mock_client_instance = AsyncMock()
        mock_client_instance.get_all_clients.return_value = []
        mock_client_cls.return_value = mock_client_instance

        await _cleanup_dangling_peers()

        # Check the first query filtered by is_active == True
        first_call_args = mock_session.execute.call_args_list[0][0]
        stmt_str = str(first_call_args[0]).lower()
        self.assertIn("is_active", stmt_str)
        self.assertIn("true", stmt_str)

        mock_client_cls.assert_called_once_with("http://active:8443", "key1")

    @patch("bot.handlers.admin.servers.card_routes.is_admin", return_value=True)
    @patch("bot.handlers.admin.servers.card_routes.get_server_by_id")
    @patch("bot.handlers.admin.servers.card_routes.update_server")
    @patch("services.workers.node_monitor.reset_server_monitor_state")
    @patch("bot.handlers.admin.servers.card_routes._show_server_card")
    @patch("bot.handlers.admin.servers.card_routes.AuditService")
    async def test_toggle_server_off_cleans_up_circuit_breaker(
        self, mock_audit, mock_show_card, mock_reset_monitor, mock_update_server, mock_get_server, mock_is_admin
    ):
        from bot.handlers.admin.servers.card_routes import toggle_server_apply
        mock_audit.log_action = AsyncMock()

        api_url = "http://disabled-node.test:8443"
        cb = _get_circuit_breaker(api_url)
        cb.state = "OPEN"
        self.assertIn(api_url, _circuit_breakers)

        server = SimpleNamespace(id=5, name="Node 5", api_url=api_url, api_key="key5", is_active=True)
        mock_get_server.return_value = server

        callback = AsyncMock()
        callback.from_user.id = 12345
        callback.data = "admin_server_toggle_apply:5"
        state = AsyncMock()
        session = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = session
        session.begin_nested.return_value.__aexit__.return_value = None

        await toggle_server_apply(callback, state, session)

        # Verify CircuitBreaker for api_url was removed/cleared
        self.assertNotIn(api_url, _circuit_breakers)


if __name__ == "__main__":
    unittest.main()
