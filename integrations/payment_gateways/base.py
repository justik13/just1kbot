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
    related_external_id: str | None = None
    is_canceled: bool = False

    @property
    def payment_id(self) -> str | None:
        return self.related_external_id


@dataclass(frozen=True)
class PaymentStatusResult:
    is_paid: bool
    is_refunded: bool
    is_canceled: bool
    status_str: str
    amount_rub: Decimal | None = None
    cancellation_reason: str | None = None
    is_temporary_error: bool = False


class PaymentCreationAmbiguousError(RuntimeError):
    """The provider may have created the payment, but the result is unknown.

    Network timeout, 5xx or an unreadable response. YooKassa documents HTTP
    500 as "result unknown, check first" and keeps an Idempotence-Key valid
    for 24 hours, so a payment object may exist while the local order state
    is undefined. Callers must NOT discard the order in this case: a
    `payment.succeeded` webhook may still arrive for it.
    """


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
        bot_username: str | None = None,
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
