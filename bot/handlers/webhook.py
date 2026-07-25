import logging
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from aiohttp import web

from bot.middlewares.correlation import set_request_id
from database.connection import session_scope
from services.audit_service import AuditService
from services.payment_service import PaymentService
from services.payment_service.alerts import (
    _send_payment_not_found_alert_now,
)
from services.yookassa_service import YooKassaService

logger = logging.getLogger(__name__)

WEBHOOK_MAX_AGE_SECONDS = 300

YOOKASSA_IP_RANGES = [
    "185.71.76.0/27",
    "185.71.77.0/27",
    "77.75.153.0/25",
    "77.75.154.128/25",
    "77.75.156.0/25",
    "77.75.156.128/25",
    "2a02:5180::/32",
]


def _is_yookassa_ip(ip: str) -> bool:
    import ipaddress

    try:
        client_ip = ipaddress.ip_address(ip)
        for cidr in YOOKASSA_IP_RANGES:
            if client_ip in ipaddress.ip_network(cidr):
                return True
        return False
    except ValueError:
        return False


def _get_real_ip(request: web.Request) -> str:
    """
    За nginx request.remote == 127.0.0.1.
    Читаем X-Real-IP / X-Forwarded-For, которые ставит nginx.
    """
    real_ip = request.headers.get("X-Real-IP", "").strip()
    if real_ip:
        return real_ip
    forwarded = request.headers.get("X-Forwarded-For", "").strip()
    if forwarded:
        first_ip = forwarded.split(",")[0].strip()
        if first_ip:
            return first_ip
    return request.remote or ""


