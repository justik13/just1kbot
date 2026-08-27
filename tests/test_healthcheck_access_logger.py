import asyncio
import inspect
import logging
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web
from bot.main import HealthcheckAccessLogger
from services.amnezia_client import AmneziaClient


class TestHealthcheckAccessLogger(unittest.TestCase):
    def setUp(self):
        self.logger_mock = MagicMock(spec=logging.Logger)
        self.access_logger = HealthcheckAccessLogger(self.logger_mock, "%s")

    def test_suppresses_health_200_ok(self):
        request = MagicMock(spec=web.Request)
        request.path = "/health"
        response = MagicMock(spec=web.StreamResponse)
        response.status = 200

        with patch("aiohttp.web_log.AccessLogger.log") as mock_super_log:
            self.access_logger.log(request, response, 0.001)
            mock_super_log.assert_not_called()
            self.logger_mock.info.assert_not_called()

    def test_logs_health_500_error(self):
        request = MagicMock(spec=web.Request)
        request.path = "/health"
        response = MagicMock(spec=web.StreamResponse)
        response.status = 500

        with patch("aiohttp.web_log.AccessLogger.log") as mock_super_log:
            self.access_logger.log(request, response, 0.001)
            mock_super_log.assert_called_once_with(request, response, 0.001)

    def test_logs_other_endpoints(self):
        request = MagicMock(spec=web.Request)
        request.path = "/webhook/yookassa"
        response = MagicMock(spec=web.StreamResponse)
        response.status = 200

        with patch("aiohttp.web_log.AccessLogger.log") as mock_super_log:
            self.access_logger.log(request, response, 0.001)
            mock_super_log.assert_called_once_with(request, response, 0.001)


class TestGetServerLoadTimeout(unittest.IsolatedAsyncioTestCase):
    def test_default_timeout_signature(self):
        sig = inspect.signature(AmneziaClient.get_server_load)
        timeout_param = sig.parameters.get("timeout")
        self.assertIsNotNone(timeout_param)
        self.assertEqual(timeout_param.default, 10.0)

    async def test_get_server_load_timeout_handling_logs_error_name(self):
        client = AmneziaClient("http://localhost:3000", "testkey")
        with patch.object(client, "_get_server_load_internal", side_effect=asyncio.TimeoutError()):
            with patch("services.amnezia_client.logger.warning") as mock_warn:
                res = await client.get_server_load(timeout=0.01)
                self.assertIsNone(res)
                mock_warn.assert_called_once()
                self.assertIn("TimeoutError", mock_warn.call_args[0][2])


class TestSlotsCacheAndServerCardSync(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from services.slots_cache import clear_slots_cache
        clear_slots_cache()

    def tearDown(self):
        from services.slots_cache import clear_slots_cache
        clear_slots_cache()

    def test_slots_cache_ttl_is_at_least_1800s(self):
        from services.slots_cache import _slots_cache
        self.assertGreaterEqual(_slots_cache.ttl, 1800)

    async def test_show_server_card_uses_cached_real_count_and_displays_db_discrepancy(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from bot.handlers.admin.servers import common
        from services.slots_cache import update_cached_peer_count

        callback = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock()),
        )
        server = SimpleNamespace(
            id=5,
            name="Poland Node",
            country_flag="🇵🇱",
            is_active=True,
            disabled_reason=None,
            protocol="amneziawg2",
            max_clients=240,
            api_url="https://pl.example.com:8443",
        )
        session = AsyncMock()

        # Set cached real count = 6, DB count = 8
        update_cached_peer_count(5, 6)

        with patch.object(
            common,
            "get_server_peer_counts",
            new=AsyncMock(return_value={5: 8}),
        ):
            await common._show_server_card(callback, session, server)

        rendered = callback.message.edit_text.call_args.args[0]
        self.assertIn("6 / 240", rendered)
        self.assertIn("(в БД: 8)", rendered)

    async def test_show_server_card_shows_clean_slots_when_counts_match(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from bot.handlers.admin.servers import common
        from services.slots_cache import update_cached_peer_count

        callback = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock()),
        )
        server = SimpleNamespace(
            id=5,
            name="Poland Node",
            country_flag="🇵🇱",
            is_active=True,
            disabled_reason=None,
            protocol="amneziawg2",
            max_clients=240,
            api_url="https://pl.example.com:8443",
        )
        session = AsyncMock()

        # Both cached and DB are 6
        update_cached_peer_count(5, 6)

        with patch.object(
            common,
            "get_server_peer_counts",
            new=AsyncMock(return_value={5: 6}),
        ):
            await common._show_server_card(callback, session, server)

    async def test_slots_cache_monotonic_ordering_prevents_stale_overwrite(self):
        from services.slots_cache import get_cached_peer_count, update_cached_peer_count

        # Newer snapshot at t=100 has 8 peers
        update_cached_peer_count(1, 8, timestamp=100.0)
        self.assertEqual(get_cached_peer_count(1), 8)

        # Slower delayed snapshot at t=90 with 5 peers finishes later -> must NOT overwrite newer t=100
        update_cached_peer_count(1, 5, timestamp=90.0)
        self.assertEqual(get_cached_peer_count(1), 8)

        # Even newer snapshot at t=110 with 10 peers -> MUST overwrite
        update_cached_peer_count(1, 10, timestamp=110.0)
        self.assertEqual(get_cached_peer_count(1), 10)

    def test_invalidate_server_cache_removes_entry(self):
        from services.slots_cache import get_cached_peer_count, invalidate_server_cache, update_cached_peer_count

        update_cached_peer_count(2, 15)
        self.assertEqual(get_cached_peer_count(2), 15)

        invalidate_server_cache(2)
        self.assertIsNone(get_cached_peer_count(2))

    async def test_cleanup_worker_preserves_cache_on_api_failure(self):
        from services.slots_cache import get_cached_peer_count, update_cached_peer_count
        from services.workers.cleanup import _cleanup_dangling_peers

        update_cached_peer_count(3, 9)

        # Simulate Server in DB
        server_mock = MagicMock(id=3, name="DE Node", api_url="http://de.node", api_key="k", is_active=True)

        class MockSession:
            async def execute(self, stmt):
                res = MagicMock()
                # If selecting server: return server_mock
                res.scalars.return_value.all.return_value = [server_mock]
                # If selecting vpn profiles: return []
                res.all.return_value = []
                return res

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def mock_session_scope():
            yield MockSession()

        # Simulate API returning None (failure/timeout)
        with (
            patch("services.workers.cleanup.session_scope", mock_session_scope),
            patch("services.workers.cleanup.AmneziaClient.get_all_clients", AsyncMock(return_value=None)),
        ):
            await _cleanup_dangling_peers()

        # Cache must NOT be overwritten with 0!
        self.assertEqual(get_cached_peer_count(3), 9)


if __name__ == "__main__":
    unittest.main()
