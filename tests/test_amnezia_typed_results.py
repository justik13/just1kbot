import asyncio
import logging
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

from services import amnezia_client as module
from services.amnezia_client import (
    AmneziaAPIResult,
    AmneziaClient,
    AmneziaClientCreateResponse,
    AmneziaErrorKind,
    RequestSemantics,
)


class FakeResponse:
    def __init__(self, status, payload=None, json_error=None):
        self.status = status
        self.payload = payload
        self.json_error = json_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        if self.json_error:
            raise self.json_error
        return self.payload


class FakeRequestContext:
    def __init__(self, outcome):
        self.outcome = outcome

    async def __aenter__(self):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.request = MagicMock(side_effect=self._request)

    def _request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return FakeRequestContext(self.outcomes.pop(0))


class AmneziaTypedResultTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        module._http_session = None
        module._circuit_breakers.clear()
        module._rate_limiters.clear()
        self.client = AmneziaClient("https://vpn.example", "test-key")
        self.sleep = patch.object(module.asyncio, "sleep", new=AsyncMock())
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def use_session(self, *outcomes):
        session = FakeSession(*outcomes)
        getter = patch.object(module, "get_http_session", new=AsyncMock(return_value=session))
        getter.start()
        self.addCleanup(getter.stop)
        return session

    async def test_create_timeout_is_not_retried(self):
        session = self.use_session(asyncio.TimeoutError())
        result = await self.client.create_user_result("alice")
        self.assertEqual(session.request.call_count, 1)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, AmneziaErrorKind.TIMEOUT)
        self.assertTrue(result.ambiguous)
        self.assertFalse(result.retryable)

    async def test_create_500_is_not_retried(self):
        session = self.use_session(FakeResponse(500))
        result = await self.client.create_user_result("alice")
        self.assertEqual(session.request.call_count, 1)
        self.assertEqual(result.error_kind, AmneziaErrorKind.SERVER_ERROR)
        self.assertTrue(result.ambiguous)
        self.assertFalse(result.retryable)

    async def test_create_invalid_success_response_is_ambiguous(self):
        session = self.use_session(FakeResponse(200, {"unexpected": True}))
        result = await self.client.create_user_result("alice")
        self.assertEqual(session.request.call_count, 1)
        self.assertEqual(result.error_kind, AmneziaErrorKind.INVALID_RESPONSE)
        self.assertTrue(result.ambiguous)
        self.assertFalse(result.retryable)

    async def test_read_retries_timeout(self):
        session = self.use_session(
            asyncio.TimeoutError(), FakeResponse(200, {"name": "server"})
        )
        result = await self.client._request_result(
            "GET", "/server", semantics=RequestSemantics.READ
        )
        self.assertEqual(session.request.call_count, 2)
        self.assertTrue(result.ok)
        self.assertEqual(result.value, {"name": "server"})

    async def test_patch_retries_same_payload_after_503(self):
        session = self.use_session(FakeResponse(503), FakeResponse(204))
        result = await self.client.update_client_result("peer-1", status="disabled")
        self.assertTrue(result.ok)
        self.assertEqual(session.request.call_count, 2)
        self.assertEqual(session.calls[0][2]["json"], session.calls[1][2]["json"])

    async def test_delete_404_is_success(self):
        self.use_session(FakeResponse(404))
        result = await self.client.delete_user_result("missing")
        self.assertTrue(result.ok)
        self.assertIsNone(result.error_kind)

    async def test_auth_errors_do_not_increment_breaker(self):
        for status in (401, 403):
            with self.subTest(status=status):
                session = self.use_session(FakeResponse(status))
                result = await self.client._request_result(
                    "GET", "/server", semantics=RequestSemantics.READ
                )
                self.assertEqual(result.error_kind, AmneziaErrorKind.AUTH_FAILED)
                self.assertFalse(result.retryable)
                self.assertFalse(result.ambiguous)
                self.assertEqual(module._get_circuit_breaker(self.client.api_url).failure_count, 0)
                self.addCleanup(lambda: None)

    async def test_validation_errors_do_not_increment_breaker(self):
        for status in (400, 422):
            session = self.use_session(FakeResponse(status))
            result = await self.client._request_result(
                "GET", "/clients", semantics=RequestSemantics.READ
            )
            self.assertEqual(result.error_kind, AmneziaErrorKind.VALIDATION_FAILED)
            self.assertFalse(result.retryable)
            self.assertEqual(module._get_circuit_breaker(self.client.api_url).failure_count, 0)

    async def test_create_429_is_retryable_but_not_retried_or_breaker_failure(self):
        session = self.use_session(FakeResponse(429))
        result = await self.client.create_user_result("alice")
        self.assertEqual(session.request.call_count, 1)
        self.assertEqual(result.error_kind, AmneziaErrorKind.RATE_LIMITED)
        self.assertTrue(result.retryable)
        self.assertFalse(result.ambiguous)
        self.assertEqual(module._get_circuit_breaker(self.client.api_url).failure_count, 0)

    async def test_exhausted_read_5xx_increments_breaker(self):
        attempts = module.API_RETRY_COUNT + 1
        self.use_session(*(FakeResponse(500) for _ in range(attempts)))
        result = await self.client._request_result(
            "GET", "/server", semantics=RequestSemantics.READ
        )
        self.assertEqual(result.error_kind, AmneziaErrorKind.SERVER_ERROR)
        self.assertEqual(module._get_circuit_breaker(self.client.api_url).failure_count, 1)

    async def test_circuit_open_skips_network(self):
        session = self.use_session(FakeResponse(200, {}))
        breaker = module._get_circuit_breaker(self.client.api_url)
        breaker.state = "OPEN"
        breaker.last_failure_time = module.time.monotonic()
        result = await self.client._request_result(
            "GET", "/server", semantics=RequestSemantics.READ
        )
        self.assertEqual(session.request.call_count, 0)
        self.assertEqual(result.error_kind, AmneziaErrorKind.CIRCUIT_OPEN)
        self.assertTrue(result.retryable)

    async def test_compatibility_wrappers(self):
        success = AmneziaAPIResult(True, AmneziaClientCreateResponse(
            id="1", config="vpn", protocol="awg"
        ), None, 200, False, False)
        failure = AmneziaAPIResult(False, None, AmneziaErrorKind.TIMEOUT,
                                   None, False, True)
        with patch.object(self.client, "create_user_result", new=AsyncMock(
            side_effect=[success, failure]
        )):
            self.assertIsInstance(await self.client.create_user("alice"),
                                  AmneziaClientCreateResponse)
            self.assertIsNone(await self.client.create_user("alice"))
        with patch.object(self.client, "update_client_result", new=AsyncMock(
            side_effect=[module.AmneziaClient._success(), failure]
        )):
            self.assertTrue(await self.client.update_client("1"))
            self.assertFalse(await self.client.update_client("1"))
        self.use_session(FakeResponse(404))
        self.assertTrue(await self.client.delete_user("missing"))

    async def test_api_key_is_not_logged(self):
        secret = "SUPER_SECRET_TEST_KEY"
        client = AmneziaClient("https://vpn.example", secret)
        self.use_session(aiohttp.ClientConnectionError(secret))
        with self.assertLogs(module.logger, level=logging.WARNING) as captured:
            await client.create_user_result("alice")
        self.assertNotIn(secret, "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
