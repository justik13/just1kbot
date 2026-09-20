"""Base interfaces and data structures for payment gateways."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PaymentInvoice:
    payment_url: str
    external_id: str


@dataclass(frozen=True)
class WebhookResult:
    order_id: str | None
    is_paid: bool
    is_refunded: bool
    external_id: str
    amount_rub: Decimal | None = None
    event_type: str = ""


@dataclass(frozen=True)
class PaymentStatusResult:
    is_paid: bool
    is_refunded: bool
    is_canceled: bool
    status_str: str


class BasePaymentGateway(ABC):
    """Abstract contract for payment providers (YooKassa, CryptoBot, Telegram Stars)."""

    name: str = "base"

    @abstractmethod
    async def create_payment_url(
        self,
        *,
        order_id: str,
        amount_rub: Decimal,
        description: str,
        return_url: str | None = None,
    ) -> PaymentInvoice:
        """Create a payment invoice with the provider and return redirect URL and external ID."""
        ...

    @abstractmethod
    async def parse_webhook(
        self,
        payload: dict,
        headers: dict | None = None,
    ) -> WebhookResult:
        """Parse incoming webhook event and return normalized payment outcome."""
        ...

    @abstractmethod
    async def check_payment_status(
        self,
        external_id: str,
    ) -> PaymentStatusResult:
        """Poll the provider API directly to check status on network or webhook delay."""
        ...
