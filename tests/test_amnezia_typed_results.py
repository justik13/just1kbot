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
        getter = patch.object(
            module, "get_http_session", new=AsyncMock(return_value=session)
        )
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
                self.use_session(FakeResponse(status))
                result = await self.client._request_result(
                    "GET", "/server", semantics=RequestSemantics.READ
                )
                self.assertEqual(result.error_kind, AmneziaErrorKind.AUTH_FAILED)
                self.assertFalse(result.retryable)
                self.assertFalse(result.ambiguous)
                self.assertEqual(
                    module._get_circuit_breaker(self.client.api_url).failure_count, 0
                )
                self.addCleanup(lambda: None)

    async def test_validation_errors_do_not_increment_breaker(self):
        for status in (400, 422):
            self.use_session(FakeResponse(status))
            result = await self.client._request_result(
                "GET", "/clients", semantics=RequestSemantics.READ
            )
            self.assertEqual(result.error_kind, AmneziaErrorKind.VALIDATION_FAILED)
            self.assertFalse(result.retryable)
            self.assertEqual(
                module._get_circuit_breaker(self.client.api_url).failure_count, 0
            )

    async def test_create_429_is_retryable_but_not_retried_or_breaker_failure(self):
        session = self.use_session(FakeResponse(429))
        result = await self.client.create_user_result("alice")
        self.assertEqual(session.request.call_count, 1)
        self.assertEqual(result.error_kind, AmneziaErrorKind.RATE_LIMITED)
        self.assertTrue(result.retryable)
        self.assertFalse(result.ambiguous)
        self.assertEqual(
            module._get_circuit_breaker(self.client.api_url).failure_count, 0
        )

    async def test_exhausted_read_5xx_increments_breaker(self):
        attempts = module.API_RETRY_COUNT + 1
        self.use_session(*(FakeResponse(500) for _ in range(attempts)))
        result = await self.client._request_result(
            "GET", "/server", semantics=RequestSemantics.READ
        )
        self.assertEqual(result.error_kind, AmneziaErrorKind.SERVER_ERROR)
        self.assertEqual(
            module._get_circuit_breaker(self.client.api_url).failure_count, 1
        )

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
        success = AmneziaAPIResult(
            True,
            AmneziaClientCreateResponse(id="1", config="vpn", protocol="awg"),
            None,
            200,
            False,
            False,
        )
        failure = AmneziaAPIResult(
            False, None, AmneziaErrorKind.TIMEOUT, None, False, True
        )
        with patch.object(
            self.client,
            "create_user_result",
            new=AsyncMock(side_effect=[success, failure]),
        ):
            self.assertIsInstance(
                await self.client.create_user("alice"), AmneziaClientCreateResponse
            )
            self.assertIsNone(await self.client.create_user("alice"))
        with patch.object(
            self.client,
            "update_client_result",
            new=AsyncMock(side_effect=[module.AmneziaClient._success(), failure]),
        ):
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

    async def test_request_204_preserves_compatibility_marker(self):
        self.use_session(FakeResponse(204))
        result = await self.client._request("GET", "/healthz")
        self.assertEqual(result, {})
        self.assertIsNotNone(result)

    async def test_healthcheck_204_is_true(self):
        self.use_session(FakeResponse(204))
        self.assertTrue(await self.client.healthcheck())

    async def test_request_failure_remains_none(self):
        self.use_session(FakeResponse(400))
        self.assertIsNone(await self.client._request("GET", "/server"))

    async def test_session_creation_client_error_is_typed_and_safe_for_create(self):
        error = aiohttp.ClientConnectionError("session unavailable")
        with patch.object(
            module,
            "get_http_session",
            new=AsyncMock(side_effect=error),
        ) as get_session:
            result = await self.client.create_user_result("alice")
        get_session.assert_awaited_once()
        self.assertIsInstance(result, AmneziaAPIResult)
        self.assertEqual(result.error_kind, AmneziaErrorKind.NETWORK_ERROR)
        self.assertFalse(result.ambiguous)
        self.assertTrue(result.retryable)

    async def test_limiter_false_is_typed_timeout_without_http(self):
        limiter = MagicMock()
        limiter.acquire = AsyncMock(return_value=False)
        session = self.use_session(FakeResponse(200, {}))
        with patch.object(module, "_get_rate_limiter", return_value=limiter):
            result = await self.client._request_result(
                "GET", "/server", semantics=RequestSemantics.READ
            )
        self.assertEqual(result.error_kind, AmneziaErrorKind.RATE_LIMIT_TIMEOUT)
        self.assertTrue(result.retryable)
        self.assertFalse(result.ambiguous)
        self.assertEqual(session.request.call_count, 0)

    async def test_limiter_exception_is_typed_and_does_not_log_key(self):
        secret = "SUPER_SECRET_TEST_KEY"
        client = AmneziaClient("https://vpn.example", secret)
        limiter = MagicMock()
        limiter.acquire = AsyncMock(side_effect=RuntimeError(secret))
        session = self.use_session(FakeResponse(200, {}))
        with patch.object(module, "_get_rate_limiter", return_value=limiter):
            with self.assertLogs(module.logger, level=logging.ERROR) as captured:
                result = await client.create_user_result("alice")
        self.assertEqual(result.error_kind, AmneziaErrorKind.UNKNOWN)
        self.assertTrue(result.retryable)
        self.assertFalse(result.ambiguous)
        self.assertEqual(session.request.call_count, 0)
        self.assertNotIn(secret, "\n".join(captured.output))

    async def test_limiter_cancelled_error_propagates(self):
        limiter = MagicMock()
        limiter.acquire = AsyncMock(side_effect=asyncio.CancelledError())
        with patch.object(module, "_get_rate_limiter", return_value=limiter):
            with self.assertRaises(asyncio.CancelledError):
                await self.client.create_user_result("alice")

    async def test_read_body_timeout_retries(self):
        session = self.use_session(
            FakeResponse(200, json_error=asyncio.TimeoutError()),
            FakeResponse(200, {"name": "server"}),
        )
        result = await self.client._request_result(
            "GET", "/server", semantics=RequestSemantics.READ
        )
        self.assertEqual(session.request.call_count, 2)
        self.assertTrue(result.ok)

    async def test_create_body_timeout_is_ambiguous_and_not_retried(self):
        session = self.use_session(FakeResponse(200, json_error=asyncio.TimeoutError()))
        result = await self.client.create_user_result("alice")
        self.assertEqual(session.request.call_count, 1)
        self.assertEqual(result.error_kind, AmneziaErrorKind.TIMEOUT)
        self.assertTrue(result.ambiguous)
        self.assertFalse(result.retryable)

    async def test_read_content_type_error_is_invalid_response(self):
        content_error = aiohttp.ContentTypeError(
            MagicMock(), (), message="unexpected content type"
        )
        self.use_session(FakeResponse(200, json_error=content_error))
        result = await self.client._request_result(
            "GET", "/server", semantics=RequestSemantics.READ
        )
        self.assertEqual(result.error_kind, AmneziaErrorKind.INVALID_RESPONSE)
        self.assertFalse(result.ambiguous)

    async def test_idempotent_write_body_client_error_retries(self):
        attempts = module.API_RETRY_COUNT + 1
        session = self.use_session(
            *(
                FakeResponse(
                    200,
                    json_error=aiohttp.ClientPayloadError("truncated"),
                )
                for _ in range(attempts)
            )
        )
        result = await self.client.update_client_result("peer-1", status="active")
        self.assertEqual(session.request.call_count, attempts)
        self.assertEqual(result.error_kind, AmneziaErrorKind.NETWORK_ERROR)
        self.assertTrue(result.ambiguous)
        self.assertTrue(result.retryable)

    async def test_delete_404_resets_breaker(self):
        breaker = module._get_circuit_breaker(self.client.api_url)
        breaker.failure_count = 4
        breaker.state = "CLOSED"
        self.use_session(FakeResponse(404))
        result = await self.client.delete_user_result("missing")
        self.assertTrue(result.ok)
        self.assertEqual(breaker.failure_count, 0)
        self.assertEqual(breaker.state, "CLOSED")

    async def test_read_404_neither_increments_nor_resets_breaker(self):
        breaker = module._get_circuit_breaker(self.client.api_url)
        breaker.failure_count = 4
        self.use_session(FakeResponse(404))
        result = await self.client._request_result(
            "GET", "/clients", semantics=RequestSemantics.READ
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, AmneziaErrorKind.NOT_FOUND)
        self.assertEqual(breaker.failure_count, 4)

    async def test_result_factories_preserve_invariants(self):
        success = self.client._success({"value": True}, 200)
        self.assertTrue(success.ok)
        self.assertIsNone(success.error_kind)
        self.assertFalse(success.retryable)
        self.assertFalse(success.ambiguous)

        failure = self.client._failure(
            AmneziaErrorKind.AUTH_FAILED,
            RequestSemantics.READ,
        )
        self.assertFalse(failure.ok)
        self.assertIsNotNone(failure.error_kind)


if __name__ == "__main__":
    unittest.main()
