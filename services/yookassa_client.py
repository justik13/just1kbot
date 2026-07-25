import logging
import socket
import uuid
from typing import Optional
from decimal import Decimal

import aiohttp

from config.settings import get_settings

logger = logging.getLogger(__name__)

_http_session: Optional[aiohttp.ClientSession] = None

YOOKASSA_API_BASE = "https://api.yookassa.ru/v3"


async def _get_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None:
        timeout = aiohttp.ClientTimeout(
            total=30,
            connect=5,
            sock_read=25,
        )
        connector = aiohttp.TCPConnector(
            family=socket.AF_INET,
            limit=10,
            limit_per_host=5,
            ttl_dns_cache=300,
            enable_cleanup_closed=True,
            force_close=True,
        )
        _http_session = aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            headers={"User-Agent": "ProjectX-Bot/10.0"},
        )
        logger.info(
            "[YooKassa] HTTP session created: "
            "limit=10, connect=5s, IPv4 only, DNS cache=300s"
        )
    return _http_session


async def close_yookassa_session() -> None:
    global _http_session
    if _http_session is not None:
        await _http_session.close()
        _http_session = None
        logger.info("[YooKassa] HTTP session closed")


class YooKassaClient:
    """
    Клиент для работы с YooKassa API v3.

    Аутентификация: HTTP Basic Auth (shopId:secretKey).
    Все запросы к https://api.yookassa.ru/v3.

    Безопасность webhook обеспечивается:
      1) IP-whitelisting (диапазоны YooKassa);
      2) Верификацией object.id + суммы + статуса через API.
    YooKassa НЕ отправляет заголовки аутентификации в webhook.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self.shop_id = settings.YOOKASSA_SHOP_ID
        self.secret_key = settings.YOOKASSA_SECRET_KEY
        self._auth = aiohttp.BasicAuth(
            login=self.shop_id,
            password=self.secret_key,
        )

    async def create_payment(
        self,
        amount: Decimal,
        currency: str = "RUB",
        description: str = "",
        return_url: str = "",
        metadata: Optional[dict] = None,
    ) -> Optional[dict]:
        """
        Создаёт платёж в YooKassa.

        POST /v3/payments
        Idempotence-Key генерируется автоматически.

        Возвращает dict ответа API или None при ошибке.
        """
        url = f"{YOOKASSA_API_BASE}/payments"
        body: dict = {
            "amount": {
                "value": str(amount),
                "currency": currency,
            },
            "description": description,
            "confirmation": {
                "type": "redirect",
                "return_url": return_url,
            },
        }
        if metadata:
            body["metadata"] = metadata

        idempotence_key = str(uuid.uuid4())
        try:
            session = await _get_session()
            async with session.post(
                url,
                json=body,
                auth=self._auth,
                headers={
                    "Content-Type": "application/json",
                    "Idempotence-Key": idempotence_key,
                },
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.info(
                        "YooKassa payment created: id=%s, status=%s",
                        data.get("id"),
                        data.get("status"),
                    )
                    return data
                else:
                    text = await resp.text()
                    logger.error(
                        "YooKassa create_payment failed: "
                        "status=%s, body=%s",
                        resp.status,
                        text[:500],
                    )
                    return None
        except Exception as e:
            logger.error(
                "YooKassa create_payment exception: %s",
                e,
                exc_info=True,
            )
            return None

    async def get_payment(
        self,
        payment_id: str,
    ) -> Optional[dict]:
        """
        Запрашивает платёж из YooKassa по его ID.

        GET /v3/payments/{payment_id}

        Возвращает dict ответа API или None.
        """
        url = f"{YOOKASSA_API_BASE}/payments/{payment_id}"
        try:
            session = await _get_session()
            async with session.get(
                url,
                auth=self._auth,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data
                elif resp.status == 404:
                    logger.warning(
                        "YooKassa payment not found: %s",
                        payment_id,
                    )
                    return None
                else:
                    text = await resp.text()
                    logger.error(
                        "YooKassa get_payment failed: "
                        "status=%s, body=%s",
                        resp.status,
                        text[:500],
                    )
                    return None
        except Exception as e:
            logger.error(
                "YooKassa get_payment exception: %s",
                e,
                exc_info=True,
            )
            return None

    @staticmethod
    def normalize_webhook_event(event: str) -> str:
        """
        Приводит event из webhook YooKassa
        к внутреннему статусу.

        YooKassa events:
          - payment.succeeded  → CONFIRMED
          - payment.canceled   → CANCELED
          - refund.succeeded   → CHARGEBACKED
        """
        mapping = {
            "payment.succeeded": "CONFIRMED",
            "payment.canceled": "CANCELED",
            "refund.succeeded": "CHARGEBACKED",
        }
        return mapping.get(event, event.upper())