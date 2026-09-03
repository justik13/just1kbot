from contextlib import asynccontextmanager
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from config.constants import AMNEZIA_PROTOCOL, XRAY_PROTOCOL
from database.models import Server


class StrictProtocolDecouplingTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_available_servers_strictly_filters_amnezia_protocol(self):
        from database.repositories.servers_repo import get_available_servers

        s_awg = Server(
            id=1,
            name="AWG Node",
            protocol=AMNEZIA_PROTOCOL,
            is_active=True,
            max_clients=100,
            capabilities=[],
        )
        s_xray = Server(
            id=2,
            name="Xray Origin",
            protocol=XRAY_PROTOCOL,
            is_active=True,
            max_clients=100,
            capabilities=["xray_origin"],
        )
        s_unknown = Server(
            id=3,
            name="Unknown Node",
            protocol="wireguard_legacy",
            is_active=True,
            max_clients=100,
            capabilities=[],
        )

        session = AsyncMock()
        with patch("database.repositories.servers_repo.get_active_servers", return_value=[s_awg, s_xray, s_unknown]), \
             patch("database.repositories.servers_repo.get_server_peer_counts", return_value={1: 10, 2: 5, 3: 0}), \
             patch("database.repositories.servers_repo.get_cached_peer_count", return_value=10):

            available = await get_available_servers(session)
            self.assertEqual(len(available), 1)
            self.assertEqual(available[0].id, 1)
            self.assertEqual(available[0].protocol, AMNEZIA_PROTOCOL)

    async def test_node_monitor_never_invokes_amnezia_client_on_xray_node(self):
        from services.workers.node_monitor import check_node_resources_and_alerts

        xray_server = MagicMock(spec=Server)
        xray_server.id = 5
        xray_server.name = "Xray Origin Node"
        xray_server.protocol = XRAY_PROTOCOL
        xray_server.capabilities = ["xray_origin"]
        xray_server.api_url = "https://origin.just1k.best:8444"
        xray_server.api_key = "test_key"
        xray_server.is_active = True
        xray_server.health_state = "ONLINE"
        xray_server.disabled_reason = None
        xray_server.consecutive_fails = 0
        xray_server.consecutive_successes = 0
        xray_server.problem_started_at = None
        xray_server.next_check_at = None
        xray_server.recovery_notice_sent = False

        mock_bot = AsyncMock()
        settings_mock = MagicMock()
        settings_mock.ADMIN_IDS = [100]

        dummy_session = AsyncMock()

        @asynccontextmanager
        async def dummy_scope():
            yield dummy_session

        with patch("services.workers.node_monitor.get_settings", return_value=settings_mock), \
             patch("config.settings.get_settings", return_value=settings_mock), \
             patch("services.workers.node_monitor.session_scope", side_effect=dummy_scope), \
             patch("services.workers.node_monitor.update_server_health_snapshot", return_value=(xray_server, True)), \
             patch("services.workers.node_monitor.update_server_xray_epoch_cas", return_value=(True, xray_server)), \
             patch("services.workers.node_monitor.get_all_servers", return_value=[xray_server]), \
             patch("services.workers.node_monitor.get_server_by_id", return_value=xray_server), \
             patch("services.workers.node_monitor.update_server"), \
             patch("services.workers.node_monitor.AmneziaClient") as mock_amnezia_cls, \
             patch("services.xray_node_client.XrayNodeClient.check_health", return_value=(True, "epoch-1", {"boot_id": "b1"})) as mock_xray_health:

            await check_node_resources_and_alerts(mock_bot)

            # AmneziaClient must NEVER be called on an Xray node
            mock_amnezia_cls.assert_not_called()
            mock_xray_health.assert_called_once()

    async def test_slots_cache_never_invokes_amnezia_client_on_xray_node(self):
        from services.slots_cache import get_real_peer_count, capture_server_peer_snapshot

        xray_server = Server(
            id=20,
            name="Xray Node",
            protocol="xray",
            capabilities=["xray_origin"],
            api_url="https://origin:8444",
            api_key="secret",
            max_clients=500,
        )

        with patch("services.slots_cache.AmneziaClient") as mock_amnezia_cls, \
             patch("database.connection.session_scope") as mock_scope:
            mock_session = AsyncMock()
            mock_scalar = MagicMock()
            mock_scalar.scalar.return_value = 42
            mock_session.execute.return_value = mock_scalar
            mock_session.get.return_value = xray_server
            mock_scope.return_value.__aenter__.return_value = mock_session

            # 1. get_real_peer_count
            count = await get_real_peer_count(xray_server, force_refresh=True)
            self.assertEqual(count, 42)
            mock_amnezia_cls.assert_not_called()

            # 2. capture_server_peer_snapshot
            snapshot = await capture_server_peer_snapshot(20)
            self.assertEqual(snapshot.server_id, 20)
            self.assertEqual(snapshot.peer_ids, frozenset())
            mock_amnezia_cls.assert_not_called()

    async def test_peers_routes_blocks_xray_servers(self):
        from bot.handlers.admin.servers.peers_routes import show_server_peers
        from bot.texts.admin.servers import ADMIN_SERVER_PEERS_AWG_ONLY

        callback = AsyncMock()
        callback.data = "admin_server_peers:30:1"
        callback.from_user.id = 12345

        xray_server = Server(
            id=30,
            name="Xray Origin",
            protocol="xray",
            capabilities=["xray_origin"],
        )

        session = AsyncMock()
        session.get.return_value = xray_server

        with patch("bot.handlers.admin.servers.peers_routes.is_admin", return_value=True), \
             patch("services.amnezia_client.AmneziaClient") as mock_amnezia_cls:

            await show_server_peers(callback, session)

            callback.answer.assert_called_once_with(
                ADMIN_SERVER_PEERS_AWG_ONLY,
                show_alert=True,
            )
            mock_amnezia_cls.assert_not_called()

    async def test_api_operations_executor_refuses_xray_server(self):
        from services.api_operations_executor import _client
        from types import SimpleNamespace

        op = SimpleNamespace(
            server_id=40,
            api_url_snapshot="https://origin:8444",
            api_key_snapshot="key",
        )
        xray_server = Server(
            id=40,
            name="Xray Origin",
            protocol="xray",
        )

        dummy_session = AsyncMock()
        dummy_session.get.return_value = xray_server

        @asynccontextmanager
        async def dummy_scope():
            yield dummy_session

        with patch("services.api_operations_executor.session_scope", side_effect=dummy_scope), \
             patch("services.api_operations_executor.AmneziaClient") as mock_amnezia_cls:

            client = await _client(op)
            self.assertIsNone(client)
            mock_amnezia_cls.assert_not_called()
