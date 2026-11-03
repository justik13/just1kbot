import logging
import unittest
from unittest.mock import MagicMock, patch

from aiohttp import web
from bot.main import HealthcheckAccessLogger


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

    def test_suppresses_sub_ping_200_ok(self):
        request = MagicMock(spec=web.Request)
        request.path = "/sub/wl/ping"
        response = MagicMock(spec=web.StreamResponse)
        response.status = 200

        with patch("aiohttp.web_log.AccessLogger.log") as mock_super_log:
            self.access_logger.log(request, response, 0.001)
            mock_super_log.assert_not_called()
            self.logger_mock.info.assert_not_called()
            self.logger_mock.debug.assert_not_called()
            self.logger_mock.warning.assert_not_called()

    def test_logs_sub_ping_502_warning(self):
        request = MagicMock(spec=web.Request)
        request.path = "/sub/wl/ping"
        request.method = "GET"
        request.remote = "172.19.0.3"
        request.version = MagicMock(major=1, minor=1)
        request.headers = {"X-Real-IP": "1.2.3.4", "User-Agent": "Just1kBot-Monitor"}
        response = MagicMock(spec=web.StreamResponse)
        response.status = 502
        response.body_length = 0

        self.access_logger.log(request, response, 0.001)
        self.logger_mock.warning.assert_called_once()
        log_text = self.logger_mock.warning.call_args[0][0] % self.logger_mock.warning.call_args[0][1:]
        self.assertIn("1.2.3.4", log_text)
        self.assertIn("/sub/wl/ping", log_text)
        self.assertIn("Just1kBot-Monitor", log_text)

    def test_logs_sub_wl_200_at_debug(self):
        request = MagicMock(spec=web.Request)
        request.path = "/sub/wl/secret_token_1234567890"
        request.method = "GET"
        request.remote = "172.19.0.3"
        request.version = MagicMock(major=1, minor=1)
        request.headers = {"X-Real-IP": "5.6.7.8", "User-Agent": "INCY/1.2"}
        response = MagicMock(spec=web.StreamResponse)
        response.status = 200
        response.body_length = 223

        self.access_logger.log(request, response, 0.001)
        self.logger_mock.debug.assert_called_once()
        self.logger_mock.info.assert_not_called()
        self.logger_mock.warning.assert_not_called()
        log_text = self.logger_mock.debug.call_args[0][0] % self.logger_mock.debug.call_args[0][1:]
        self.assertIn("5.6.7.8", log_text)
        self.assertIn("/sub/wl/***", log_text)
        self.assertNotIn("secret_token_1234567890", log_text)
        self.assertIn("INCY/1.2", log_text)

    def test_logs_sub_wl_403_at_warning(self):
        request = MagicMock(spec=web.Request)
        request.path = "/sub/wl/secret_token_1234567890"
        request.method = "GET"
        request.remote = "172.19.0.3"
        request.version = MagicMock(major=1, minor=1)
        request.headers = {"X-Real-IP": "5.6.7.8", "User-Agent": "INCY/2.0"}
        response = MagicMock(spec=web.StreamResponse)
        response.status = 403
        response.body_length = 40

        self.access_logger.log(request, response, 0.001)
        self.logger_mock.warning.assert_called_once()
        self.logger_mock.info.assert_not_called()
        self.logger_mock.debug.assert_not_called()
        log_text = self.logger_mock.warning.call_args[0][0] % self.logger_mock.warning.call_args[0][1:]
        self.assertIn("5.6.7.8", log_text)
        self.assertIn("/sub/wl/***", log_text)
        self.assertNotIn("secret_token_1234567890", log_text)
        self.assertIn("INCY/2.0", log_text)


if __name__ == "__main__":
    unittest.main()
