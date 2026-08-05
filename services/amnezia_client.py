import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Generic, List, Optional, TypeVar
from urllib.parse import urlsplit

import aiohttp
from pydantic import BaseModel, Field

from bot.constants import (
    AMNEZIA_PROTOCOL,
    API_TIMEOUT,
    API_CONCURRENCY_LIMIT,
    API_RETRY_COUNT,
)
from utils.security import SafeResolver, allow_local_networks

logger = logging.getLogger(__name__)

_http_session: Optional[aiohttp.ClientSession] = None


def _safe_api_target(api_url: str) -> str:
    """Return a log-safe endpoint without credentials or query data."""
    parsed = urlsplit(api_url)
    host = parsed.hostname or "<invalid-host>"
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port:
        host = f"{host}:{port}"
    return host


T = TypeVar("T")


class AmneziaErrorKind(str, Enum):
    CONFIGURATION = "configuration"
    CIRCUIT_OPEN = "circuit_open"
    RATE_LIMIT_TIMEOUT = "rate_limit_timeout"
    RATE_LIMITED = "rate_limited"
    AUTH_FAILED = "auth_failed"
    NOT_FOUND = "not_found"
    VALIDATION_FAILED = "validation_failed"
    REDIRECT = "redirect"
    SERVER_ERROR = "server_error"
    NETWORK_ERROR = "network_error"
    TIMEOUT = "timeout"
    INVALID_RESPONSE = "invalid_response"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class AmneziaAPIResult(Generic[T]):
    ok: bool
    value: Optional[T]
    error_kind: Optional[AmneziaErrorKind]
    status_code: Optional[int]
    retryable: bool
    ambiguous: bool


class RequestSemantics(str, Enum):
    READ = "read"
    IDEMPOTENT_WRITE = "idempotent_write"
    CREATE = "create"


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.state = "CLOSED"
        self.last_failure_time = 0.0
        self._lock = asyncio.Lock()

    async def is_available(self) -> bool:
        async with self._lock:
            if self.state == "OPEN":
                elapsed = (
                    time.monotonic() - self.last_failure_time
                )
                if elapsed > self.recovery_timeout:
                    logger.info(
                        "Circuit breaker: half-open, "
                        "attempting recovery "
                        "(was OPEN for %.0fs)",
                        elapsed,
                    )
                    self.state = "CLOSED"
                    self.failure_count = 0
                    return True
                return False
            return True

    async def record_success(self):
        async with self._lock:
            if self.failure_count > 0:
                logger.info(
                    "Circuit breaker: request succeeded, "
                    "resetting failure count (%s -> 0)",
                    self.failure_count,
                )
            self.failure_count = 0
            self.state = "CLOSED"

    async def record_failure(self):
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.monotonic()
            if self.failure_count >= self.failure_threshold:
                if self.state != "OPEN":
                    logger.warning(
                        "Circuit breaker: OPEN after %s failures. "
                        "Will retry in %.0fs.",
                        self.failure_count,
                        self.recovery_timeout,
                    )
                self.state = "OPEN"

    @property
    def is_open(self) -> bool:
        return self.state == "OPEN"


_circuit_breakers: dict[str, CircuitBreaker] = {}


def _get_circuit_breaker(api_url: str) -> CircuitBreaker:
    if api_url not in _circuit_breakers:
        _circuit_breakers[api_url] = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=60.0,
        )
    return _circuit_breakers[api_url]


def cleanup_server_circuit_breakers(api_url: str) -> None:
    api_url = (api_url or "").rstrip("/")
    if api_url in _circuit_breakers:
        del _circuit_breakers[api_url]
        logger.debug(
            "Circuit breaker cleaned for %s",
            _safe_api_target(api_url),
        )
    if api_url in _rate_limiters:
        del _rate_limiters[api_url]
        logger.debug(
            "Rate limiter cleaned for %s",
            _safe_api_target(api_url),
        )


class TokenBucketRateLimiter:
    def __init__(self, rate: float = 3.0, burst: int = 5):
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self.last_refill
                self.tokens = min(
                    self.burst,
                    self.tokens + elapsed * self.rate,
                )
                self.last_refill = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            await asyncio.sleep(
                min(1.0 / self.rate, remaining)
            )


_rate_limiters: dict[str, TokenBucketRateLimiter] = {}


