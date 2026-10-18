import io
import logging
import unittest

from utils.correlation import (
    CorrelationFilter,
    correlation_scope,
    get_current_request_id,
    reset_request_id,
    set_request_id,
)
import bot.middlewares.correlation as reexported


class CorrelationLifecycleTests(unittest.TestCase):
    def test_default_request_id(self):
        self.assertEqual(get_current_request_id(), "system")

    def test_set_and_reset_request_id(self):
        token = set_request_id("req-test-1")
        try:
            self.assertEqual(get_current_request_id(), "req-test-1")
        finally:
            reset_request_id(token)
        self.assertEqual(get_current_request_id(), "system")

    def test_correlation_scope_basic(self):
        with correlation_scope("scope-1") as rid:
            self.assertEqual(rid, "scope-1")
            self.assertEqual(get_current_request_id(), "scope-1")
        self.assertEqual(get_current_request_id(), "system")

    def test_correlation_scope_nested_restores_outer(self):
        with correlation_scope("outer-123"):
            self.assertEqual(get_current_request_id(), "outer-123")
            with correlation_scope("inner-456"):
                self.assertEqual(get_current_request_id(), "inner-456")
            self.assertEqual(get_current_request_id(), "outer-123")
        self.assertEqual(get_current_request_id(), "system")

    def test_correlation_scope_resets_on_exception(self):
        try:
            with correlation_scope("exceptional-scope"):
                self.assertEqual(get_current_request_id(), "exceptional-scope")
                raise RuntimeError("simulated error")
        except RuntimeError:
            pass
        self.assertEqual(get_current_request_id(), "system")

    def test_correlation_filter_injects_record_attribute(self):
        log_filter = CorrelationFilter()
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("[%(request_id)s] %(message)s"))
        handler.addFilter(log_filter)

        logger = logging.getLogger(f"test_logger_{id(stream)}")
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)

        logger.info("message without scope")
        with correlation_scope("corr-filter-test"):
            logger.info("message with scope")
        logger.info("message after scope")

        output = stream.getvalue().splitlines()
        self.assertEqual(len(output), 3)
        self.assertEqual(output[0], "[system] message without scope")
        self.assertEqual(output[1], "[corr-filter-test] message with scope")
        self.assertEqual(output[2], "[system] message after scope")

    def test_reexported_module_has_identical_objects(self):
        self.assertIs(reexported.correlation_scope, correlation_scope)
        self.assertIs(reexported.get_current_request_id, get_current_request_id)
        self.assertIs(reexported.set_request_id, set_request_id)
        self.assertIs(reexported.reset_request_id, reset_request_id)
        self.assertIs(reexported.CorrelationFilter, CorrelationFilter)
