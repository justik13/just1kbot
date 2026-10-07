import asyncio
import hashlib
import ipaddress
import json
import logging
import os
from pathlib import Path
import time
import uuid

import redis.asyncio as aioredis
from aiohttp import web
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from bot.middlewares.correlation import set_request_id
from config.constants import YOOKASSA_IP_RANGES
from config.settings import get_settings
from database.connection import session_scope
from database.models import WebhookInbox
from utils.http_rate_limiter import get_trusted_client_ip

logger = logging.getLogger(__name__)
_healthcheck_redis = None

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
        try:
            allowed_ranges = get_settings().yookassa_allowed_ip_ranges
        except Exception:
            allowed_ranges = YOOKASSA_IP_RANGES
        for cidr in allowed_ranges:
            try:
                if client_ip in ipaddress.ip_network(cidr, strict=False):
                    return True
            except ValueError:
                continue
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
        logger.warning(
            "[%s] [SECURITY] Rejected YooKassa webhook from unverified IP: %s. "
            "If YooKassa added new webhook IP ranges, configure them via YOOKASSA_EXTRA_IPS in .env.",
            request_id,
            peer_ip,
        )
        return web.Response(status=404, text="Not Found")
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
    except Exception as exc:
        logger.warning(
            "[%s] Rejected invalid YooKassa webhook payload from %s: %s",
            request_id,
            peer_ip,
            exc,
        )
        return web.Response(status=400, text="Invalid webhook")
    try:
        async with session_scope() as session:
            from config.enums import WebhookInboxStatus
            from services.order_service import OrderService

            existing_event = await session.scalar(
                select(WebhookInbox).where(
                    WebhookInbox.provider == "yookassa",
                    WebhookInbox.event_key == event_key,
                )
            )
            if existing_event and existing_event.status != WebhookInboxStatus.PENDING.value:
                logger.info(
                    "[%s] YooKassa webhook event %s already processed (inbox_id=%s, status=%s), skipping",
                    request_id,
                    event_key,
                    existing_event.id,
                    existing_event.status,
                )
                return web.Response(status=200, text="OK")

            order = await OrderService.process_webhook_event(session, payload)
            inbox_status = (
                WebhookInboxStatus.SUCCEEDED.value
                if order
                else WebhookInboxStatus.PENDING.value
            )

            if order and order.status == "paid" and getattr(order, "_newly_paid", False):
                bot = request.app.get("bot")
                if bot:
                    from database.models import User
                    from bot.handlers.payment.credit_notify import (
                        notify_order_credited,
                    )
                    from services.order_notifications import (
                        mark_notified,
                        mark_notify_pending,
                    )

                    user = await session.get(User, order.user_id)
                    try:
                        delivered = await notify_order_credited(
                            bot, session, order, user
                        )
                    except Exception as exc:
                        logger.warning(
                            "Could not notify user of order fulfillment: %s", exc
                        )
                        delivered = False
                    if delivered:
                        mark_notified(order)
                    else:
                        # Keep the debt: the credit-notify worker retries
                        # the push for users who already left the screen.
                        mark_notify_pending(order)
                    await session.flush()

            if existing_event:
                existing_event.status = inbox_status
                if public_order_id:
                    existing_event.public_order_id = public_order_id
            else:
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
                        status=inbox_status,
                    )
                    .on_conflict_do_nothing(
                        constraint="uq_webhook_inbox_provider_event_key"
                    )
                )
    except Exception:
        logger.exception("[%s] webhook inbox commit failed", request_id)
        return web.Response(status=500, text="Database unavailable")
    if not order:
        return web.Response(status=503, text="order_pending_retry")
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
            _healthcheck_cache = (now, 503, "Unhealthy")
            return web.Response(status=503, text="Unhealthy")

        # Проверка Redis
        try:
            r = _get_healthcheck_redis()
            await r.ping()
        except Exception as e:
            logger.warning("Healthcheck Redis failed: %s", e)
            _healthcheck_cache = (now, 503, "Unhealthy")
            return web.Response(status=503, text="Unhealthy")

        # Проверка Workers Heartbeat (если файл настроен или существует)
        content = await asyncio.to_thread(_read_worker_heartbeat)
        if content is not None:
            if content.startswith("STOPPED"):
                logger.warning("Healthcheck Workers failed: reported STOPPED")
                _healthcheck_cache = (now, 503, "Unhealthy (Workers Stopped)")
                return web.Response(status=503, text="Unhealthy (Workers Stopped)")
            try:
                ts = int(content)
                now_wall = int(time.time())
                if now_wall - ts > 180:
                    logger.warning("Healthcheck Workers failed: stale heartbeat (age=%ss)", int(now_wall - ts))
                    _healthcheck_cache = (now, 503, "Unhealthy (Workers Stale)")
                    return web.Response(status=503, text="Unhealthy (Workers Stale)")
            except ValueError:
                pass

        _healthcheck_cache = (now, 200, "OK")
        return web.Response(status=200, text="OK")


def _read_worker_heartbeat() -> str | None:
    heartbeat_path = os.environ.get("JUST1KBOT_HEARTBEAT_FILE", "/run/just1kbot/heartbeat")
    heartbeat_file = Path(heartbeat_path)
    if heartbeat_file.exists():
        try:
            return heartbeat_file.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning("Healthcheck Workers heartbeat read error: %s", e)
    return None


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
    app.router.add_post(
        "/webhook/yookassa",
        yookassa_webhook_handler,
    )
    app.router.add_post(
        "/yookassa/webhook",
        yookassa_webhook_handler,
    )
    app.router.add_get("/health", healthcheck_handler)
    app.on_cleanup.append(_close_healthcheck_redis)
    logger.info("YooKassa webhook route registered: POST /webhook/yookassa & POST /yookassa/webhook")
    logger.info("Healthcheck endpoint registered: GET /health")

    # Register White Internet subscription feed routes
    from bot.handlers.white_internet_web import setup_white_internet_web_routes
    setup_white_internet_web_routes(app)
