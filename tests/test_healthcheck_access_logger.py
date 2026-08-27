import asyncio
import inspect
import logging
import unittest
from unittest.mock import MagicMock, patch

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


if __name__ == "__main__":
    unittest.main()
