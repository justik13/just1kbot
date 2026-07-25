import logging
import re
from decimal import Decimal
from typing import Optional

from aioyookassa import YooKassa
from aioyookassa.types.payment import PaymentAmount, Confirmation
from aioyookassa.types.enum import ConfirmationType
from aioyookassa.types.params import CreatePaymentParams

from config.settings import get_settings

logger = logging.getLogger(__name__)

_client: Optional[YooKassa] = None

# Паттерн для валидации email.
# Отсекаем фиктивные адреса вида {telegram_id}@receipt.local.
_EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
)

_BLOCKED_EMAIL_DOMAINS = {
    "receipt.local",
    "example.com",
    "example.org",
    "example.net",
    "test.com",
    "localhost",
}


def _is_valid_receipt_email(email: str) -> bool:
    """
    Проверяет, что email подходит для отправки чека.
    Отсекаем:
    - пустые и None;
    - невалидные по формату;
    - фиктивные домены (receipt.local и т.д.).
    """
    if not email or not isinstance(email, str):
        return False

    email = email.strip().lower()

    if not _EMAIL_REGEX.match(email):
        return False

    domain = email.rsplit("@", 1)[-1]

    if domain in _BLOCKED_EMAIL_DOMAINS:
        return False

    return True


def _get_client() -> YooKassa:
    global _client

    if _client is None:
        settings = get_settings()

        _client = YooKassa(
            api_key=settings.YOOKASSA_SECRET_KEY,
            shop_id=settings.YOOKASSA_SHOP_ID,
        )

        logger.info("[YooKassa] aioyookassa client initialized")

    return _client


async def close_yookassa_client() -> None:
    global _client

    if _client is not None:
        try:
            await _client.close()
        except Exception as e:
            logger.warning("[YooKassa] Error closing client: %s", e)
        finally:
            _client = None
            logger.info("[YooKassa] Client closed")


def _payment_to_dict(payment) -> Optional[dict]:
    if payment is None:
        return None

    try:
        data = payment.model_dump(mode="json", by_alias=True)
    except AttributeError:
        try:
            data = payment.dict(by_alias=True)
        except Exception:
            return None
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    confirmation = data.get("confirmation")

    if isinstance(confirmation, dict):
        url = confirmation.get("confirmation_url") or confirmation.get("url")

        if url:
            confirmation["confirmation_url"] = url

    amount = data.get("amount")

    if isinstance(amount, dict) and "value" in amount:
        amount["value"] = str(amount["value"])

    status = data.get("status")

    if status is not None:
        data["status"] = str(status).lower()

    return data


class YooKassaService:
    @staticmethod
    async def create_payment(
        amount: Decimal,
        currency: str = "RUB",
        description: str = "",
        return_url: str = "",
        metadata: Optional[dict] = None,
        receipt_email: str = "",
    ) -> Optional[dict]:
        client = _get_client()
        settings = get_settings()

        try:
            params_kwargs = dict(
                amount=PaymentAmount(value=str(amount), currency=currency),
                confirmation=Confirmation(
                    type=ConfirmationType.REDIRECT,
                    return_url=return_url,
                ),
                description=description,
                metadata=metadata,
                capture=True,
            )

            # Чеки отправляются только если:
            # 1. YOOKASSA_RECEIPTS_ENABLED = true в .env;
            # 2. receipt_email валидный и не фиктивный.
            #
            # Фиктивные адреса вида {telegram_id}@receipt.local
            # больше НЕ отправляются в YooKassa.
            if (
                settings.YOOKASSA_RECEIPTS_ENABLED
                and receipt_email
                and _is_valid_receipt_email(receipt_email)
            ):
                params_kwargs["receipt"] = {
                    "customer": {"email": receipt_email},
                    "items": [
                        {
                            "description": description or "Подписка VPN",
                            "quantity": "1.00",
                            "amount": {
                                "value": str(amount),
                                "currency": currency,
                            },
                            "vat_code": 6,
                        }
                    ],
                }
            elif settings.YOOKASSA_RECEIPTS_ENABLED and receipt_email:
                logger.warning(
                    "Receipt email rejected as invalid/fake: %s. "
                    "Payment will be created without receipt.",
                    receipt_email[:50],
                )

            params = CreatePaymentParams(**params_kwargs)

            payment = await client.payments.create_payment(params)

            data = _payment_to_dict(payment)

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
            payment = await client.payments.get_payment(payment_id)
            return _payment_to_dict(payment)
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
            payment = await client.payments.cancel_payment(payment_id)

            data = _payment_to_dict(payment)

            if data:
                logger.info("YooKassa payment cancelled: id=%s", payment_id)

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
            "payment.refunded": "CHARGEBACKED",
            "refund.succeeded": "CHARGEBACKED",
            "payment.waiting_for_capture": "WAITING_FOR_CAPTURE",
        }

        return mapping.get(event, event.upper())