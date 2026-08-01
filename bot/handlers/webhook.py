import hashlib
import ipaddress
import json
import logging
import uuid
from decimal import Decimal, InvalidOperation
from typing import Optional

from aiohttp import web
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from bot.middlewares.correlation import set_request_id
from config.settings import get_settings
from database.connection import session_scope
from database.models import WebhookInbox
from services.yookassa_service import YooKassaService

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
LEGACY_UNSUPPORTED_EVENTS = {"payment.refunded"}
REFUND_EVENTS = {"refund.succeeded", "payment.refunded"}


def _validate_webhook_object(obj: dict, event: str) -> tuple[str, str]:
    if event in REFUND_EVENTS:
        provider_object_id = obj.get("id")
        payment_external_id = obj.get("payment_id")

        if not payment_external_id:
            payment = obj.get("payment")
            if isinstance(payment, dict):
                payment_external_id = payment.get("id")
    else:
        provider_object_id = obj.get("id")
        payment_external_id = provider_object_id

    if not provider_object_id or not payment_external_id:
        raise ValueError("identity")

    return str(provider_object_id), str(payment_external_id)


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


def _safe_decimal(value: Optional[str]) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        return None


async def _verify_stale_webhook_via_api(
    webhook_object: dict,
    normalized_status: str,
    transaction_id: str,
) -> tuple[bool, Optional[dict]]:
    api_data = await YooKassaService.get_payment(transaction_id)
    if not api_data:
        return False, None

    api_status_raw = api_data.get("status", "")
    api_status_map = {
        "succeeded": "CONFIRMED",
        "canceled": "CANCELED",
        "pending": "PENDING",
        "processing": "PROCESSING",
        "waiting_for_capture": "WAITING_FOR_CAPTURE",
        "refunded": "REFUNDED",
    }
    api_status = api_status_map.get(
        api_status_raw,
        api_status_raw.upper(),
    )

    if normalized_status == "CHARGEBACKED":
        if api_status not in {"REFUNDED", "CANCELED", "CHARGEBACKED"}:
            logger.warning(
                "Stale webhook chargeback status mismatch: "
                "callback=%s, api=%s, payment=%s",
                normalized_status,
                api_status,
                transaction_id,
            )
            return False, None

        # Проверка суммы для chargeback
        callback_amount_str = None
        amount_obj = webhook_object.get("amount")
        if isinstance(amount_obj, dict):
            callback_amount_str = amount_obj.get("value")
        api_amount = api_data.get("amount", {})
        api_amount_str = (
            api_amount.get("value") if isinstance(api_amount, dict) else None
        )
        if callback_amount_str and api_amount_str:
            cb_decimal = _safe_decimal(callback_amount_str)
            api_decimal = _safe_decimal(api_amount_str)
            if cb_decimal is not None and api_decimal is not None:
                if cb_decimal != api_decimal:
                    logger.warning(
                        "Stale webhook chargeback amount mismatch: "
                        "callback=%s, api=%s, payment=%s",
                        callback_amount_str,
                        api_amount_str,
                        transaction_id,
                    )
                    return False, None

        # Проверка payload для chargeback
        callback_metadata = webhook_object.get("metadata") or {}
        callback_payload = callback_metadata.get("payload", "")
        api_metadata = api_data.get("metadata") or {}
        api_payload = api_metadata.get("payload", "")
        if callback_payload != api_payload:
            logger.warning(
                "Stale webhook chargeback payload mismatch: "
                "callback=%s, api=%s, payment=%s",
                callback_payload,
                api_payload,
                transaction_id,
            )
            return False, None

        return True, api_data

    if api_status != normalized_status:
        logger.warning(
            "Stale webhook status mismatch: callback=%s, api=%s, payment=%s",
            normalized_status,
            api_status,
            transaction_id,
        )
        return False, None

    callback_amount_str = None
    amount_obj = webhook_object.get("amount")
    if isinstance(amount_obj, dict):
        callback_amount_str = amount_obj.get("value")
    api_amount = api_data.get("amount", {})
    api_amount_str = api_amount.get("value") if isinstance(api_amount, dict) else None
    if callback_amount_str and api_amount_str:
        cb_decimal = _safe_decimal(callback_amount_str)
        api_decimal = _safe_decimal(api_amount_str)
        if cb_decimal is not None and api_decimal is not None:
            if cb_decimal != api_decimal:
                logger.warning(
                    "Stale webhook amount mismatch: callback=%s, api=%s, payment=%s",
                    callback_amount_str,
                    api_amount_str,
                    transaction_id,
                )
                return False, None

    callback_metadata = webhook_object.get("metadata") or {}
    callback_payload = callback_metadata.get("payload", "")
    api_metadata = api_data.get("metadata") or {}
    api_payload = api_metadata.get("payload", "")
    if callback_payload != api_payload:
        logger.warning(
            "Stale webhook payload mismatch: callback=%s, api=%s, payment=%s",
            callback_payload,
            api_payload,
            transaction_id,
        )
        return False, None

    return True, api_data


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
        event = payload.get("event")
        if event not in OFFICIAL_YOOKASSA_EVENTS | LEGACY_UNSUPPORTED_EVENTS:
            raise ValueError("unsupported_event")
        obj = payload.get("object")
        if not isinstance(event, str) or not isinstance(obj, dict):
            raise ValueError("structure")

        # Validate webhook object identifiers
        provider_object_id, payment_external_id = _validate_webhook_object(obj, event)

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
