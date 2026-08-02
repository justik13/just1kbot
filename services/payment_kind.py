"""Shared classification for YooKassa balance top-ups."""

from database.models import Payment


def is_balance_topup(payment: Payment) -> bool:
    return payment.payment_kind == "balance_topup"
