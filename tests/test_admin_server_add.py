from contextlib import asynccontextmanager
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from database.models import Server
from bot.handlers.admin.servers.add_routes import process_add_server


class TestAdminServerAdd(unittest.IsolatedAsyncioTestCase):
    """Test server addition flow in admin handlers."""

    async def test_awg_server_name_preserves_admin_input(self):
        """Verify that user-provided name has precedence over server_info.name."""
        msg = MagicMock()
        msg.from_user = MagicMock(id=100)
        msg.text = "secret-key-12345"
        msg.bot = AsyncMock()
        msg.chat.id = 100
        msg.message_id = 10

        msg.delete = AsyncMock()

        state = AsyncMock()
        state.get_data = AsyncMock(
            return_value={
                "step": "api_key",
                "protocol": "amneziawg2",
                "name": "Польша",
                "country_flag": "🇵🇱",
                "api_url": "https://pl.example.com:8443",
            }
        )
        session = AsyncMock()

        # Mock AmneziaClient
        mock_server_info = MagicMock()
        mock_server_info.name = "amnezia-awg2"
        mock_server_info.protocols = ["amneziawg2"]
        mock_server_info.get_effective_max_peers.return_value = 200
        mock_server_info.get_protocol.return_value = "amneziawg2"
        mock_server_info.SERVER_MAX_PEERS = 250

        mock_client = AsyncMock()
        mock_client.healthcheck = AsyncMock(return_value=True)
        mock_client.get_server_info = AsyncMock(return_value=mock_server_info)

        created_server = Server(
            id=6,
            name="Польша",
            country_flag="🇵🇱",
            api_url="https://pl.example.com:8443",
            protocol="amneziawg2",
            max_clients=200,
        )

        with (
            patch("bot.handlers.admin.servers.add_routes.is_admin", return_value=True),
            patch("bot.handlers.admin.servers.add_routes.render_hub", new_callable=AsyncMock) as mock_render,
            patch("bot.handlers.admin.servers.add_routes.AmneziaClient", return_value=mock_client),
            patch("bot.handlers.admin.servers.add_routes.get_server_by_api_url", new_callable=AsyncMock, return_value=None),
            patch("bot.handlers.admin.servers.add_routes.create_server", new_callable=AsyncMock, return_value=created_server) as mock_create_server,
            patch("bot.handlers.admin.servers.add_routes.AuditService.log_action", new_callable=AsyncMock),
        ):
            await process_add_server(msg, state, session)

            mock_create_server.assert_called_once()
            _, kwargs = mock_create_server.call_args
            # The name passed to create_server MUST be "Польша", NOT "amnezia-awg2"
            self.assertEqual(kwargs["name"], "Польша")
            self.assertEqual(kwargs["country_flag"], "🇵🇱")
            self.assertEqual(kwargs["protocol"], "amneziawg2")

            state.clear.assert_called_once()
            self.assertEqual(mock_render.call_count, 2)

    async def test_awg_server_name_fallback_to_server_info_when_empty(self):
        """If admin input name is empty for any reason, fallback to server_info.name."""
        msg = MagicMock()
        msg.from_user = MagicMock(id=100)
        msg.text = "secret-key-12345"
        msg.bot = AsyncMock()
        msg.chat.id = 100
        msg.message_id = 10

        msg.delete = AsyncMock()

        state = AsyncMock()
        state.get_data = AsyncMock(
            return_value={
                "step": "api_key",
                "protocol": "amneziawg2",
                "name": "",
                "country_flag": "🇵🇱",
                "api_url": "https://pl.example.com:8443",
            }
        )
        session = AsyncMock()

        mock_server_info = MagicMock()
        mock_server_info.name = "amnezia-awg2"
        mock_server_info.protocols = ["amneziawg2"]
        mock_server_info.get_effective_max_peers.return_value = 200
        mock_server_info.get_protocol.return_value = "amneziawg2"
        mock_server_info.SERVER_MAX_PEERS = 250

        mock_client = AsyncMock()
        mock_client.healthcheck = AsyncMock(return_value=True)
        mock_client.get_server_info = AsyncMock(return_value=mock_server_info)

        created_server = Server(
            id=6,
            name="amnezia-awg2",
            country_flag="🇵🇱",
            api_url="https://pl.example.com:8443",
            protocol="amneziawg2",
            max_clients=200,
        )

        with (
            patch("bot.handlers.admin.servers.add_routes.is_admin", return_value=True),
            patch("bot.handlers.admin.servers.add_routes.render_hub", new_callable=AsyncMock) as mock_render,
            patch("bot.handlers.admin.servers.add_routes.AmneziaClient", return_value=mock_client),
            patch("bot.handlers.admin.servers.add_routes.get_server_by_api_url", new_callable=AsyncMock, return_value=None),
            patch("bot.handlers.admin.servers.add_routes.create_server", new_callable=AsyncMock, return_value=created_server) as mock_create_server,
            patch("bot.handlers.admin.servers.add_routes.AuditService.log_action", new_callable=AsyncMock),
        ):
            await process_add_server(msg, state, session)

            mock_create_server.assert_called_once()
            _, kwargs = mock_create_server.call_args
            self.assertEqual(kwargs["name"], "amnezia-awg2")
            self.assertEqual(mock_render.call_count, 2)

    async def test_awg_capacity_retains_reported_max_peers(self):
        """When API reports maxPeers (e.g. 253), it must be preserved and not clamped to 200."""
        msg = MagicMock()
        msg.from_user = MagicMock(id=100)
        msg.text = "secret-key-12345"
        msg.bot = AsyncMock()
        msg.chat.id = 100
        msg.message_id = 10
        msg.delete = AsyncMock()

        state = AsyncMock()
        state.get_data = AsyncMock(
            return_value={
                "step": "api_key",
                "protocol": "amneziawg2",
                "name": "Польша",
                "country_flag": "🇵🇱",
                "api_url": "https://pl.example.com:8443",
            }
        )
        session = AsyncMock()

        mock_server_info = MagicMock()
        mock_server_info.name = ""
        mock_server_info.protocols = ["amneziawg2"]
        mock_server_info.maxPeers = 253
        mock_server_info.serverMaxPeers = 253
        mock_server_info.SERVER_MAX_PEERS = 253
        mock_server_info.get_effective_max_peers.return_value = 253
        mock_server_info.get_protocol.return_value = "amneziawg2"

        mock_client = AsyncMock()
        mock_client.healthcheck = AsyncMock(return_value=True)
        mock_client.get_server_info = AsyncMock(return_value=mock_server_info)

        created_server = Server(
            id=6,
            name="Польша",
            country_flag="🇵🇱",
            api_url="https://pl.example.com:8443",
            protocol="amneziawg2",
            max_clients=253,
        )

        with (
            patch("bot.handlers.admin.servers.add_routes.is_admin", return_value=True),
            patch("bot.handlers.admin.servers.add_routes.render_hub", new_callable=AsyncMock),
            patch("bot.handlers.admin.servers.add_routes.AmneziaClient", return_value=mock_client),
            patch("bot.handlers.admin.servers.add_routes.get_server_by_api_url", new_callable=AsyncMock, return_value=None),
            patch("bot.handlers.admin.servers.add_routes.create_server", new_callable=AsyncMock, return_value=created_server) as mock_create_server,
            patch("bot.handlers.admin.servers.add_routes.AuditService.log_action", new_callable=AsyncMock),
        ):
            await process_add_server(msg, state, session)

            mock_create_server.assert_called_once()
            _, kwargs = mock_create_server.call_args
            self.assertEqual(kwargs["max_clients"], 253)

    async def test_awg_capacity_fallback_when_unreported(self):
        """When API reports 0/None for maxPeers and serverMaxPeers, fallback to 200."""
        msg = MagicMock()
        msg.from_user = MagicMock(id=100)
        msg.text = "secret-key-12345"
        msg.bot = AsyncMock()
        msg.chat.id = 100
        msg.message_id = 10
        msg.delete = AsyncMock()

        state = AsyncMock()
        state.get_data = AsyncMock(
            return_value={
                "step": "api_key",
                "protocol": "amneziawg2",
                "name": "Польша",
                "country_flag": "🇵🇱",
                "api_url": "https://pl.example.com:8443",
            }
        )
        session = AsyncMock()

        mock_server_info = MagicMock()
        mock_server_info.name = ""
        mock_server_info.protocols = ["amneziawg2"]
        mock_server_info.maxPeers = 0
        mock_server_info.serverMaxPeers = 0
        mock_server_info.SERVER_MAX_PEERS = 250
        mock_server_info.get_effective_max_peers.return_value = 250
        mock_server_info.get_protocol.return_value = "amneziawg2"

        mock_client = AsyncMock()
        mock_client.healthcheck = AsyncMock(return_value=True)
        mock_client.get_server_info = AsyncMock(return_value=mock_server_info)

        created_server = Server(
            id=6,
            name="Польша",
            country_flag="🇵🇱",
            api_url="https://pl.example.com:8443",
            protocol="amneziawg2",
            max_clients=200,
        )

        with (
            patch("bot.handlers.admin.servers.add_routes.is_admin", return_value=True),
            patch("bot.handlers.admin.servers.add_routes.render_hub", new_callable=AsyncMock),
            patch("bot.handlers.admin.servers.add_routes.AmneziaClient", return_value=mock_client),
            patch("bot.handlers.admin.servers.add_routes.get_server_by_api_url", new_callable=AsyncMock, return_value=None),
            patch("bot.handlers.admin.servers.add_routes.create_server", new_callable=AsyncMock, return_value=created_server) as mock_create_server,
            patch("bot.handlers.admin.servers.add_routes.AuditService.log_action", new_callable=AsyncMock),
        ):
            await process_add_server(msg, state, session)

            mock_create_server.assert_called_once()
            _, kwargs = mock_create_server.call_args
            self.assertEqual(kwargs["max_clients"], 200)

    async def test_xray_server_duplicate_url_blocked(self):
        """Creating an Xray server with duplicate api_url renders error and does not create server."""
        from bot.handlers.admin.servers.add_routes import process_add_server

        msg = MagicMock()
        msg.from_user = MagicMock(id=100)
        msg.text = "secret-xray-key"
        msg.bot = AsyncMock()
        msg.chat.id = 100
        msg.message_id = 10
        msg.delete = AsyncMock()

        state = AsyncMock()
        state.get_data = AsyncMock(
            return_value={
                "step": "api_key",
                "protocol": "xray",
                "name": "Origin",
                "country_flag": "🇷🇺",
                "api_url": "https://xray.example.com:8443",
            }
        )
        session = AsyncMock()
        existing_srv = Server(id=99, api_url="https://xray.example.com:8443", name="Existing")

        mock_xray = AsyncMock()
        mock_xray.check_health = AsyncMock(return_value=(True, "epoch_1", {"services": ["white_internet"]}))

        @asynccontextmanager
        async def fake_xray_client(*args, **kwargs):
            yield mock_xray

        with (
            patch("bot.handlers.admin.servers.add_routes.is_admin", return_value=True),
            patch("bot.handlers.admin.servers.add_routes.render_hub", new_callable=AsyncMock) as mock_render,
            patch("services.xray_node_client.XrayNodeClient", fake_xray_client),
            patch("bot.handlers.admin.servers.add_routes.get_server_by_api_url", new_callable=AsyncMock, return_value=existing_srv),
            patch("bot.handlers.admin.servers.add_routes.create_server", new_callable=AsyncMock) as mock_create,
        ):
            await process_add_server(msg, state, session)
            mock_create.assert_not_called()
            state.clear.assert_called_once()
            # Must render duplicate url error
            self.assertTrue(any("уже зарегистрирован" in str(call.args[2]) for call in mock_render.call_args_list))

    async def test_awg_server_preserves_vless_capability_when_xray_check_fails(self):
        """Updating an existing server with 'vless' preserves 'vless' capability if Xray check throws/fails."""
        from bot.handlers.admin.servers.add_routes import process_add_server

        msg = MagicMock()
        msg.from_user = MagicMock(id=100)
        msg.text = "secret-key"
        msg.bot = AsyncMock()
        msg.chat.id = 100
        msg.message_id = 10
        msg.delete = AsyncMock()

        state = AsyncMock()
        state.get_data = AsyncMock(
            return_value={
                "step": "api_key",
                "protocol": "amneziawg2",
                "existing_server_id": 42,
                "name": "Mixed Node",
                "country_flag": "🇩🇪",
                "api_url": "https://mixed.example.com:8443",
            }
        )
        server = Server(
            id=42,
            name="Mixed Node",
            protocol="awg",
            capabilities=["awg", "vless"],
            api_url="https://mixed.example.com:8443",
            api_key="old-key",
        )
        session = AsyncMock()
        session.get = AsyncMock(return_value=server)

        mock_server_info = MagicMock()
        mock_server_info.name = "Mixed Node"
        mock_server_info.protocols = ["amneziawg2"]
        mock_server_info.get_effective_max_peers.return_value = 100
        mock_server_info.get_protocol.return_value = "amneziawg2"
        mock_server_info.SERVER_MAX_PEERS = 100

        mock_client = AsyncMock()
        mock_client.healthcheck = AsyncMock(return_value=True)
        mock_client.get_server_info = AsyncMock(return_value=mock_server_info)

        mock_xray = AsyncMock()
        # Xray API check times out or fails
        mock_xray.check_health = AsyncMock(side_effect=TimeoutError("Xray timed out"))

        @asynccontextmanager
        async def fake_xray_client(*args, **kwargs):
            yield mock_xray

        with (
            patch("bot.handlers.admin.servers.add_routes.is_admin", return_value=True),
            patch("bot.handlers.admin.servers.add_routes.render_hub", new_callable=AsyncMock),
            patch("bot.handlers.admin.servers.add_routes.AmneziaClient", return_value=mock_client),
            patch("bot.handlers.admin.servers.add_routes.get_server_by_api_url", new_callable=AsyncMock, return_value=server),
            patch("services.xray_node_client.XrayNodeClient", fake_xray_client),
            patch("bot.handlers.admin.servers.add_routes.AuditService.log_action", new_callable=AsyncMock),
        ):
            await process_add_server(msg, state, session)
            # vless capability must NOT be dropped!
            self.assertIn("vless", server.capabilities)
            self.assertIn("awg", server.capabilities)
