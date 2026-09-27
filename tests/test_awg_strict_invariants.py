import os
import unittest
from unittest.mock import AsyncMock, patch

from config.constants import AMNEZIA_PROTOCOL, AMNEZIA_PROTOCOLS
from database.models import Server
from database.repositories.servers_repo import is_server_allocatable
from services.amnezia_client import AmneziaClient
from utils.vpn_helpers import _get_awg_block


class AWGStrictInvariantsTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.env_patcher = patch.dict(
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
                "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/db",
            },
        )
        cls.env_patcher.start()

    @classmethod
    def tearDownClass(cls):
        cls.env_patcher.stop()

    def test_amnezia_protocols_set_strict_awg2_plus(self):
        """Invariant: AMNEZIA_PROTOCOLS strictly contains modern AWG 2.0+ (no legacy awg/amneziawg/wg)."""
        from config.constants import AMNEZIA_DOCKER_CONTAINER

        self.assertEqual(AMNEZIA_DOCKER_CONTAINER, "amnezia-awg2")
        self.assertEqual(AMNEZIA_PROTOCOL, "amneziawg2")
        self.assertEqual(set(AMNEZIA_PROTOCOLS), {"amneziawg2", "amneziawg3", "amneziawg3.1"})
        self.assertNotIn("awg", AMNEZIA_PROTOCOLS)
        self.assertNotIn("amneziawg", AMNEZIA_PROTOCOLS)
        self.assertNotIn("wg", AMNEZIA_PROTOCOLS)
        self.assertNotIn("wireguard", AMNEZIA_PROTOCOLS)

    def test_servers_repo_can_fulfill_protocol_strict_awg(self):
        """Invariant: Any protocol in AMNEZIA_PROTOCOLS is rejected on xray_origin servers."""
        server = Server(
            id=1,
            name="test-xray-server",
            api_url="http://1.2.3.4:8080",
            api_key="key",
            is_active=True,
            protocol="xray",
            capabilities=["xray_origin"],
        )
        for proto in AMNEZIA_PROTOCOLS:
            self.assertFalse(is_server_allocatable(server, proto))

    def test_vpn_helpers_get_awg_block_strict_amnezia_awg2_container(self):
        """Invariant: Only canonical amnezia-awg2 container is accepted; legacy or fictitious are rejected."""
        valid_data = {
            "containers": [
                {"container": "amnezia-awg2", "awg": {"protocol_version": "3.1", "port": 51820}},
            ]
        }
        self.assertIsNotNone(_get_awg_block(valid_data))
        self.assertEqual(_get_awg_block(valid_data)["protocol_version"], "3.1")

        # Rejected legacy container
        legacy_data = {
            "containers": [
                {"container": "amnezia-awg", "awg": {"protocol_version": "2", "port": 51820}},
            ]
        }
        self.assertIsNone(_get_awg_block(legacy_data))

        # Rejected fictitious container
        awg3_data = {
            "containers": [
                {"container": "amnezia-awg3", "awg": {"protocol_version": "3.1", "port": 51820}},
            ]
        }
        self.assertIsNone(_get_awg_block(awg3_data))

    async def test_amnezia_client_protocol_propagation(self):
        """Invariant: AmneziaClient propagates server protocol to API requests."""
        client = AmneziaClient("http://127.0.0.1:8080", "test-key", protocol="amneziawg3")
        self.assertEqual(client.protocol, "amneziawg3")

        # Mock _request_result to inspect outgoing JSON payload
        mock_result = AsyncMock()
        mock_result.ok = True
        mock_result.status_code = 200
        mock_result.value = {"client": {"id": "peer-1", "config": "[Interface]\nAddress = 10.8.1.2/32\nPrivateKey = priv=\nJc = 4\nJmin = 10\nJmax = 50\nS1 = 15\nS2 = 20\nS3 = 25\nS4 = 30\nH1 = 100\nH2 = 200\nH3 = 300\nH4 = 400\n[Peer]\nPublicKey = pub=\nEndpoint = 1.2.3.4:51820\nAllowedIPs = 0.0.0.0/0\n"}}

        with patch.object(client, "_request_result", return_value=mock_result) as mock_req:
            # 1. create_user_result uses client.protocol when not explicitly given
            await client.create_user_result("user_1")
            call_kwargs = mock_req.call_args.kwargs
            self.assertEqual(call_kwargs["json"]["protocol"], "amneziawg3")

            # 2. create_user_result overrides with explicit protocol
            await client.create_user_result("user_1", protocol="amneziawg3.1")
            call_kwargs = mock_req.call_args.kwargs
            self.assertEqual(call_kwargs["json"]["protocol"], "amneziawg3.1")

            # 3. delete_user_result uses client.protocol
            await client.delete_user_result("peer-1")
            call_kwargs = mock_req.call_args.kwargs
            self.assertEqual(call_kwargs["json"]["protocol"], "amneziawg3")

            # 4. update_client_result uses client.protocol
            await client.update_client_result("peer-1", status="active")
            call_kwargs = mock_req.call_args.kwargs
            self.assertEqual(call_kwargs["json"]["protocol"], "amneziawg3")

    async def test_build_client_for_operation_injects_server_protocol(self):
        """Invariant: _client propagates Server.protocol to AmneziaClient."""
        from types import SimpleNamespace
        from services.api_operations_executor import _client

        op = SimpleNamespace(
            server_id=42,
            api_url_snapshot="http://node.local:8080",
            api_key_snapshot="testkey",
        )
        fake_server = Server(
            id=42,
            name="awg3-node",
            api_url="http://node.local:8080",
            api_key="testkey",
            protocol="amneziawg3",
            is_active=True,
        )

        mock_session = AsyncMock()
        mock_session.get.return_value = fake_server

        with patch("services.api_operations_executor.session_scope") as mock_scope:
            mock_scope.return_value.__aenter__.return_value = mock_session
            client = await _client(op)

        self.assertIsNotNone(client)
        self.assertEqual(client.protocol, "amneziawg3")


if __name__ == "__main__":
    unittest.main()