def _get_rate_limiter(api_url: str) -> TokenBucketRateLimiter:
    if api_url not in _rate_limiters:
        _rate_limiters[api_url] = TokenBucketRateLimiter(
            rate=3.0,
            burst=5,
        )
    return _rate_limiters[api_url]


class AmneziaClientCreateResponse(BaseModel):
    id: str
    config: str
    protocol: str = AMNEZIA_PROTOCOL


class AmneziaClientTraffic(BaseModel):
    totalDownload: int = 0
    totalUpload: int = 0
    received: int = 0
    sent: int = 0


class AmneziaClientListItem(BaseModel):
    id: str
    username: str = ""
    peer_name: str = ""
    status: str = "active"
    traffics: AmneziaClientTraffic = Field(
        default_factory=AmneziaClientTraffic
    )
    lastHandshake: Optional[float] = None
    lastSeen: Optional[float] = None
    updatedAt: Optional[float] = None

    @property
    def clientName(self) -> str:
        return self.username

    @property
    def name(self) -> str:
        return self.peer_name


class AmneziaServerInfo(BaseModel):
    name: str = ""
    protocols: List[str] = Field(default_factory=list)
    maxPeers: int = 0
    serverMaxPeers: int = 0
    SERVER_MAX_PEERS: int = 250

    def get_effective_max_peers(self) -> int:
        return (
            self.maxPeers
            or self.serverMaxPeers
            or self.SERVER_MAX_PEERS
        )


async def get_http_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None:
        connector = aiohttp.TCPConnector(
            limit=100,
            limit_per_host=API_CONCURRENCY_LIMIT,
            resolver=SafeResolver(
                allow_local=allow_local_networks(),
            ),
        )
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        _http_session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
        )
    return _http_session


async def close_http_session():
    global _http_session
    if _http_session:
        await _http_session.close()
        _http_session = None


