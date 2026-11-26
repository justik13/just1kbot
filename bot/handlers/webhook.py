import asyncio
import hashlib
import html
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


async def _process_yookassa_payload_in_session(
    session,
    payload: dict,
    *,
    event: str,
    request_id: str,
    is_existing_event: bool,
):
    from config.enums import WebhookInboxStatus
    from services.order_service import OrderService, extract_manual_review_alert

    order = await OrderService.process_webhook_event(session, payload)
    order_meta = (order.metadata_ or {}) if order else {}
    if not order:
        if event in ("refund.succeeded", "payment.succeeded", "payment.canceled"):
            inbox_status = WebhookInboxStatus.PENDING.value
        elif is_existing_event:
            inbox_status = WebhookInboxStatus.DEAD.value
        else:
            inbox_status = WebhookInboxStatus.PENDING.value
    elif getattr(order, "_webhook_amount_mismatch", False):
        logger.critical(
            "[%s] YooKassa webhook %s for order %s flagged payment_amount_mismatch (%s); marking inbox dead",
            request_id,
            event,
            order.id,
            order_meta.get("payment_amount_mismatch"),
        )
        inbox_status = WebhookInboxStatus.DEAD.value
    else:
        inbox_status = WebhookInboxStatus.SUCCEEDED.value

    notify_order_info = None
    if order and order.status == "paid" and getattr(order, "_newly_paid", False):
        from services.order_notifications import mark_notify_pending

        mark_notify_pending(order)
        notify_order_info = (order.id, order.user_id)
        await session.flush()

    admin_alert_info = extract_manual_review_alert(order, require_mismatch_key=True)
    if admin_alert_info is not None and order is not None:
        order_meta = order.metadata_ or {}
    refund_alert_info = None
    if (
        event == "refund.succeeded"
        and order is not None
        and inbox_status == WebhookInboxStatus.SUCCEEDED.value
        and not (
            order_meta.get("refund_payment_id_mismatch")
            or (
                order_meta.get("refund_amount_mismatch")
                and not order_meta.get("negative_balance_rub")
            )
        )
    ):
        refund_alert_info = {
            "order_id": order.id,
            "user_id": order.user_id,
            "service_type": order.service_type,
            "status": order.status,
            "amount_rub": (
                order_meta.get("refund_on_canceled_amount_rub")
                if order.status == "canceled"
                else (
                    order_meta.get("partial_refund_amount_rub")
                    if order.status == "partially_refunded"
                    else order.amount_rub
                )
            ) or order.amount_rub,
            "refund_received_on_canceled": bool(
                order_meta.get("refund_received_on_canceled")
            ),
        }
    return order, inbox_status, notify_order_info, admin_alert_info, refund_alert_info


