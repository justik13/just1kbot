"""Factory for obtaining payment gateways."""

from integrations.payment_gateways.base import BasePaymentGateway
from integrations.payment_gateways.yookassa import YooKassaGateway

_GATEWAYS: dict[str, BasePaymentGateway] = {
    "yookassa": YooKassaGateway(),
}


def get_payment_gateway(name: str = "yookassa") -> BasePaymentGateway:
    gateway = _GATEWAYS.get(name.lower())
    if not gateway:
        raise ValueError(f"Unknown payment gateway: {name}")
    return gateway