class AmneziaClient:
    def __init__(self, api_url: str, api_key: str):
        self.api_url = (api_url or "").rstrip("/")
        self.api_key = api_key or ""
        self._log_target = _safe_api_target(self.api_url)
        self._headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        self._key_error_logged = False

    @staticmethod
    def _success(
        value: Any = None,
        status_code: Optional[int] = None,
    ) -> AmneziaAPIResult[Any]:
        return AmneziaAPIResult(
            ok=True,
            value=value,
            error_kind=None,
            status_code=status_code,
            retryable=False,
            ambiguous=False,
        )

    @staticmethod
    def _failure(
        kind: AmneziaErrorKind,
        semantics: RequestSemantics,
        *,
        status_code: Optional[int] = None,
        retryable: bool = False,
        ambiguous: Optional[bool] = None,
    ) -> AmneziaAPIResult[Any]:
        if ambiguous is None:
            ambiguous = semantics is not RequestSemantics.READ
        if (
            semantics is RequestSemantics.CREATE
            and ambiguous
            and kind in {
                AmneziaErrorKind.SERVER_ERROR,
                AmneziaErrorKind.NETWORK_ERROR,
                AmneziaErrorKind.TIMEOUT,
                AmneziaErrorKind.INVALID_RESPONSE,
            }
        ):
            retryable = False
        return AmneziaAPIResult(
            ok=False,
            value=None,
            error_kind=kind,
            status_code=status_code,
            retryable=retryable,
            ambiguous=ambiguous,
        )

    async def _request_result(
        self,
        method: str,
        path: str,
        *,
        semantics: RequestSemantics,
        not_found_as_success: bool = False,
        **kwargs,
    ) -> AmneziaAPIResult[Any]:
        if not self.api_url:
            logger.error("AmneziaClient: empty API URL")
            return self._failure(
                AmneziaErrorKind.CONFIGURATION,
                semantics,
                ambiguous=False,
            )

        if not self.api_key:
            if not self._key_error_logged:
                logger.critical(
                    "AmneziaClient: empty API key for %s%s. "
                    "This usually means DB_ENCRYPTION_KEY issue "
                    "or corrupted encrypted server key.",
                    self._log_target,
                    path,
                )
                self._key_error_logged = True
            return self._failure(
                AmneziaErrorKind.CONFIGURATION,
                semantics,
                ambiguous=False,
            )

        url = f"{self.api_url}{path}"
        cb = _get_circuit_breaker(self.api_url)

        if not await cb.is_available():
            logger.debug(
                "Circuit breaker OPEN for %s%s, skipping request",
                self._log_target,
                path,
            )
            return self._failure(
                AmneziaErrorKind.CIRCUIT_OPEN,
                semantics,
                retryable=True,
                ambiguous=False,
            )

        limiter = _get_rate_limiter(self.api_url)

        max_attempts = (
            1
            if semantics is RequestSemantics.CREATE
            else API_RETRY_COUNT + 1
        )
        for attempt in range(max_attempts):
            try:
                limiter_acquired = await limiter.acquire(timeout=30.0)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.error(
                    "Rate limiter error for %s%s: %s",
                    self._log_target,
                    path,
                    type(error).__name__,
                )
                return self._failure(
                    AmneziaErrorKind.UNKNOWN,
                    semantics,
                    retryable=True,
                    ambiguous=False,
                )
            if not limiter_acquired:
                logger.warning(
                    "Rate limit timeout for %s%s "
                    "(attempt %s/%s)",
                    self._log_target,
                    path,
                    attempt + 1,
                    max_attempts,
                )
                return self._failure(
                    AmneziaErrorKind.RATE_LIMIT_TIMEOUT,
                    semantics,
                    retryable=True,
                    ambiguous=False,
                )

            request_started = False
            try:
                session = await get_http_session()
                request_started = True
                async with session.request(
                    method,
                    url,
                    headers=self._headers,
                    allow_redirects=False,
                    **kwargs,
                ) as response:
                    if response.status == 204:
                        await cb.record_success()
                        return self._success(status_code=204)
                    elif 200 <= response.status < 300:
                        value = await response.json()
                        await cb.record_success()
                        return self._success(value, response.status)
                    elif 300 <= response.status < 400:
                        logger.warning(
                            "API %s%s returned redirect %s. "
                            "Redirects are disabled.",
                            self._log_target,
                            path,
                            response.status,
                        )
                        return self._failure(
                            AmneziaErrorKind.REDIRECT,
                            semantics,
                            status_code=response.status,
                            ambiguous=False,
                        )
                    elif response.status == 429:
                        if attempt + 1 < max_attempts:
                            backoff = 2 ** (attempt + 1)
                            logger.warning(
                                "API %s%s returned 429, "
                                "retrying in %ss",
                                self._log_target,
                                path,
                                backoff,
                            )
                            await asyncio.sleep(backoff)
                            continue
                        logger.warning(
                            "API %s%s returned 429 after all retries "
                            "(rate limited, NOT a server failure)",
                            self._log_target,
                            path,
                        )
                        return self._failure(
                            AmneziaErrorKind.RATE_LIMITED,
                            semantics,
                            status_code=429,
                            retryable=True,
                            ambiguous=False,
                        )
                    elif 400 <= response.status < 500:
                        if (
                            not_found_as_success
                            and response.status == 404
                        ):
                            await cb.record_success()
                            return self._success(status_code=404)
                        if response.status in (401, 403):
                            kind = AmneziaErrorKind.AUTH_FAILED
                        elif response.status == 404:
                            kind = AmneziaErrorKind.NOT_FOUND
                        elif response.status in (400, 409, 422):
                            kind = AmneziaErrorKind.VALIDATION_FAILED
                        else:
                            kind = AmneziaErrorKind.UNKNOWN
                        logger.warning(
                            "API %s%s returned %s (%s)",
                            self._log_target,
                            path,
                            response.status,
                            kind.value,
                        )
                        return self._failure(
                            kind,
                            semantics,
                            status_code=response.status,
                            ambiguous=False,
                        )
                    else:
                        if (
                            attempt + 1 < max_attempts
                            and response.status >= 500
                        ):
                            backoff = 2 ** attempt
                            logger.warning(
                                "API %s%s returned %s, "
                                "retrying in %ss (attempt %s)",
                                self._log_target,
                                path,
                                response.status,
                                backoff,
                                attempt + 1,
                            )
                            await asyncio.sleep(backoff)
                            continue
                        if response.status >= 500:
                            await cb.record_failure()
                            return self._failure(
                                AmneziaErrorKind.SERVER_ERROR,
                                semantics,
                                status_code=response.status,
                                retryable=True,
                            )
                        return self._failure(
                            AmneziaErrorKind.UNKNOWN,
                            semantics,
                            status_code=response.status,
                            ambiguous=False,
                        )

            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError as error:
                kind = AmneziaErrorKind.TIMEOUT
                if attempt + 1 < max_attempts:
                    backoff = 2 ** attempt
                    logger.warning(
                        "Network error for %s%s: %s, "
                        "retrying in %ss (attempt %s)",
                        self._log_target,
                        path,
                        type(error).__name__,
                        backoff,
                        attempt + 1,
                    )
                    await asyncio.sleep(backoff)
                else:
                    await cb.record_failure()
                    logger.error(
                        "All retries exhausted for %s%s: %s",
                        self._log_target,
                        path,
                        type(error).__name__,
                    )
                    return self._failure(
                        kind,
                        semantics,
                        retryable=True,
                        ambiguous=(
                            semantics is not RequestSemantics.READ
                            if request_started
                            else False
                        ),
                    )
            except aiohttp.ContentTypeError:
                return self._failure(
                    AmneziaErrorKind.INVALID_RESPONSE,
                    semantics,
                    retryable=True,
                )
            except aiohttp.ClientError as error:
                kind = AmneziaErrorKind.NETWORK_ERROR
                if attempt + 1 < max_attempts:
                    backoff = 2 ** attempt
                    logger.warning(
                        "Network error for %s%s: %s, "
                        "retrying in %ss (attempt %s)",
                        self._log_target,
                        path,
                        type(error).__name__,
                        backoff,
                        attempt + 1,
                    )
                    await asyncio.sleep(backoff)
                    continue
                await cb.record_failure()
                logger.error(
                    "All retries exhausted for %s%s: %s",
                    self._log_target,
                    path,
                    type(error).__name__,
                )
                return self._failure(
                    kind,
                    semantics,
                    retryable=True,
                    ambiguous=(
                        semantics is not RequestSemantics.READ
                        if request_started
                        else False
                    ),
                )
            except (ValueError, TypeError):
                return self._failure(
                    AmneziaErrorKind.INVALID_RESPONSE,
                    semantics,
                    retryable=True,
                )
            except Exception as error:
                logger.error(
                    "Unexpected error for %s%s: %s",
                    self._log_target,
                    path,
                    type(error).__name__,
                )
                return self._failure(
                    AmneziaErrorKind.UNKNOWN,
                    semantics,
                    retryable=not request_started,
                    ambiguous=(
                        semantics is not RequestSemantics.READ
                        if request_started
                        else False
                    ),
                )

        return self._failure(AmneziaErrorKind.UNKNOWN, semantics)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        semantics: RequestSemantics = RequestSemantics.READ,
        not_found_as_success: bool = False,
        **kwargs,
    ) -> Optional[Any]:
        result = await self._request_result(
            method,
            path,
            semantics=semantics,
            not_found_as_success=not_found_as_success,
            **kwargs,
        )
        if not result.ok:
            return None
        return result.value if result.value is not None else {}

    async def create_user_result(
        self,
        client_name: str,
        expires_at: Optional[int] = None,
    ) -> AmneziaAPIResult[AmneziaClientCreateResponse]:
        # Проверка емкости сервера удалена - она должна выполняться на уровне бизнес-логики (DeviceService)
        # чтобы избежать лишних HTTP-запросов и TOCTOU race conditions
        
        data = {
            "clientName": client_name,
            "protocol": AMNEZIA_PROTOCOL,
            "expiresAt": expires_at,
        }
        result = await self._request_result(
            "POST",
            "/clients",
            semantics=RequestSemantics.CREATE,
            json=data,
        )
        if not result.ok:
            return result
        if isinstance(result.value, dict) and "client" in result.value:
            try:
                client = AmneziaClientCreateResponse(
                    **result.value["client"]
                )
                return self._success(client, result.status_code)
            except Exception as error:
                logger.error(
                    "Failed to parse create_user response: %s",
                    type(error).__name__,
                )
        return self._failure(
            AmneziaErrorKind.INVALID_RESPONSE,
            RequestSemantics.CREATE,
            status_code=result.status_code,
            retryable=False,
            ambiguous=True,
        )

    async def create_user(
        self,
        client_name: str,
        expires_at: Optional[int] = None,
    ) -> Optional[AmneziaClientCreateResponse]:
        result = await self.create_user_result(client_name, expires_at)
        return result.value if result.ok else None

    async def delete_user_result(
        self,
        client_id: str,
    ) -> AmneziaAPIResult[None]:
        data = {
            "clientId": client_id,
            "protocol": AMNEZIA_PROTOCOL,
        }
        result = await self._request_result(
            "DELETE",
            "/clients",
            semantics=RequestSemantics.IDEMPOTENT_WRITE,
            json=data,
            not_found_as_success=True,
        )
        if result.ok:
            return self._success(status_code=result.status_code)
        return result

    async def delete_user(self, client_id: str) -> bool:
        return (await self.delete_user_result(client_id)).ok

    async def update_client_result(
        self,
        client_id: str,
        status: Optional[str] = None,
        expires_at: Optional[int] = None,
        clear_expires_at: bool = False,
    ) -> AmneziaAPIResult[None]:
        data = {
            "clientId": client_id,
            "protocol": AMNEZIA_PROTOCOL,
        }
        if expires_at is not None and status is None:
            status = "active"
        if status is not None:
            data["status"] = status
        if clear_expires_at:
            data["expiresAt"] = None
        elif expires_at is not None:
            data["expiresAt"] = expires_at

        result = await self._request_result(
            "PATCH",
            "/clients",
            semantics=RequestSemantics.IDEMPOTENT_WRITE,
            json=data,
        )
        if result.ok:
            return self._success(status_code=result.status_code)
        return result

    async def update_client(
        self,
        client_id: str,
        status: Optional[str] = None,
        expires_at: Optional[int] = None,
        clear_expires_at: bool = False,
    ) -> bool:
        result = await self.update_client_result(
            client_id,
            status,
            expires_at,
            clear_expires_at,
        )
        return result.ok

    async def get_server_info(
        self,
    ) -> Optional[AmneziaServerInfo]:
        result = await self._request(
            "GET",
            "/server",
            semantics=RequestSemantics.READ,
        )
        if result:
            try:
                return AmneziaServerInfo(**result)
            except Exception as e:
                logger.error(
                    "Failed to parse get_server_info response: %s",
                    e,
                )
                return None
        return None

    async def healthcheck(self) -> bool:
        return (
            await self._request(
                "GET",
                "/healthz",
                semantics=RequestSemantics.READ,
            )
        ) is not None

    async def get_all_clients(
        self,
    ) -> Optional[List[AmneziaClientListItem]]:
        """
        Возвращает полный список клиентов.

        Важно:
        - если API недоступен, возвращает None;
        - если не удалось получить хотя бы одну страницу,
          возвращает None;
        - частичные данные больше НЕ возвращаются, потому что
          они могут привести к неверному подсчёту слотов;
        - если API вернул сырые элементы, но ни один не распарсился,
          это считается ошибкой формата API, а не реальным нулём.

        Поддерживаются разные форматы ответа:
        1) {"items": [...]}
        2) {"clients": [...]}
        3) {"data": [...]}
        4) [...]
        5) вложенные peers:
           [{"username": "...", "peers": [...]}]
        6) плоские клиенты:
           [{"id": "...", "username": "...", "traffic": {...}}]
        """
        all_clients: List[AmneziaClientListItem] = []
        page_size = 100
        page_count = 0
        MAX_SAFETY_PAGES = 100

        from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
        import httpx

        # P2-4: Add tenacity retry to pagination
        @retry(
            wait=wait_exponential(multiplier=1, min=2, max=10),
            stop=stop_after_attempt(3),
            retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
            reraise=True
        )
        async def fetch_page(skip: int, limit: int):
            return await self._request(
                "GET",
                "/clients",
                semantics=RequestSemantics.READ,
                params={
                    "skip": skip,
                    "limit": limit,
                },
            )

        while page_count < MAX_SAFETY_PAGES:
            try:
                result = await fetch_page(page_count * page_size, page_size)
            except Exception as e:
                logger.warning("get_all_clients: API failed on page %s after retries: %s", page_count, e)
                return None

            if result is None:
                logger.warning(
                    "get_all_clients: API returned None on page %s. "
                    "Returning None instead of partial result.",
                    page_count,
                )
                return None

            if isinstance(result, list):
                items_raw = result
            elif isinstance(result, dict):
                items_raw = (
                    result.get("items")
                    or result.get("clients")
                    or result.get("data")
                    or []
                )
                if isinstance(items_raw, dict):
                    items_raw = [items_raw]
            else:
                logger.error(
                    "get_all_clients: unexpected API response type: %s",
                    type(result).__name__,
                )
                return None

            if not isinstance(items_raw, list):
                logger.error(
                    "get_all_clients: unexpected items type: %s",
                    type(items_raw).__name__,
                )
                return None

            if not items_raw:
                break

            page_clients = self._parse_clients_page(items_raw)

            #
            # Защита от "ложного нуля".
            #
            # Если API вернул элементы, но ни один не удалось распарсить,
            # это похоже на изменение формата API.
            #
            # В таком случае нельзя считать, что на сервере 0 пиров,
            # потому что это может привести к переполнению сервера
            # и некорректной синхронизации.
            #
            if items_raw and not page_clients:
                logger.critical(
                    "get_all_clients: API returned items, "
                    "but none were parsed. "
                    "Treating this as API format error "
                    "instead of zero peers."
                )
                return None

            all_clients.extend(page_clients)

            #
            # ИСПРАВЛЕНО: пагинация по количеству СЫРЫХ элементов
            # из API, а не по количеству распарсенных пиров.
            #
            # Раньше: len(page_clients) < page_size
            # Проблема: при вложенном формате (1 элемент → 50 пиров)
            # или при ошибках парсинга page_clients может быть
            # меньше page_size, хотя API вернул полную страницу
            # и есть ещё данные.
            #
            # Теперь: len(items_raw) < page_size
            # Если API вернул меньше сырых элементов, чем запрошено,
            # значит страниц больше нет.
            #
            if len(items_raw) < page_size:
                break

            page_count += 1

        if page_count >= MAX_SAFETY_PAGES:
            logger.warning(
                "get_all_clients: reached safety limit "
                "(%s clients). Returning None because full list "
                "cannot be safely fetched.",
                MAX_SAFETY_PAGES * page_size,
            )
            return None

        logger.info(
            "get_all_clients: parsed %s peers across %s page(s)",
            len(all_clients),
            page_count + 1,
        )
        return all_clients

    @staticmethod
    def _parse_clients_page(
        items_raw: list,
    ) -> List[AmneziaClientListItem]:
        clients: List[AmneziaClientListItem] = []

        for item in items_raw:
            if not isinstance(item, dict):
                continue

            username = (
                item.get("username")
                or item.get("name")
                or item.get("clientName")
                or ""
            )

            peers = item.get("peers")

            #
            # Поддержка двух форматов:
            #
            # 1) item.peers = [...]
            # 2) item сам является peer/client объектом
            #
            if isinstance(peers, list) and peers:
                peer_items = peers
            else:
                peer_items = [item]

            for peer in peer_items:
                if not isinstance(peer, dict):
                    continue

                peer_id = (
                    peer.get("id")
                    or peer.get("clientId")
                    or peer.get("peerId")
                    or peer.get("publicKey")
                )
                if not peer_id:
                    continue

                traffic_raw = (
                    peer.get("traffic")
                    or peer.get("traffics")
                    or item.get("traffic")
                    or item.get("traffics")
                    or {}
                )

                if isinstance(traffic_raw, dict):
                    received = (
                        traffic_raw.get("received")
                        or traffic_raw.get("totalDownload")
                        or 0
                    )
                    sent = (
                        traffic_raw.get("sent")
                        or traffic_raw.get("totalUpload")
                        or 0
                    )
                    traffic = AmneziaClientTraffic(
                        totalDownload=received,
                        totalUpload=sent,
                        received=received,
                        sent=sent,
                    )
                else:
                    traffic = AmneziaClientTraffic()

                try:
                    client_item = AmneziaClientListItem(
                        id=str(peer_id),
                        username=(
                            peer.get("username")
                            or item.get("username")
                            or username
                            or ""
                        ),
                        peer_name=(
                            peer.get("name")
                            or peer.get("peer_name")
                            or item.get("name")
                            or ""
                        ),
                        status=(
                            peer.get("status")
                            or item.get("status")
                            or "active"
                        ),
                        traffics=traffic,
                        lastHandshake=(
                            peer.get("lastHandshake")
                            or item.get("lastHandshake")
                        ),
                        lastSeen=(
                            peer.get("lastSeen")
                            or item.get("lastSeen")
                        ),
                        updatedAt=(
                            peer.get("updatedAt")
                            or item.get("updatedAt")
                        ),
                    )
                    clients.append(client_item)
                except Exception as e:
                    logger.warning(
                        "Failed to parse peer item: %s, peer=%s",
                        e,
                        peer,
                    )
                    continue

        return clients
