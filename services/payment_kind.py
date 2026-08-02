"""Shared, lock-neutral classification of quote-backed payments."""
from sqlalchemy import select

from database.models import Payment, TariffQuote


def is_balance_topup(payment: Payment) -> bool:
    return payment.payment_kind == "balance_topup"


async def is_tariff_change_payment(session, payment: Payment) -> bool:
    """Identity lookup only: never adds a quote lock after a Payment lock."""
    if payment.tariff_quote_id is None:
        return False
    return bool(await session.scalar(select(TariffQuote.id).where(
        TariffQuote.id == payment.tariff_quote_id,
        TariffQuote.operation_type == "change",
    )))
