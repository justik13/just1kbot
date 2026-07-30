"""Durably prepare a tariff-change payment, without HTTP or transaction ownership.

The only mutable locks are checkout advisory lock -> User -> quote. Existing
Payment/provider identity is immutable and is deliberately read without another
``FOR UPDATE`` lock, avoiding inversion with Payment -> User finalizers.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.models import Payment, PaymentProviderOperation, TariffQuote, TariffVersion
from database.repositories.tariff_quotes_repo import lock_checkout_user
from services.payment_provider_operations import create_payload
from utils.datetime_helpers import now_utc


@dataclass(frozen=True)
class TariffChangePaymentResult:
    payment: Payment | None
    created: bool
    provider_operation: PaymentProviderOperation | None
    failure_code: str | None


def _failure(code: str) -> TariffChangePaymentResult:
    return TariffChangePaymentResult(None, False, None, code)


async def create_tariff_change_payment(
    session: AsyncSession, *, user_id: int, quote_public_id: uuid.UUID | str,
    bot_username: str, as_of: datetime,
) -> TariffChangePaymentResult:
    """Create only frozen local intent; caller owns commit/rollback."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        return _failure("as_of_timezone_required")
    try:
        quote_public_id = uuid.UUID(str(quote_public_id))
    except (ValueError, TypeError, AttributeError):
        return _failure("quote_public_id_invalid")
    user = await lock_checkout_user(session, user_id)
    if user is None:
        return _failure("user_not_found")
    if user.is_deleted or user.is_banned or user.is_bot_blocked:
        return _failure("user_ineligible")
    quote = await session.scalar(select(TariffQuote).where(
        TariffQuote.public_id == quote_public_id,
        TariffQuote.user_id == user_id,
    ).with_for_update())
    if quote is None:
        return _failure("quote_not_found")
    if quote.operation_type != "change":
        return _failure("quote_wrong_operation")
    if quote.status != "active":
        return _failure("quote_" + str(quote.status))
    if quote.expires_at is None or as_of >= quote.expires_at:
        return _failure("quote_expired")
    frozen = (quote.source_tariff_version_id, quote.target_tariff_version_id,
              quote.balance_as_of, quote.source_subscription_end,
              quote.source_balance_fingerprint, quote.source_entitlement_entry_ids,
              quote.source_ledger_entry_ids)
    if any(value is None for value in frozen) or quote.diagnostic_reason:
        return _failure("quote_quarantined_or_incomplete")
    source = await session.get(TariffVersion, quote.source_tariff_version_id)
    target = await session.get(TariffVersion, quote.target_tariff_version_id)
    if not source or not target or source.id == target.id:
        return _failure("quote_tariff_version_invalid")
    if user.current_tariff_id != source.tariff_id or target.tariff_id == source.tariff_id:
        return _failure("quote_tariff_version_invalid")
    if quote.currency != "RUB" or target.currency != quote.currency:
        return _failure("quote_currency_invalid")
    amount = quote.confirmed_payment_required_rub
    try:
        amount = Decimal(amount)
    except Exception:
        return _failure("quote_amount_invalid")
    if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
        return _failure("quote_amount_invalid")

    linked = None
    if quote.payment_id is not None:
        linked = await session.get(Payment, quote.payment_id)
    by_quote = await session.scalar(select(Payment).where(Payment.tariff_quote_id == quote.id))
    if linked is not None or by_quote is not None:
        payment = linked or by_quote
        if linked is None or by_quote is None or linked.id != by_quote.id or any((
            payment.user_id != quote.user_id,
            payment.tariff_version_id != target.id,
            payment.tariff_id != target.tariff_id,
            payment.amount != amount,
            payment.snapshot_amount != amount,
            payment.currency != quote.currency,
            payment.snapshot_currency != quote.currency,
        )):
            return _failure("quote_payment_conflict")
        operations = list((await session.scalars(select(PaymentProviderOperation).where(
            PaymentProviderOperation.payment_id == payment.id,
            PaymentProviderOperation.operation_type == "create_payment",
        ))).all())
        op = operations[0] if len(operations) == 1 else None
        positive = amount > 0
        if (payment.provider_required != positive or not payment.public_order_id or
                (positive and (not payment.provider_idempotency_key or op is None)) or
                (not positive and (payment.provider_idempotency_key is not None or operations or
                 payment.external_id is not None or payment.payment_url is not None or
                 payment.paid_at is not None or payment.provider_confirmed_at is not None or
                 payment.provider_status != "not_created"))):
            return _failure("provider_operation_conflict")
        if positive:
            settings = get_settings()
            expected = create_payload(
                payment, f"Доплата за смену тарифа ({target.name_snapshot})",
                settings.YOOKASSA_RETURN_URL.format(bot_username=bot_username.lstrip("@")),
            )
            if (op.idempotency_key != payment.provider_idempotency_key or op.payload != expected or
                    op.payload.get("metadata") != {"order_id": payment.public_order_id,
                                                   "local_payment_id": str(payment.id)}):
                return _failure("provider_operation_conflict")
        return TariffChangePaymentResult(payment, False, op, None)
    if quote.payment_id is not None or by_quote is not None:
        return _failure("quote_payment_conflict")

    # A checkout conflicts until provider impossibility and legacy fulfillment
    # finality are both established.  `abandoned` alone is never evidence.
    from sqlalchemy import and_, exists, or_
    from database.models import PaymentFulfillmentOperation
    provider_unfinished = exists(select(PaymentProviderOperation.id).where(
        PaymentProviderOperation.payment_id == Payment.id,
        PaymentProviderOperation.status.in_(("pending", "processing", "retry")),
    ))
    fulfillment_unfinished = exists(select(PaymentFulfillmentOperation.id).where(
        PaymentFulfillmentOperation.payment_id == Payment.id,
        PaymentFulfillmentOperation.status.in_(("pending", "processing", "retry")),
    ))
    conflict = await session.scalar(select(Payment.id).join(
        TariffQuote, TariffQuote.id == Payment.tariff_quote_id,
    ).where(
        Payment.user_id == user_id,
        TariffQuote.operation_type.in_(("purchase", "renew")),
        or_(
            Payment.provider_status.in_(("creating", "pending", "waiting_for_capture", "unknown", "manual_review")),
            provider_unfinished,
            and_(Payment.provider_status == "succeeded",
                 Payment.fulfillment_status.not_in(("succeeded", "reversed"))),
            fulfillment_unfinished,
        ),
    ).limit(1))
    if conflict is not None:
        return _failure("unfinished_checkout_exists")

    positive = amount > 0
    payment = Payment(
        user_id=user_id, tariff_quote_id=quote.id, tariff_version_id=target.id,
        tariff_id=target.tariff_id, amount=amount, currency=quote.currency,
        snapshot_amount=amount, snapshot_currency=quote.currency,
        snapshot_duration_days=None, snapshot_device_limit=None,
        public_order_id="chg_" + uuid.uuid4().hex,
        provider_idempotency_key=uuid.uuid4().hex if positive else None,
        provider_required=positive,
        provider_status="creating" if positive else "not_created",
        fulfillment_status="not_ready", reconciliation_status="ok",
        status="pending", checkout_status="active",
    )
    session.add(payment)
    await session.flush()
    quote.payment_id = payment.id
    operation = None
    if positive:
        settings = get_settings()
        description = f"Доплата за смену тарифа ({target.name_snapshot})"
        return_url = settings.YOOKASSA_RETURN_URL.format(bot_username=bot_username.lstrip("@"))
        operation = PaymentProviderOperation(
            payment_id=payment.id, operation_type="create_payment", status="pending",
            idempotency_key=payment.provider_idempotency_key,
            payload=create_payload(payment, description, return_url), next_attempt_at=now_utc(),
        )
        session.add(operation)
    await session.flush()
    return TariffChangePaymentResult(payment, True, operation, None)
