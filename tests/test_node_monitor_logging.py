import asyncio
import logging
import unittest

import aiohttp

from services.workers import _ExpectedNodeMonitorNetworkWarningFilter


class NodeMonitorLoggingTests(unittest.TestCase):
    def test_expected_healthcheck_network_warning_is_filtered(self):
        record = logging.LogRecord(
            name="services.workers.node_monitor",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="Healthcheck exception for server %s (%s): %s",
            args=(1, "node-1", aiohttp.ClientConnectionError()),
            exc_info=None,
        )
        self.assertFalse(_ExpectedNodeMonitorNetworkWarningFilter().filter(record))

    def test_expected_timeout_warning_is_filtered(self):
        record = logging.LogRecord(
            name="services.workers.node_monitor",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="Healthcheck exception for server %s (%s): %s",
            args=(1, "node-1", asyncio.TimeoutError()),
            exc_info=None,
        )
        self.assertFalse(_ExpectedNodeMonitorNetworkWarningFilter().filter(record))

    def test_unexpected_exception_is_not_filtered(self):
        record = logging.LogRecord(
            name="services.workers.node_monitor",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="Healthcheck exception for server %s (%s): %s",
            args=(1, "node-1", RuntimeError("programming bug")),
            exc_info=None,
        )
        self.assertTrue(_ExpectedNodeMonitorNetworkWarningFilter().filter(record))

    def test_unrelated_warning_is_not_filtered(self):
        record = logging.LogRecord(
            name="services.workers.node_monitor",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="Important unexpected warning: %s",
            args=("details",),
            exc_info=None,
        )
        self.assertTrue(_ExpectedNodeMonitorNetworkWarningFilter().filter(record))


if __name__ == "__main__":
    unittest.main()
