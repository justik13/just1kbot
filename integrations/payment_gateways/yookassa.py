"""YooKassa implementation of BasePaymentGateway."""

import logging
import os
from decimal import Decimal, InvalidOperation

from config.settings import get_settings
from integrations.payment_gateways.base import (
    BasePaymentGateway,
    PaymentCreationAmbiguousError,
    PaymentInvoice,
    PaymentStatusResult,
    WebhookResult,
)
from services.yookassa_service import YooKassaErrorKind, YooKassaService

logger = logging.getLogger(__name__)


class YooKassaGateway(BasePaymentGateway):
    name: str = "yookassa"

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
            raw_template = "https://t.me/{bot_username}"
            try:
                settings = get_settings()
                raw_template = getattr(settings, "YOOKASSA_RETURN_URL", raw_template) or raw_template
            except Exception:
                pass
            resolved_bot_user = bot_username or os.getenv("BOT_USERNAME")
            if not resolved_bot_user:
                try:
                    from aiogram import Bot
                    current_bot = Bot.get_current()
                    if current_bot:
                        me = getattr(current_bot, "_me", None)
                        if not me:
                            me = await current_bot.get_me()
                        if me and me.username:
                            resolved_bot_user = me.username
                except Exception:
                    pass
            if not resolved_bot_user:
                try:
                    settings = get_settings()
                    resolved_bot_user = getattr(settings, "BOT_USERNAME", None)
                except Exception:
                    pass
            if not resolved_bot_user:
                resolved_bot_user = "bot"
            resolved_bot_user = str(resolved_bot_user).lstrip("@")
            if "{bot_username}" in raw_template:
                resolved_return_url = raw_template.format(bot_username=resolved_bot_user)
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
        result = await YooKassaService.create_payment_result(
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
                # Timeout / 5xx / unreadable response: YooKassa may have
                # created the payment anyway. Report it as unknown so the
                # caller keeps the order and can still be settled by webhook.
                # Machine-readable token (no prose): full diagnostics are in
                # the log line above, and user wording lives in bot/texts.
                raise PaymentCreationAmbiguousError(
                    f"payment_creation_ambiguous:{result.error_kind or 'unknown'}"
                )
            raise RuntimeError(
                f"YooKassa payment creation error: {result.error_kind or 'unknown'}"
            )

        data = result.value
        external_id = data.get("id", "")
        confirmation = data.get("confirmation", {})
        confirmation_url = confirmation.get("confirmation_url", "")

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
        has_invalid_currency = bool(currency is not None and currency != "RUB")
        if amount_val is not None and not has_invalid_currency:
            try:
                parsed = Decimal(str(amount_val))
                if parsed > 0:
                    amount_rub = parsed
            except (InvalidOperation, ValueError, TypeError):
                amount_rub = None

        related_external_id = None
        is_canceled = False
        if event == "refund.succeeded":
            external_id = obj.get("id", "")
            related_external_id = obj.get("payment_id")
            is_paid = False
            is_refunded = True
        elif event == "payment.succeeded":
            external_id = obj.get("id", "")
            is_paid = not has_invalid_currency and (amount_val is None or amount_rub is not None)
            is_refunded = False
        elif event == "payment.canceled":
            external_id = obj.get("id", "")
            is_paid = False
            is_refunded = False
            is_canceled = True
        else:
            external_id = obj.get("id", "")
            is_paid = False
            is_refunded = False

        return WebhookResult(
            order_id=order_id,
            is_paid=is_paid,
            is_refunded=is_refunded,
            is_canceled=is_canceled,
            external_id=external_id,
            amount_rub=amount_rub,
            event_type=event or status,
            related_external_id=related_external_id,
        )

    async def check_payment_status(
        self,
        external_id: str,
    ) -> PaymentStatusResult:
        result = await YooKassaService.get_payment_result(external_id)
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
        return PaymentStatusResult(
            is_paid=(status == "succeeded" and amount_rub is not None and amount_rub > 0),
            is_refunded=(status == "refunded"),
            is_canceled=(status == "canceled"),
            status_str=status,
            amount_rub=amount_rub,
            cancellation_reason=cancellation_reason,
            is_temporary_error=False,
        )
