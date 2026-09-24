"""YooKassa implementation of BasePaymentGateway."""

import logging
import os
from decimal import Decimal

from config.settings import get_settings
from integrations.payment_gateways.base import (
    BasePaymentGateway,
    PaymentInvoice,
    PaymentStatusResult,
    WebhookResult,
)
from services.yookassa_service import YooKassaService

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
                "YooKassa payment creation failed for order %s: kind=%s, status=%s",
                order_id,
                result.error_kind,
                result.status_code,
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
        event = payload.get("event", "")
        obj = payload.get("object", {})

        status = obj.get("status", "")
        metadata = obj.get("metadata", {}) or {}
        order_id = metadata.get("order_id")

        amount_val = obj.get("amount", {}).get("value")
        amount_rub = Decimal(str(amount_val)) if amount_val is not None else None

        related_external_id = None
        if event == "refund.succeeded":
            external_id = obj.get("id", "")
            related_external_id = obj.get("payment_id")
            is_paid = False
            is_refunded = True
        elif event == "payment.succeeded":
            external_id = obj.get("id", "")
            is_paid = True
            is_refunded = False
        else:
            external_id = obj.get("id", "")
            is_refunded = status == "refunded"
            is_paid = status == "succeeded" and not is_refunded
            if is_refunded:
                related_external_id = obj.get("payment_id")

        return WebhookResult(
            order_id=order_id,
            is_paid=is_paid,
            is_refunded=is_refunded,
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
                "YooKassa status check failed for payment %s: kind=%s",
                external_id,
                result.error_kind,
            )
            return PaymentStatusResult(
                is_paid=False,
                is_refunded=False,
                is_canceled=False,
                status_str="unknown",
            )

        data = result.value
        status = data.get("status", "")
        return PaymentStatusResult(
            is_paid=(status == "succeeded"),
            is_refunded=(status == "refunded"),
            is_canceled=(status == "canceled"),
            status_str=status,
        )