def _is_recent_timestamp(
    created_at: str,
    max_age_seconds: int = WEBHOOK_MAX_AGE_SECONDS,
) -> bool:
    # ──────────────────────────────────────────────────────
    # ИСПРАВЛЕНО: отсутствие created_at → отклоняем.
    # Раньше возвращали True, что теоретически позволяло
    # replay-атаку с отсутствующим timestamp.
    # ──────────────────────────────────────────────────────
    if not created_at or not isinstance(created_at, str):
        return False

    created_at = created_at.strip()
    if not created_at:
        return False

    try:
        ts = float(created_at)
        if ts > 1e12:
            ts = ts / 1000.0
        age = time.time() - ts
        return age <= max_age_seconds
    except (ValueError, TypeError, OverflowError):
        pass

    try:
        dt = datetime.fromisoformat(
            created_at.replace("Z", "+00:00")
        )
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = (
            datetime.now(timezone.utc) - dt
        ).total_seconds()
        return age <= max_age_seconds
    except (ValueError, TypeError, OverflowError):
        pass

    return False


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
    }
    api_status = api_status_map.get(
        api_status_raw, api_status_raw.upper()
    )

    if api_status != normalized_status:
        logger.warning(
            "Stale webhook status mismatch: "
            "callback=%s, api=%s, payment=%s",
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
    api_amount_str = (
        api_amount.get("value")
        if isinstance(api_amount, dict)
        else None
    )

    if callback_amount_str and api_amount_str:
        cb_decimal = _safe_decimal(callback_amount_str)
        api_decimal = _safe_decimal(api_amount_str)
        if cb_decimal is not None and api_decimal is not None:
            if cb_decimal != api_decimal:
                logger.warning(
                    "Stale webhook amount mismatch: "
                    "callback=%s, api=%s, payment=%s",
                    callback_amount_str,
                    api_amount_str,
                    transaction_id,
                )
                return False, None

    # ──────────────────────────────────────────────────────
    # ИСПРАВЛЕНО: сравниваем payload ВСЕГДА, даже если
    # один из них пустой. Раньше проверка пропускалась
    # при пустом callback_payload, что позволяло
    # поддельный webhook без metadata.
    # ──────────────────────────────────────────────────────
    callback_metadata = webhook_object.get("metadata") or {}
    callback_payload = callback_metadata.get("payload", "")

    api_metadata = api_data.get("metadata") or {}
    api_payload = api_metadata.get("payload", "")

    if callback_payload != api_payload:
        logger.warning(
            "Stale webhook payload mismatch: "
            "callback=%s, api=%s, payment=%s",
            callback_payload,
            api_payload,
            transaction_id,
        )
        return False, None

    return True, api_data


async def yookassa_webhook_handler(
    request: web.Request,
) -> web.Response:
    request_id = uuid.uuid4().hex[:8]
    set_request_id(request_id)

    transaction_id = None
    status = None

    try:
        peer_ip = _get_real_ip(request)
        if peer_ip and not _is_yookassa_ip(peer_ip):
            logger.warning(
                "[%s] Webhook BLOCKED from unknown IP: %s",
                request_id,
                peer_ip,
            )
            return web.Response(status=403, text="Forbidden")

        try:
            raw_data = await request.json()
        except Exception as e:
            logger.error(
                "[%s] Failed to parse webhook JSON: %s",
                request_id,
                e,
            )
            return web.Response(
                status=400, text="Invalid JSON"
            )

        event = raw_data.get("event", "")
        webhook_object = raw_data.get("object", {})
        transaction_id = webhook_object.get("id")
        status = YooKassaService.normalize_webhook_event(event)

        if not transaction_id:
            logger.warning(
                "[%s] Webhook missing payment ID.",
                request_id,
            )
            return web.Response(
                status=400, text="Missing payment ID"
            )

        valid_statuses = {
            "CONFIRMED",
            "CANCELED",
            "CHARGEBACKED",
            "WAITING_FOR_CAPTURE",
        }
        if status not in valid_statuses:
            logger.warning(
                "[%s] Unknown webhook status: %s (payment=%s)",
                request_id,
                status,
                transaction_id,
            )
            return web.Response(
                status=400, text="Invalid status"
            )

        if status == "WAITING_FOR_CAPTURE":
            logger.info(
                "[%s] Webhook received: payment=%s, "
                "status=WAITING_FOR_CAPTURE (authorized, "
                "waiting for auto-capture). Returning 200 OK.",
                request_id,
                transaction_id,
            )
            return web.Response(status=200, text="OK")

        callback_amount_str = None
        callback_currency = None
        amount_obj = webhook_object.get("amount")
        if isinstance(amount_obj, dict):
            callback_amount_str = amount_obj.get("value")
            callback_currency = amount_obj.get("currency")

        callback_amount = _safe_decimal(callback_amount_str)

        created_at = webhook_object.get("created_at")
        if created_at and not _is_recent_timestamp(created_at):
            logger.info(
                "[%s] Stale webhook detected, verifying "
                "via API. payment=%s",
                request_id,
                transaction_id,
            )
            stale_ok, stale_api_data = (
                await _verify_stale_webhook_via_api(
                    webhook_object,
                    status,
                    transaction_id,
                )
            )
            if not stale_ok:
                logger.warning(
                    "[%s] Stale webhook rejected: payment=%s",
                    request_id,
                    transaction_id,
                )
                return web.Response(
                    status=400,
                    text="Stale webhook unverified",
                )
            if stale_api_data:
                api_amount = stale_api_data.get("amount", {})
                if isinstance(api_amount, dict):
                    if api_amount.get("value"):
                        callback_amount = _safe_decimal(
                            api_amount["value"]
                        )
                    if api_amount.get("currency"):
                        callback_currency = api_amount[
                            "currency"
                        ]

        metadata = webhook_object.get("metadata") or {}
        payload = metadata.get("payload", "")

        logger.info(
            "[%s] Webhook received: payment=%s, status=%s, "
            "amount=%s, currency=%s",
            request_id,
            transaction_id,
            status,
            callback_amount,
            callback_currency,
        )

        async with session_scope() as session:
            try:
                await AuditService.log_action(
                    session,
                    admin_id=0,
                    action="YOOKASSA_CALLBACK",
                    target_type="Payment",
                    target_id=None,
                    details=(
                        f"[{request_id}] "
                        f"payment={transaction_id}, "
                        f"status={status}, "
                        f"amount={callback_amount}"
                    ),
                )
            except Exception as e:
                logger.error(
                    "[%s] Failed to log audit: %s",
                    request_id,
                    e,
                )

            success, result_code = (
                await PaymentService.handle_yookassa_callback(
                    transaction_id=transaction_id,
                    status=status,
                    payload=payload,
                    callback_amount=callback_amount,
                    callback_currency=callback_currency,
                )
            )

            if success:
                if result_code == "not_found":
                    logger.warning(
                        "[%s] Payment not found: %s",
                        request_id,
                        transaction_id,
                    )
                    try:
                        await _send_payment_not_found_alert_now(
                            {
                                "transaction_id": transaction_id,
                                "status": status,
                                "source": "yookassa_webhook",
                            }
                        )
                    except Exception:
                        pass
                    return web.Response(
                        status=404, text="Payment not found"
                    )
                return web.Response(status=200, text="OK")
            else:
                if result_code == "not_found":
                    try:
                        await _send_payment_not_found_alert_now(
                            {
                                "transaction_id": transaction_id,
                                "status": status,
                                "source": "yookassa_webhook",
                            }
                        )
                    except Exception:
                        pass
                    return web.Response(
                        status=404, text="Payment not found"
                    )
                elif result_code in (
                    "amount_mismatch",
                    "payload_mismatch",
                    "manual_review",
                    "refunded",
                ):
                    return web.Response(status=200, text="OK")
                elif result_code == "error":
                    return web.Response(
                        status=500, text="Processing failed"
                    )
                else:
                    return web.Response(
                        status=500, text="Unknown error"
                    )

    except Exception as e:
        logger.error(
            "[%s] Webhook error: %s",
            request_id,
            e,
            exc_info=True,
        )
        return web.Response(
            status=500, text="Internal server error"
        )


async def healthcheck_handler(
    request: web.Request,
) -> web.Response:
    return web.Response(status=200, text="OK")


def setup_webhook_routes(app: web.Application):
    app.router.add_post(
        "/webhook/yookassa", yookassa_webhook_handler
    )
    app.router.add_get("/health", healthcheck_handler)
    logger.info(
        "YooKassa webhook route registered: "
        "POST /webhook/yookassa"
    )
    logger.info(
        "Healthcheck endpoint registered: GET /health"
    )