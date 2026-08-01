"""Typed, idempotent YooKassa HTTP transport.

Provider commands receive their key and immutable body from PostgreSQL; this module
never invents idempotency keys or logs credentials/payloads.
"""

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar
import aiohttp
from config.settings import get_settings

T = TypeVar("T")
_client_session: aiohttp.ClientSession | None = None
_client_session_factory = None
_client_session_lock = asyncio.Lock()


async def _get_client_session() -> aiohttp.ClientSession:
    global _client_session, _client_session_factory
    session_factory = aiohttp.ClientSession
    is_closed = bool(getattr(_client_session, "closed", True))
    if (
        _client_session is None
        or is_closed
        or _client_session_factory is not session_factory
    ):
        async with _client_session_lock:
            is_closed = bool(getattr(_client_session, "closed", True))
            if (
                _client_session is None
                or is_closed
                or _client_session_factory is not session_factory
            ):
                if _client_session is not None and not is_closed:
                    close = getattr(_client_session, "close", None)
                    if close is not None:
                        await close()
                timeout = aiohttp.ClientTimeout(total=15)
                _client_session = session_factory(timeout=timeout)
                _client_session_factory = session_factory
    return _client_session


class YooKassaErrorKind(str, Enum):
    CONFIGURATION = "configuration"
    AUTH_FAILED = "auth_failed"
    VALIDATION_FAILED = "validation_failed"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    NETWORK_ERROR = "network_error"
    TIMEOUT = "timeout"
    INVALID_RESPONSE = "invalid_response"
    IDEMPOTENCY_WINDOW_EXPIRED = "create_idempotency_window_expired"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class YooKassaResult(Generic[T]):
    ok: bool
    value: T | None = None
    error_kind: YooKassaErrorKind | None = None
    status_code: int | None = None
    retryable: bool = False
    ambiguous: bool = False


class YooKassaService:
    API = "https://api.yookassa.ru/v3"

    @classmethod
    async def _request(
        cls,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        idempotency_key: str | None = None,
        ambiguous_on_failure=False,
    ) -> YooKassaResult[dict]:
        s = get_settings()
        shop = getattr(s, "YOOKASSA_SHOP_ID", None)
        secret = getattr(s, "YOOKASSA_SECRET_KEY", None)
        if not shop or not secret:
            return YooKassaResult(False, error_kind=YooKassaErrorKind.CONFIGURATION)
        headers = {"Accept": "application/json"}
        if idempotency_key:
            if len(idempotency_key) > 64:
                return YooKassaResult(
                    False, error_kind=YooKassaErrorKind.VALIDATION_FAILED
                )
            headers["Idempotence-Key"] = idempotency_key
        try:
            client = await _get_client_session()
            auth = aiohttp.BasicAuth(str(shop), str(secret))
            async with client.request(
                method, cls.API + path, json=payload, headers=headers, auth=auth
            ) as response:
                code = response.status
                try:
                    data = await response.json(content_type=None)
                except asyncio.CancelledError:
                    raise
                except asyncio.TimeoutError:
                    return YooKassaResult(
                        False,
                        error_kind=YooKassaErrorKind.TIMEOUT,
                        status_code=code,
                        retryable=True,
                        ambiguous=ambiguous_on_failure,
                    )
                except aiohttp.ClientError:
                    return YooKassaResult(
                        False,
                        error_kind=YooKassaErrorKind.NETWORK_ERROR,
                        status_code=code,
                        retryable=True,
                        ambiguous=ambiguous_on_failure,
                    )
                except (ValueError, TypeError):
                    return YooKassaResult(
                        False,
                        error_kind=YooKassaErrorKind.INVALID_RESPONSE,
                        status_code=code,
                        retryable=200 <= code < 300 or code >= 500,
                        ambiguous=ambiguous_on_failure,
                    )
                except Exception:
                    return YooKassaResult(
                        False,
                        error_kind=YooKassaErrorKind.UNKNOWN,
                        status_code=code,
                        retryable=False,
                        ambiguous=ambiguous_on_failure,
                    )
                if 200 <= code < 300:
                    if isinstance(data, dict):
                        return YooKassaResult(True, value=data, status_code=code)
                    return YooKassaResult(
                        False,
                        error_kind=YooKassaErrorKind.INVALID_RESPONSE,
                        status_code=code,
                        retryable=True,
                        ambiguous=ambiguous_on_failure,
                    )
                kind = YooKassaErrorKind.UNKNOWN
                if code in (401, 403):
                    kind = YooKassaErrorKind.AUTH_FAILED
                elif code == 404:
                    kind = YooKassaErrorKind.NOT_FOUND
                elif code == 429:
                    kind = YooKassaErrorKind.RATE_LIMITED
                elif code >= 500:
                    kind = YooKassaErrorKind.SERVER_ERROR
                elif code < 500:
                    kind = YooKassaErrorKind.VALIDATION_FAILED
                return YooKassaResult(
                    False,
                    error_kind=kind,
                    status_code=code,
                    retryable=code == 429 or code >= 500,
                    ambiguous=ambiguous_on_failure and code >= 500,
                )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            return YooKassaResult(
                False,
                error_kind=YooKassaErrorKind.TIMEOUT,
                retryable=True,
                ambiguous=ambiguous_on_failure,
            )
        except aiohttp.ClientError:
            return YooKassaResult(
                False,
                error_kind=YooKassaErrorKind.NETWORK_ERROR,
                retryable=True,
                ambiguous=ambiguous_on_failure,
            )
        except Exception:
            return YooKassaResult(
                False,
                error_kind=YooKassaErrorKind.UNKNOWN,
                retryable=False,
                ambiguous=False,
            )

    @classmethod
    async def create_payment_result(cls, payload: dict, *, idempotency_key: str):
        return await cls._request(
            "POST",
            "/payments",
            payload=payload,
            idempotency_key=idempotency_key,
            ambiguous_on_failure=True,
        )

    @classmethod
    async def get_payment_result(cls, payment_id: str):
        return await cls._request("GET", f"/payments/{payment_id}")

    @classmethod
    async def cancel_payment_result(cls, payment_id: str, *, idempotency_key: str):
        return await cls._request(
            "POST",
            f"/payments/{payment_id}/cancel",
            payload={},
            idempotency_key=idempotency_key,
            ambiguous_on_failure=True,
        )

    @classmethod
    async def get_payment(cls, payment_id):
        result = await cls.get_payment_result(payment_id)
        return result.value if result.ok else None

    @staticmethod
    def normalize_webhook_event(event: str) -> str:
        return {
            "payment.succeeded": "CONFIRMED",
            "payment.canceled": "CANCELED",
            "payment.refunded": "CHARGEBACKED",
            "refund.succeeded": "CHARGEBACKED",
            "payment.waiting_for_capture": "WAITING_FOR_CAPTURE",
        }.get(event, event.upper())


async def close_yookassa_client():
    global _client_session, _client_session_factory
    if _client_session is not None and not getattr(_client_session, "closed", False):
        close = getattr(_client_session, "close", None)
        if close is not None:
            await close()
    _client_session = None
    _client_session_factory = None
