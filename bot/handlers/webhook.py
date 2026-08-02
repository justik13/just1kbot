import hashlib
import ipaddress
import json
import logging
import uuid
from aiohttp import web
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from bot.middlewares.correlation import set_request_id
from config.settings import get_settings
from database.connection import session_scope
from database.models import WebhookInbox

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
    remote = (request.remote or "").strip()
    trusted_proxy = remote in {"127.0.0.1", "::1"}
    if trusted_proxy:
        real_ip = request.headers.get("X-Real-IP", "").strip()
        if real_ip:
            return real_ip
        forwarded = request.headers.get("X-Forwarded-For", "").strip()
        if forwarded:
            first_ip = forwarded.split(",")[0].strip()
            if first_ip:
                return first_ip
    return remote



async def yookassa_webhook_handler(request: web.Request) -> web.Response:
    """Authenticate, validate and durably persist only; workers own all effects."""
    request_id = uuid.uuid4().hex[:8]
    set_request_id(request_id)
    peer_ip = _get_real_ip(request)
    if not peer_ip or not _is_yookassa_ip(peer_ip):
        return web.Response(status=403, text="Forbidden")
    if request.content_length is not None and request.content_length > 262144:
        return web.Response(status=413, text="Payload too large")
    try:
        payload = await request.json()
        event, obj, provider_object_id, payment_external_id = (
            _validate_webhook_payload(payload)
        )

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


# ──────────────────────────────────────────────────────────────
# ИСПРАВЛЕНО: healthcheck проверяет DB и Redis.
# ──────────────────────────────────────────────────────────────
async def healthcheck_handler(
    request: web.Request,
) -> web.Response:
    # Проверка DB
    try:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
    except Exception as e:
        logger.warning("Healthcheck DB failed: %s", e)
        return web.Response(status=503, text="DB unavailable")

    # Проверка Redis
    try:
        r = _get_healthcheck_redis()
        await r.ping()
    except Exception as e:
        logger.warning("Healthcheck Redis failed: %s", e)
        return web.Response(status=503, text="Redis unavailable")

    return web.Response(status=200, text="OK")


def _get_healthcheck_redis():
    global _healthcheck_redis
    if _healthcheck_redis is None:
        settings = get_settings()
        _healthcheck_redis = aioredis.from_url(
            settings.REDIS_URL,
            socket_timeout=2.0,
        )
    return _healthcheck_redis


async def _close_healthcheck_redis(app: web.Application) -> None:
    global _healthcheck_redis
    if _healthcheck_redis is not None:
        await _healthcheck_redis.aclose()
        _healthcheck_redis = None


def setup_webhook_routes(app: web.Application):
    app.router.add_post(
        "/webhook/yookassa",
        yookassa_webhook_handler,
    )
    app.router.add_get("/health", healthcheck_handler)
    app.on_cleanup.append(_close_healthcheck_redis)
    logger.info("YooKassa webhook route registered: POST /webhook/yookassa")
    logger.info("Healthcheck endpoint registered: GET /health")