async def _dispatch_yookassa_notifications(
    bot,
    *,
    request_id: str,
    order,
    notify_order_info,
    admin_alert_info,
    refund_alert_info,
) -> None:
    if not bot:
        return
    if notify_order_info:
        n_order_id, n_user_id = notify_order_info
        try:
            from database.models import Order, User
            from services.order_notifications import mark_notified
            from services.workers.credit_notifications import notify_order_credited

            async with session_scope() as notify_session:
                loaded_order = await notify_session.get(Order, n_order_id)
                db_order = loaded_order if isinstance(loaded_order, Order) else order
                loaded_user = await notify_session.get(User, n_user_id)
                db_user = loaded_user if isinstance(loaded_user, User) else None
                if db_order and db_user:
                    delivered = await notify_order_credited(
                        bot, notify_session, db_order, db_user
                    )
                    if delivered:
                        mark_notified(db_order)
                        if order is not None:
                            mark_notified(order)
                        await notify_session.flush()
        except Exception as exc:
            logger.warning(
                "[%s] Could not deliver immediate credit notification for order %s: %s",
                request_id,
                n_order_id,
                exc,
            )

    if admin_alert_info:
        try:
            from bot import texts
            from bot.keyboards.notifications import get_admin_finances_keyboard
            from config.settings import get_settings
            from utils.telegram import safe_send_message

            admin_ids = get_settings().ADMIN_IDS
            alert_text = texts.ADMIN_ALERT_ORDER_REQUIRES_MANUAL_REVIEW.format(
                order_id=html.escape(str(admin_alert_info["order_id"])),
                amount_rub=html.escape(str(admin_alert_info["amount_rub"])),
                mismatch=html.escape(str(admin_alert_info["mismatch"])),
            )
            reply_markup = get_admin_finances_keyboard(str(admin_alert_info["order_id"]))
            for aid in admin_ids:
                try:
                    await safe_send_message(
                        bot,
                        aid,
                        alert_text,
                        reply_markup=reply_markup,
                        parse_mode="HTML",
                    )
                except Exception as exc:
                    logger.warning("Failed to send manual review alert to admin %s: %s", aid, exc)
        except Exception as exc:
            logger.warning("[%s] Failed to send admin alerts: %s", request_id, exc)

    if refund_alert_info:
        try:
            from bot.keyboards.notifications import get_admin_finances_keyboard
            from config.settings import get_settings
            from utils.telegram import safe_send_message

            service_labels = {
                "standard": "Стандарт",
                "white_internet": "Белый интернет",
                "topup": "Пополнение",
            }
            is_refund_on_canceled = (
                refund_alert_info["status"] == "canceled"
                and refund_alert_info.get("refund_received_on_canceled")
            )
            is_partial_refund = refund_alert_info["status"] == "partially_refunded"
            if is_refund_on_canceled:
                status_label = "Возврат по отменённому заказу"
                footer_line = (
                    "Требуется ручная проверка (заказ уже был отменён, доступ и баланс не изменялись; "
                    "автоматическое восстановление заблокировано)."
                )
            elif is_partial_refund:
                status_label = "Частичный возврат"
                footer_line = "Требуется ручная проверка (доступ и баланс не изменялись)."
            else:
                status_label = "Полный возврат"
                footer_line = "Доступ и баланс обновлены автоматически."
            refund_text = (
                "💸 <b>Возврат ЮKassa</b>\n"
                f"Заказ: <code>{refund_alert_info['order_id']}</code>\n"
                f"Пользователь ID: <code>{refund_alert_info['user_id']}</code>\n"
                f"Услуга: {service_labels.get(refund_alert_info['service_type'], refund_alert_info['service_type'])}\n"
                f"Сумма: {refund_alert_info['amount_rub']} ₽ ({status_label})\n"
                f"{footer_line}"
            )
            refund_reply_markup = get_admin_finances_keyboard(
                str(refund_alert_info["order_id"])
            )
            for aid in get_settings().ADMIN_IDS:
                try:
                    await safe_send_message(
                        bot,
                        aid,
                        refund_text,
                        reply_markup=refund_reply_markup,
                        parse_mode="HTML",
                    )
                except Exception as exc:
                    logger.warning("Failed to send refund notice to admin %s: %s", aid, exc)
        except Exception as exc:
            logger.warning("[%s] Failed to send refund admin notice: %s", request_id, exc)


