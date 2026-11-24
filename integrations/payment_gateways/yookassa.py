"""YooKassa implementation of BasePaymentGateway."""

import asyncio
from dataclasses import dataclass
import logging
import os
import time
from decimal import Decimal, InvalidOperation

import aiohttp

from config.settings import get_settings
from integrations.payment_gateways.base import (
    BasePaymentGateway,
    PaymentCreationAmbiguousError,
    PaymentInvoice,
    PaymentStatusResult,
    WebhookResult,
)
from services.yookassa_service import (
    CHECK_STATUS_TIMEOUT,
    CREATE_PAYMENT_TIMEOUT,
    YooKassaErrorKind,
    YooKassaResult,
    _get_client_session,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class YooKassaWebhookResult(WebhookResult):
    mismatch_reason: str | None = None


@dataclass(frozen=True)
class YooKassaPaymentStatusResult(PaymentStatusResult):
    mismatch_reason: str | None = None


class YooKassaGateway(BasePaymentGateway):
    name: str = "yookassa"
    API = "https://api.yookassa.ru/v3"

    @classmethod
    async def _request(
        cls,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        idempotency_key: str | None = None,
        ambiguous_on_failure: bool = False,
        timeout: aiohttp.ClientTimeout | None = None,
    ) -> YooKassaResult[dict]:
        can_retry = (method == "GET") or bool(idempotency_key)
        max_attempts = 3 if can_retry else 1
        last_res: YooKassaResult[dict] | None = None
        for attempt in range(max_attempts):
            last_res = await cls._request_once(
                method,
                path,
                payload=payload,
                idempotency_key=idempotency_key,
                ambiguous_on_failure=ambiguous_on_failure,
                timeout=timeout,
            )
            if last_res.ok or not last_res.retryable or attempt + 1 >= max_attempts:
                return last_res
            await asyncio.sleep(0.5 * (2**attempt))
        return last_res

    @classmethod
    async def _request_once(
        cls,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        idempotency_key: str | None = None,
        ambiguous_on_failure: bool = False,
        timeout: aiohttp.ClientTimeout | None = None,
    ) -> YooKassaResult[dict]:
        settings = get_settings()
        shop = settings.YOOKASSA_SHOP_ID
        secret = settings.YOOKASSA_SECRET_KEY
        headers = {
            "Accept": "application/json",
            "Authorization": aiohttp.encode_basic_auth(shop, secret),
        }
        if idempotency_key:
            if len(idempotency_key) > 64:
                return YooKassaResult(
                    False, error_kind=YooKassaErrorKind.VALIDATION_FAILED
                )
            headers["Idempotence-Key"] = idempotency_key
        start = time.monotonic()
        req_timeout = timeout or CREATE_PAYMENT_TIMEOUT
        try:
            client = await _get_client_session()
            async with client.request(
                method,
                cls.API + path,
                json=payload,
                headers=headers,
                timeout=req_timeout,
            ) as response:
                code = response.status
                ambiguous_read = ambiguous_on_failure and not (400 <= code < 500)
                try:
                    data = await response.json(content_type=None)
                except asyncio.CancelledError:
                    raise
                except asyncio.TimeoutError:
                    logger.warning(
                        "YooKassa HTTP %s %s JSON decode timeout (status=%s, %.0f ms)",
                        method,
                        path,
                        code,
                        (time.monotonic() - start) * 1000,
                    )
                    return YooKassaResult(
                        False,
                        error_kind=YooKassaErrorKind.TIMEOUT,
                        status_code=code,
                        retryable=True,
                        ambiguous=ambiguous_read,
                    )
                except aiohttp.ClientError as exc:
                    logger.warning(
                        "YooKassa HTTP %s %s client error on read (status=%s, error=%s, %.0f ms)",
                        method,
                        path,
                        code,
                        exc,
                        (time.monotonic() - start) * 1000,
                    )
                    return YooKassaResult(
                        False,
                        error_kind=YooKassaErrorKind.NETWORK_ERROR,
                        status_code=code,
                        retryable=True,
                        ambiguous=ambiguous_read,
                    )
                except (ValueError, TypeError):
                    logger.warning(
                        "YooKassa HTTP %s %s invalid JSON body (status=%s, %.0f ms)",
                        method,
                        path,
                        code,
                        (time.monotonic() - start) * 1000,
                    )
                    return YooKassaResult(
                        False,
                        error_kind=YooKassaErrorKind.INVALID_RESPONSE,
                        status_code=code,
                        retryable=200 <= code < 300 or code >= 500,
                        ambiguous=ambiguous_read,
                    )
                except Exception as exc:
                    logger.warning(
                        "YooKassa HTTP %s %s unexpected error on read (status=%s, error=%s, %.0f ms)",
                        method,
                        path,
                        code,
                        exc,
                        (time.monotonic() - start) * 1000,
                    )
                    return YooKassaResult(
                        False,
                        error_kind=YooKassaErrorKind.UNKNOWN,
                        status_code=code,
                        retryable=False,
                        ambiguous=ambiguous_read,
                    )
                elapsed_ms = (time.monotonic() - start) * 1000
                if 200 <= code < 300:
                    if isinstance(data, dict):
                        logger.info(
                            "YooKassa HTTP %s %s -> %s (%.0f ms, idempotency=%s)",
                            method,
                            path,
                            code,
                            elapsed_ms,
                            idempotency_key[:8] if idempotency_key else "-",
                        )
                        return YooKassaResult(True, value=data, status_code=code)
                    logger.warning(
                        "YooKassa HTTP %s %s returned non-dict JSON (status=%s, %.0f ms)",
                        method,
                        path,
                        code,
                        elapsed_ms,
                    )
                    return YooKassaResult(
                        False,
                        error_kind=YooKassaErrorKind.INVALID_RESPONSE,
                        status_code=code,
                        retryable=True,
                        ambiguous=ambiguous_read,
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
                logger.warning(
                    "YooKassa HTTP %s %s failed: status=%s, kind=%s (%.0f ms)",
                    method,
                    path,
                    code,
                    kind.value,
                    elapsed_ms,
                )
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
            logger.warning(
                "YooKassa HTTP %s %s request timeout (%.0f ms)",
                method,
                path,
                (time.monotonic() - start) * 1000,
            )
            return YooKassaResult(
                False,
                error_kind=YooKassaErrorKind.TIMEOUT,
                retryable=True,
                ambiguous=ambiguous_on_failure,
            )
        except aiohttp.ClientError as exc:
            logger.warning(
                "YooKassa HTTP %s %s network connection error: %s (%.0f ms)",
                method,
                path,
                exc,
                (time.monotonic() - start) * 1000,
            )
            return YooKassaResult(
                False,
                error_kind=YooKassaErrorKind.NETWORK_ERROR,
                retryable=True,
                ambiguous=ambiguous_on_failure,
            )
        except Exception as exc:
            logger.error(
                "YooKassa HTTP %s %s unexpected client failure: %s (%.0f ms)",
                method,
                path,
                exc,
                (time.monotonic() - start) * 1000,
            )
            return YooKassaResult(
                False,
                error_kind=YooKassaErrorKind.UNKNOWN,
                retryable=False,
                ambiguous=False,
            )

    @classmethod
    async def _create_payment_result(
        cls,
        payload: dict,
        *,
        idempotency_key: str,
        timeout: aiohttp.ClientTimeout | None = None,
    ) -> YooKassaResult[dict]:
        return await cls._request(
            "POST",
            "/payments",
            payload=payload,
            idempotency_key=idempotency_key,
            ambiguous_on_failure=True,
            timeout=timeout or CREATE_PAYMENT_TIMEOUT,
        )

    @classmethod
    async def _get_payment_result(
        cls,
        payment_id: str,
        timeout: aiohttp.ClientTimeout | None = None,
    ) -> YooKassaResult[dict]:
        return await cls._request(
            "GET",
            f"/payments/{payment_id}",
            timeout=timeout or CHECK_STATUS_TIMEOUT,
        )

    async def create_payment_url(
        self,
        *,
        order_id: str,
        amount_rub: Decimal,
        description: str,
        return_url: str | None = None,
        bot_username: str | None = None,
    ) -> PaymentInvoice:
        resolved_return_url = return_url
        if not resolved_return_url:
            raw_template = "https://t.me/{bot_username}?startapp=paid"
            try:
                settings = get_settings()
                raw_template = getattr(settings, "YOOKASSA_RETURN_URL", raw_template) or raw_template
            except Exception:
                pass
            resolved_bot_user = bot_username or os.getenv("BOT_USERNAME")
            if not resolved_bot_user:
                try:
                    settings = get_settings()
                    resolved_bot_user = getattr(settings, "BOT_USERNAME", None)
                except Exception:
                    pass
            if resolved_bot_user:
                resolved_bot_user = str(resolved_bot_user).strip().lstrip("@")
            if "{bot_username}" in raw_template:
                if resolved_bot_user:
                    resolved_return_url = raw_template.format(bot_username=resolved_bot_user)
                else:
                    resolved_return_url = "https://t.me"
            else:
                resolved_return_url = raw_template
        if not resolved_return_url:
            resolved_return_url = "https://t.me"

        payload = {
            "amount": {
                "value": f"{amount_rub:.2f}",
                "currency": "RUB",
            },
            "capture": True,
            "confirmation": {
                "type": "redirect",
                "return_url": resolved_return_url,
            },
            "description": description[:128],
            "metadata": {
                "order_id": order_id,
            },
        }

        # Idempotency key: order_id is a unique UUID
        idempotency_key = f"order_{order_id}"
        result = await self._create_payment_result(
            payload, idempotency_key=idempotency_key
        )
        if not result.ok or not result.value:
            logger.error(
                "YooKassa payment creation failed for order %s: kind=%s, status=%s, ambiguous=%s",
                order_id,
                result.error_kind,
                result.status_code,
                result.ambiguous,
            )
            if result.ambiguous:
                raise PaymentCreationAmbiguousError(
                    f"YooKassa payment creation ambiguous: {result.error_kind or 'unknown'}"
                )
            raise RuntimeError(
                f"YooKassa payment creation error: {result.error_kind or 'unknown'}"
            )

        data = result.value or {}
        external_id = (data.get("id") or "").strip()
        confirmation = data.get("confirmation") or {}
        confirmation_url = (confirmation.get("confirmation_url") or "").strip()

        if not external_id or not confirmation_url:
            raise PaymentCreationAmbiguousError(
                f"YooKassa payment response missing required id or confirmation_url: external_id={external_id!r}, url={confirmation_url!r}"
            )

        return PaymentInvoice(
            payment_url=confirmation_url,
            external_id=external_id,
        )

    async def parse_webhook(
        self,
        payload: dict,
        headers: dict | None = None,
    ) -> WebhookResult:
        payload = payload or {}
        event = payload.get("event", "")
        obj = payload.get("object") or {}

        status = obj.get("status", "")
        metadata = obj.get("metadata") or {}
        order_id = metadata.get("order_id")

        amount_obj = obj.get("amount") or {}
        amount_val = amount_obj.get("value")
        currency = amount_obj.get("currency")
        amount_rub = None
        if amount_val is not None and currency == "RUB":
            try:
                parsed = Decimal(str(amount_val))
                if parsed > 0:
                    amount_rub = parsed
            except (InvalidOperation, ValueError, TypeError):
                amount_rub = None

        related_external_id = None
        is_canceled = False
        mismatch_reason: str | None = None
        if event == "refund.succeeded":
            external_id = obj.get("id", "")
            related_external_id = obj.get("payment_id")
            is_paid = False
            is_refunded = (status == "succeeded")
        elif event == "payment.succeeded":
            external_id = obj.get("id", "")
            is_paid = (status == "succeeded" and amount_rub is not None and amount_rub > 0)
            is_refunded = False
            if status == "succeeded" and not is_paid:
                mismatch_reason = (
                    f"Invalid currency or amount on payment.succeeded: "
                    f"currency={currency!r}, value={amount_val!r}"
                )
        elif event == "payment.canceled":
            external_id = obj.get("id", "")
            is_paid = False
            is_refunded = False
            is_canceled = True
        else:
            external_id = obj.get("id", "")
            is_paid = False
            is_refunded = False

        return YooKassaWebhookResult(
            order_id=order_id,
            is_paid=is_paid,
            is_refunded=is_refunded,
            is_canceled=is_canceled,
            external_id=external_id,
            amount_rub=amount_rub,
            event_type=event or status,
            related_external_id=related_external_id,
            mismatch_reason=mismatch_reason,
        )

    async def check_payment_status(
        self,
        external_id: str,
    ) -> PaymentStatusResult:
        result = await self._get_payment_result(external_id)
        if not result.ok or not result.value:
            logger.warning(
                "YooKassa status check failed for payment %s: kind=%s, status=%s, retryable=%s",
                external_id,
                result.error_kind,
                result.status_code,
                result.retryable,
            )
            is_temporary = (
                result.error_kind in (
                    YooKassaErrorKind.TIMEOUT,
                    YooKassaErrorKind.NETWORK_ERROR,
                    YooKassaErrorKind.SERVER_ERROR,
                    YooKassaErrorKind.RATE_LIMITED,
                )
                or result.retryable
            )
            status_str = result.error_kind.value if result.error_kind else "unknown"
            if result.status_code == 404:
                status_str = "not_found"
            return PaymentStatusResult(
                is_paid=False,
                is_refunded=False,
                is_canceled=False,
                status_str=status_str,
                is_temporary_error=is_temporary,
            )

        data = result.value
        status = data.get("status", "")
        amount_obj = data.get("amount") or {}
        amount_val = amount_obj.get("value")
        currency = amount_obj.get("currency")
        amount_rub = None
        if amount_val is not None and currency == "RUB":
            try:
                parsed = Decimal(str(amount_val))
                if parsed > 0:
                    amount_rub = parsed
            except (InvalidOperation, ValueError, TypeError):
                amount_rub = None
        cancellation_details = data.get("cancellation_details") or {}
        cancellation_reason = (
            cancellation_details.get("reason")
            if isinstance(cancellation_details, dict)
            else None
        )
        refunded_obj = data.get("refunded_amount") or {}
        refunded_val = refunded_obj.get("value") if isinstance(refunded_obj, dict) else None
        refunded_currency = refunded_obj.get("currency") if isinstance(refunded_obj, dict) else None
        is_refunded = False
        mismatch_reason: str | None = None
        if status == "succeeded" and amount_rub is None:
            mismatch_reason = (
                f"Invalid currency or amount on payment status: "
                f"currency={currency!r}, value={amount_val!r}"
            )
        if refunded_val is not None:
            if refunded_currency is not None and refunded_currency != "RUB":
                is_refunded = True
                mismatch_reason = (
                    f"Non-RUB refunded_amount currency: {refunded_currency!r}"
                )
            else:
                try:
                    refunded_rub = Decimal(str(refunded_val))
                    if refunded_rub > 0:
                        # Treat any positive refund (partial or full) as refunded
                        # so pending-order reconciliation never grants full benefits.
                        is_refunded = True
                        if amount_rub is not None and refunded_rub < amount_rub:
                            mismatch_reason = (
                                f"Partial refund detected during status check: "
                                f"{refunded_rub} / {amount_rub} RUB"
                            )
                except (InvalidOperation, ValueError, TypeError):
                    is_refunded = False
        return YooKassaPaymentStatusResult(
            is_paid=(
                status == "succeeded"
                and not is_refunded
                and mismatch_reason is None
                and amount_rub is not None
                and amount_rub > 0
            ),
            is_refunded=is_refunded,
            is_canceled=(status == "canceled"),
            status_str=status,
            amount_rub=amount_rub,
            cancellation_reason=cancellation_reason,
            is_temporary_error=False,
            mismatch_reason=mismatch_reason,
        )
