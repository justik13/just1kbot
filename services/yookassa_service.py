import logging
from decimal import Decimal
from typing import Optional

from aioyookassa import Client as AioYooKassaClient

from config.settings import get_settings

logger = logging.getLogger(__name__)

_client: Optional[AioYooKassaClient] = None


def _get_client() -> AioYooKassaClient:
    global _client
    if _client is None:
        settings = get_settings()
        _client = AioYooKassaClient(
            shop_id=settings.YOOKASSA_SHOP_ID,
            secret_key=settings.YOOKASSA_SECRET_KEY,
        )
        logger.info("[YooKassa] aioyookassa client initialized")
    return _client


async def close_yookassa_client() -> None:
    global _client
    if _client is not None:
        try:
            close_fn = getattr(_client, "close", None)
            if close_fn is not None:
                result = close_fn()
                if hasattr(result, "__await__"):
                    await result
        except Exception as e:
            logger.warning("[YooKassa] Error closing client: %s", e)
        finally:
            _client = None
            logger.info("[YooKassa] Client closed")


def _to_dict(obj) -> Optional[dict]:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj
    for method_name in ("model_dump", "dict", "to_dict"):
        fn = getattr(obj, method_name, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass
    result: dict = {}
    for attr in (
        "id", "status", "description", "created_at",
        "paid_at", "expires_at", "metadata",
    ):
        val = getattr(obj, attr, None)
        if val is not None:
            result[attr] = val
    amount = getattr(obj, "amount", None)
    if amount is not None:
        if isinstance(amount, dict):
            result["amount"] = amount
        else:
            result["amount"] = {
                "value": str(getattr(amount, "value", "")),
                "currency": str(getattr(amount, "currency", "RUB")),
            }
    confirmation = getattr(obj, "confirmation", None)
    if confirmation is not None:
        if isinstance(confirmation, dict):
            result["confirmation"] = confirmation
        else:
            result["confirmation"] = {
                "type": str(getattr(confirmation, "type", "redirect")),
                "confirmation_url": str(
                    getattr(confirmation, "confirmation_url", "")
                ),
            }
    return result if result else None


class YooKassaService:

    @staticmethod
    async def create_payment(
        amount: Decimal,
        currency: str = "RUB",
        description: str = "",
        return_url: str = "",
        metadata: Optional[dict] = None,
    ) -> Optional[dict]:
        client = _get_client()
        body: dict = {
            "amount": {"value": str(amount), "currency": currency},
            "description": description,
            "confirmation": {
                "type": "redirect",
                "return_url": return_url,
            },
        }
        if metadata:
            body["metadata"] = metadata
        try:
            payment = await client.create_payment(body)
            data = _to_dict(payment)
            if data:
                logger.info(
                    "YooKassa payment created: id=%s, status=%s",
                    data.get("id"),
                    data.get("status"),
                )
            return data
        except Exception as e:
            logger.error(
                "YooKassa create_payment exception: %s",
                e,
                exc_info=True,
            )
            return None

    @staticmethod
    async def get_payment(payment_id: str) -> Optional[dict]:
        client = _get_client()
        try:
            payment = await client.get_payment(payment_id)
            return _to_dict(payment)
        except Exception as e:
            logger.error(
                "YooKassa get_payment exception: %s",
                e,
                exc_info=True,
            )
            return None

    @staticmethod
    async def cancel_payment(
        payment_id: str,
        reason: str = "",
    ) -> Optional[dict]:
        client = _get_client()
        try:
            payment = await client.cancel_payment(payment_id)
            data = _to_dict(payment)
            if data:
                logger.info(
                    "YooKassa payment cancelled: id=%s",
                    payment_id,
                )
            return data
        except Exception as e:
            logger.error(
                "YooKassa cancel_payment exception: %s",
                e,
                exc_info=True,
            )
            return None

    @staticmethod
    def normalize_webhook_event(event: str) -> str:
        mapping = {
            "payment.succeeded": "CONFIRMED",
            "payment.canceled": "CANCELED",
            "refund.succeeded": "CHARGEBACKED",
        }
        return mapping.get(event, event.upper())