async def reprocess_webhook_inbox_row(
    session,
    bot,
    row: WebhookInbox,
) -> tuple[bool, str, str | None, dict | None]:
    """Synchronously reprocess a saved WebhookInbox row inside an active DB session."""
    from config.enums import WebhookInboxStatus

    if getattr(row, "provider", None) != "yookassa":
        raise ValueError("Unsupported webhook provider")
    payload = getattr(row, "payload", None)
    event, obj, _provider_obj_id, _pay_ext_id = _validate_webhook_payload(payload)
    request_id = f"retry-{getattr(row, 'id', '0')}"

    try:
        (
            order,
            inbox_status,
            notify_order_info,
            admin_alert_info,
            refund_alert_info,
        ) = await _process_yookassa_payload_in_session(
            session,
            payload,
            event=event,
            request_id=request_id,
            is_existing_event=True,
        )
    except Exception as exc:
        logger.exception(
            "[%s] Failed to reprocess WebhookInbox row %s",
            request_id,
            getattr(row, "id", None),
        )
        row.status = WebhookInboxStatus.DEAD.value
        return False, WebhookInboxStatus.DEAD.value, f"{type(exc).__name__}: {exc}", None

    if inbox_status != WebhookInboxStatus.SUCCEEDED.value:
        row.status = WebhookInboxStatus.DEAD.value
        reason = (
            "amount_mismatch"
            if order is not None and getattr(order, "_webhook_amount_mismatch", False)
            else "order_not_found"
        )
        return False, WebhookInboxStatus.DEAD.value, reason, None

    row.status = WebhookInboxStatus.SUCCEEDED.value
    metadata = obj.get("metadata") or {}
    public_order_id = metadata.get("order_id")
    if public_order_id and not getattr(row, "public_order_id", None):
        row.public_order_id = str(public_order_id)

    notify_ctx = (
        {
            "request_id": request_id,
            "order": order,
            "notify_order_info": notify_order_info,
            "admin_alert_info": admin_alert_info,
            "refund_alert_info": refund_alert_info,
        }
        if bot is not None and (notify_order_info or admin_alert_info or refund_alert_info)
        else None
    )
    return True, WebhookInboxStatus.SUCCEEDED.value, None, notify_ctx


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
    max_body_bytes = 262144
    if request.content_length is not None and request.content_length > max_body_bytes:
        return web.Response(status=413, text="Payload too large")
    raw_body_bytes: bytes | None = None
    if getattr(request, "content", None) is not None:
        chunks: list[bytes] = []
        total_bytes = 0
        async for chunk in request.content.iter_chunked(65536):
            total_bytes += len(chunk)
            if total_bytes > max_body_bytes:
                return web.Response(status=413, text="Payload too large")
            chunks.append(chunk)
        raw_body_bytes = b"".join(chunks)
    try:
        payload = (
            json.loads(raw_body_bytes.decode("utf-8"))
            if raw_body_bytes is not None
            else await request.json()
        )
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

            (
                order,
                inbox_status,
                notify_order_info,
                admin_alert_info,
                refund_alert_info,
            ) = await _process_yookassa_payload_in_session(
                session,
                payload,
                event=event,
                request_id=request_id,
                is_existing_event=bool(existing_event),
            )

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

    bot = request.app.get("bot")
    await _dispatch_yookassa_notifications(
        bot,
        request_id=request_id,
        order=order,
        notify_order_info=notify_order_info,
        admin_alert_info=admin_alert_info,
        refund_alert_info=refund_alert_info,
    )
    if inbox_status == WebhookInboxStatus.PENDING.value:
        return web.Response(status=503, text="order_pending_retry")
    return web.Response(status=200, text="OK")


_healthcheck_cache: tuple[float, int, str] | None = None
_HEALTHCHECK_CACHE_TTL = 5.0  # seconds
_healthcheck_lock: asyncio.Lock | None = None
_healthcheck_lock_loop: asyncio.AbstractEventLoop | None = None


def _get_healthcheck_lock() -> asyncio.Lock:
    global _healthcheck_lock, _healthcheck_lock_loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None
    if _healthcheck_lock is None or _healthcheck_lock_loop is not current_loop:
        _healthcheck_lock = asyncio.Lock()
        _healthcheck_lock_loop = current_loop
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

    # Register standard VLESS subscription feed routes
    from bot.handlers.vless_web import setup_vless_web_routes
    setup_vless_web_routes(app)

    # Register Telegram Mini App frontend and admin API routes
    from bot.miniapp import setup_miniapp_routes
    setup_miniapp_routes(app)
