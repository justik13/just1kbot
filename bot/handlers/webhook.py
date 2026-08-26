import asyncio
import hashlib
import ipaddress
import json
import logging
import time
import uuid

import redis.asyncio as aioredis
from aiohttp import web
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from bot.middlewares.correlation import set_request_id
from config.settings import get_settings
from database.connection import session_scope
from database.models import WebhookInbox
from utils.http_rate_limiter import get_trusted_client_ip

logger = logging.getLogger(__name__)
_healthcheck_redis = None

YOOKASSA_IP_RANGES = [
    "185.71.76.0/27",
    "185.71.77.0/27",
    "77.75.153.0/25",
    "77.75.154.128/25",
    "77.75.156.11/32",
    "77.75.156.35/32",
    "2a02:5180::/32",
]

OFFICIAL_YOOKASSA_EVENTS = {
    "payment.waiting_for_capture",
    "payment.succeeded",
    "payment.canceled",
    "refund.succeeded",
}
REFUND_EVENTS = {"refund.succeeded"}


def _validate_webhook_object(obj: dict, event: str) -> tuple[str, str]:
    provider_object_id = obj.get("id")
    payment_external_id = (
        obj.get("payment_id") if event in REFUND_EVENTS else provider_object_id
    )

    if not provider_object_id or not payment_external_id:
        raise ValueError("identity")

    return str(provider_object_id), str(payment_external_id)


def _validate_webhook_payload(payload: object) -> tuple[str, dict, str, str]:
    if not isinstance(payload, dict):
        raise ValueError("structure")
    if payload.get("type") != "notification":
        raise ValueError("notification_type")

    event = payload.get("event")
    obj = payload.get("object")
    if event not in OFFICIAL_YOOKASSA_EVENTS:
        raise ValueError("unsupported_event")
    if not isinstance(event, str) or not isinstance(obj, dict):
        raise ValueError("structure")

    provider_object_id, payment_external_id = _validate_webhook_object(obj, event)
    return event, obj, provider_object_id, payment_external_id


def _is_yookassa_ip(ip: str) -> bool:
    try:
        client_ip = ipaddress.ip_address(ip)
        for cidr in YOOKASSA_IP_RANGES:
            if client_ip in ipaddress.ip_network(cidr):
                return True
        return False
    except ValueError:
        return False


def _get_real_ip(request: web.Request) -> str:
    """Resolve the client IP honouring TRUSTED_PROXIES.

    X-Real-IP / X-Forwarded-For are only trusted when the direct peer is an
    explicitly configured reverse proxy; every other peer is taken verbatim
    from the socket so private networks cannot spoof the YooKassa allowlist.
    """
    return get_trusted_client_ip(request)


async def yookassa_webhook_handler(request: web.Request) -> web.Response:
    """Authenticate, validate and durably persist only; workers own all effects."""
    request_id = uuid.uuid4().hex[:8]
    set_request_id(request_id)
    peer_ip = _get_real_ip(request)
    if not peer_ip or not _is_yookassa_ip(peer_ip):
        logger.warning("[%s] Rejected webhook from unverified IP %s", request_id, peer_ip)
        return web.Response(status=403, text="Forbidden")
    if request.content_length is not None and request.content_length > 262144:
        return web.Response(status=413, text="Payload too large")
    try:
        payload = await request.json()
        event, obj, provider_object_id, payment_external_id = (
            _validate_webhook_payload(payload)
        )

        logger.info("[%s] Accepted YooKassa webhook event %s for object %s from IP %s", request_id, event, provider_object_id, peer_ip)
        metadata = obj.get("metadata") or {}
        public_order_id = metadata.get("order_id")
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        event_key = hashlib.sha256(canonical.encode()).hexdigest()
    except Exception:
        return web.Response(status=400, text="Invalid webhook")
    try:
        async with session_scope() as session:
            await session.execute(
                insert(WebhookInbox)
                .values(
                    provider="yookassa",
                    event_key=event_key,
                    event_type=event,
                    provider_object_id=str(provider_object_id),
                    payment_external_id=str(payment_external_id),
                    public_order_id=public_order_id,
                    payload=payload,
                )
                .on_conflict_do_nothing(
                    constraint="uq_webhook_inbox_provider_event_key"
                )
            )
    except Exception:
        logger.exception("[%s] webhook inbox commit failed", request_id)
        return web.Response(status=500, text="Database unavailable")
    return web.Response(status=200, text="OK")


_healthcheck_cache: tuple[float, int, str] | None = None
_HEALTHCHECK_CACHE_TTL = 5.0  # seconds
_healthcheck_lock: asyncio.Lock | None = None


def _get_healthcheck_lock() -> asyncio.Lock:
    global _healthcheck_lock
    if _healthcheck_lock is None:
        _healthcheck_lock = asyncio.Lock()
    return _healthcheck_lock


# ──────────────────────────────────────────────────────────────
# Healthcheck with 5-second in-memory TTL cache & single-flight lock
# ──────────────────────────────────────────────────────────────
async def healthcheck_handler(
    request: web.Request,
) -> web.Response:
    global _healthcheck_cache
    now = time.monotonic()
    if _healthcheck_cache is not None:
        cached_time, status_code, body = _healthcheck_cache
        if now - cached_time < _HEALTHCHECK_CACHE_TTL:
            return web.Response(status=status_code, text=body)

    async with _get_healthcheck_lock():
        now = time.monotonic()
        if _healthcheck_cache is not None:
            cached_time, status_code, body = _healthcheck_cache
            if now - cached_time < _HEALTHCHECK_CACHE_TTL:
                return web.Response(status=status_code, text=body)

        # Проверка DB
        try:
            async with session_scope() as session:
                await session.execute(text("SELECT 1"))
        except Exception as e:
            logger.warning("Healthcheck DB failed: %s", e)
            _healthcheck_cache = (now, 503, "DB unavailable")
            return web.Response(status=503, text="DB unavailable")

        # Проверка Redis
        try:
            r = _get_healthcheck_redis()
            await r.ping()
        except Exception as e:
            logger.warning("Healthcheck Redis failed: %s", e)
            _healthcheck_cache = (now, 503, "Redis unavailable")
            return web.Response(status=503, text="Redis unavailable")

        _healthcheck_cache = (now, 200, "OK")
        return web.Response(status=200, text="OK")


def _get_healthcheck_redis():
    global _healthcheck_redis
    if _healthcheck_redis is None:
        settings = get_settings()
        _healthcheck_redis = aioredis.from_url(
            settings.REDIS_URL,
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
        )
    return _healthcheck_redis


async def _close_healthcheck_redis(app: web.Application) -> None:
    global _healthcheck_redis
    if _healthcheck_redis is not None:
        await _healthcheck_redis.aclose()
        _healthcheck_redis = None


def setup_webhook_routes(app: web.Application):
    from bot.handlers.amnezia_bridge import amnezia_bridge_handler
    from bot.handlers.subscription_feed import (
        subscription_feed_handler,
        subscription_open_handler,
    )

    app.router.add_post(
        "/webhook/yookassa",
        yookassa_webhook_handler,
    )
    app.router.add_post(
        "/yookassa/webhook",
        yookassa_webhook_handler,
    )
    app.router.add_get("/health", healthcheck_handler)
    app.router.add_get("/sub/open/{token}", subscription_open_handler)
    app.router.add_get("/subscription/open/{token}", subscription_open_handler)
    app.router.add_get("/sub/{token}", subscription_feed_handler)
    app.router.add_get("/subscription/{token}", subscription_feed_handler)
    app.router.add_get("/amnezia/open/{profile_id}", amnezia_bridge_handler)
    app.on_cleanup.append(_close_healthcheck_redis)
    logger.info("YooKassa webhook route registered: POST /webhook/yookassa & POST /yookassa/webhook")
    logger.info("Healthcheck endpoint registered: GET /health")
    logger.info("Subscription feed endpoint registered: GET /sub/{token} & GET /subscription/{token}")
    logger.info("Subscription open endpoint registered: GET /sub/open/{token} & GET /subscription/open/{token}")
    logger.info("Amnezia bridge endpoint registered: GET /amnezia/open/{profile_id}")
