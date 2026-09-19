"""Durable, fail-closed YooKassa refund lifecycle for balance top-ups."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, or_, select

from bot import texts
from config.enums import AdminAuditAction
from database.dispute_models import PaymentDispute
from database.models import (
    AccountBalanceReservation,
    AccountLedgerEntry,
    Payment,
    PaymentEvent,
    PaymentRefund,
    User,
)
from database.refund_models import ProviderRefundOperation
from database.repositories.account_ledger_repo import (
    create_payment_debit,
    lock_account_user,
    reserve_payment_funds,
    resolve_reservation,
    whole_rubles,
)
from services.audit_service import AuditService
from services.yookassa_service import YooKassaErrorKind, YooKassaResult, YooKassaService
from utils.datetime_helpers import now_utc

_logger = logging.getLogger(__name__)

REFUND_LEASE_SECONDS = 60
ACTIVE_STATUSES = ("pending", "processing", "retry")


class BalanceRefundError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ProviderRefundOwnershipError(RuntimeError):
    pass


@dataclass(frozen=True)
class BalanceRefundRequest:
    operation: ProviderRefundOperation
    reservation: AccountBalanceReservation
    created: bool


@dataclass(frozen=True)
class ProviderRefundClaim:
    operation_id: int
    payment_id: int
    reservation_id: int
    public_operation_id: uuid.UUID
    idempotency_key: str
    amount: Decimal
    currency: str
    provider_payment_id: str
    provider_refund_id: str | None
    worker_id: str
    attempt_number: int
    created_at: object


async def request_balance_topup_refund(
    session,
    *,
    payment_id: int,
    requested_by_admin_id: int | None,
) -> BalanceRefundRequest:
    """Reserve every currently refundable ruble and enqueue one provider command."""
    # To respect global Payment -> User lock hierarchy, we MUST lock Payment first
    payment = await session.scalar(
        select(Payment).where(Payment.id == payment_id).with_for_update()
    )
    if payment is None:
        raise BalanceRefundError("payment_not_found")
    
    await lock_account_user(session, payment.user_id)
    if payment.provider_status not in {"succeeded", "refunded"}:
        raise BalanceRefundError("payment_not_refundable")
    if not payment.external_id:
        raise BalanceRefundError("provider_payment_id_missing")
    if payment.currency != "RUB":
        raise BalanceRefundError("refund_currency_invalid")
    active_dispute = await session.scalar(
        select(PaymentDispute.id).where(
            PaymentDispute.payment_id == payment.id,
            PaymentDispute.status.in_(("open", "manual_review")),
        )
    )
    if active_dispute is not None:
        raise BalanceRefundError("payment_has_active_dispute")

    active = await session.scalar(
        select(ProviderRefundOperation)
        .where(
            ProviderRefundOperation.payment_id == payment.id,
            ProviderRefundOperation.status.in_(ACTIVE_STATUSES),
        )
        .order_by(ProviderRefundOperation.id.desc())
        .with_for_update()
    )
    if active is not None:
        reservation = await session.get(
            AccountBalanceReservation, active.reservation_id
        )
        if reservation is None or reservation.status != "active":
            raise BalanceRefundError("active_refund_reservation_missing")
        return BalanceRefundRequest(active, reservation, False)

    already_refunded = Decimal(
        await session.scalar(
            select(func.coalesce(func.sum(PaymentRefund.amount), 0)).where(
                PaymentRefund.payment_id == payment.id,
                PaymentRefund.provider_status == "succeeded",
            )
        )
        or 0
    )
    refundable = whole_rubles(
        max(Decimal(0), Decimal(payment.amount) - already_refunded),
        allow_zero=True,
    )
    if refundable <= 0:
        raise BalanceRefundError("no_refundable_balance")

    public_id = uuid.uuid4()
    idempotency_key = f"refund-{public_id.hex}"
    reservation, _ = await reserve_payment_funds(
        session,
        payment_id=payment.id,
        reservation_type="refund",
        amount=refundable,
        idempotency_key=f"refund-reservation:{public_id.hex}",
        metadata={
            "operation_id": str(public_id),
            "requested_by_admin_id": requested_by_admin_id,
        },
    )
    operation = ProviderRefundOperation(
        operation_id=public_id,
        payment_id=payment.id,
        reservation_id=reservation.id,
        idempotency_key=idempotency_key,
        amount=refundable,
        currency="RUB",
        provider_payment_id=payment.external_id,
        status="pending",
        next_attempt_at=now_utc(),
        requested_by_admin_id=requested_by_admin_id,
    )
    session.add(operation)
    await session.flush()
    session.add(
        PaymentEvent(
            payment_id=payment.id,
            event_type="balance_refund_requested",
            provider_status=payment.provider_status,
            reason="provider_refund_outbox_created",
            source="admin",
            details=(
                f"operation={public_id}; reservation={reservation.id}; "
                f"amount={int(refundable)} RUB"
            ),
        )
    )
    await AuditService.log_action(
        session,
        admin_id=requested_by_admin_id or 0,
        action=AdminAuditAction.BALANCE_REFUND_REQUESTED,
        target_type="Payment",
        target_id=payment.id,
        details=(
            f"operation={public_id}, reservation={reservation.id}, "
            f"amount={int(refundable)} RUB"
        ),
    )
    return BalanceRefundRequest(operation, reservation, True)


async def claim(session, worker_id: str) -> ProviderRefundClaim | None:
    operation = await session.scalar(
        select(ProviderRefundOperation)
        .where(
            ProviderRefundOperation.status.in_(("pending", "retry")),
            ProviderRefundOperation.next_attempt_at <= now_utc(),
            ProviderRefundOperation.attempts
            < ProviderRefundOperation.max_attempts,
        )
        .order_by(ProviderRefundOperation.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if operation is None:
        return None
    operation.status = "processing"
    operation.locked_by = worker_id
    operation.locked_at = now_utc()
    operation.attempts += 1
    await session.flush()
    return ProviderRefundClaim(
        operation.id,
        operation.payment_id,
        operation.reservation_id,
        operation.operation_id,
        operation.idempotency_key,
        Decimal(operation.amount),
        operation.currency,
        operation.provider_payment_id,
        operation.provider_refund_id,
        worker_id,
        operation.attempts,
        operation.created_at,
    )


def _refund_payload(claim: ProviderRefundClaim) -> dict:
    return {
        "payment_id": claim.provider_payment_id,
        "amount": {
            "value": format(claim.amount, ".2f"),
            "currency": claim.currency,
        },
        "description": texts.REFUND_ORDER_DESCRIPTION_TEMPLATE.format(order_id=claim.payment_id),
    }


async def perform_http(
    claim: ProviderRefundClaim,
    transport=YooKassaService,
) -> YooKassaResult[dict]:
    if claim.provider_refund_id:
        return await transport.get_refund_result(claim.provider_refund_id)
    if now_utc() - claim.created_at >= timedelta(hours=24):
        return YooKassaResult(
            False,
            error_kind=YooKassaErrorKind.IDEMPOTENCY_WINDOW_EXPIRED,
            retryable=False,
            ambiguous=True,
        )
    return await transport.create_refund_result(
        _refund_payload(claim), idempotency_key=claim.idempotency_key
    )


def _parse_provider_refund(data: dict) -> tuple[str, str, Decimal, str, str]:
    refund_id = str(data.get("id") or "")
    payment_id = str(data.get("payment_id") or "")
    status = str(data.get("status") or "")
    amount_obj = data.get("amount") or {}
    currency = str(amount_obj.get("currency") or "")
    try:
        amount = Decimal(str(amount_obj.get("value")))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BalanceRefundError("provider_refund_amount_invalid") from exc
    if not refund_id or not payment_id:
        raise BalanceRefundError("provider_refund_identity_missing")
    if status not in {"pending", "succeeded", "canceled"}:
        raise BalanceRefundError("provider_refund_status_invalid")
    if amount <= 0 or amount != amount.to_integral_value():
        raise BalanceRefundError("provider_refund_amount_invalid")
    return refund_id, payment_id, amount, currency, status


async def _get_or_create_payment_refund(
    session,
    *,
    payment: Payment,
    provider_refund_id: str,
    amount: Decimal,
    currency: str,
    provider_status: str,
    event_key: str,
) -> PaymentRefund:
    refund = await session.scalar(
        select(PaymentRefund)
        .where(PaymentRefund.provider_refund_id == provider_refund_id)
        .with_for_update()
    )
    if refund is None:
        refund = PaymentRefund(
            payment_id=payment.id,
            provider_refund_id=provider_refund_id,
            amount=amount,
            currency=currency,
            provider_status=provider_status,
            event_key=event_key[:64],
            processed_at=now_utc() if provider_status != "pending" else None,
        )
        session.add(refund)
        await session.flush()
    elif (
        refund.payment_id != payment.id
        or Decimal(refund.amount) != amount
        or refund.currency != currency
    ):
        raise BalanceRefundError("provider_refund_idempotency_conflict")
    else:
        refund.provider_status = provider_status
        if provider_status != "pending":
            refund.processed_at = refund.processed_at or now_utc()
    return refund


async def _find_matching_active_operation(
    session,
    *,
    payment: Payment,
    provider_refund_id: str,
    amount: Decimal,
    currency: str,
) -> ProviderRefundOperation | None:
    operation = await session.scalar(
        select(ProviderRefundOperation)
        .where(
            ProviderRefundOperation.payment_id == payment.id,
            ProviderRefundOperation.amount == amount,
            ProviderRefundOperation.currency == currency,
            ProviderRefundOperation.status.in_(ACTIVE_STATUSES),
            or_(
                ProviderRefundOperation.provider_refund_id.is_(None),
                ProviderRefundOperation.provider_refund_id == provider_refund_id,
            ),
        )
        .order_by(ProviderRefundOperation.id)
        .with_for_update()
    )
    if operation is None:
        return None
    if operation.provider_payment_id != payment.external_id:
        raise BalanceRefundError("active_refund_provider_payment_mismatch")
    return operation


async def _consume_reservation_if_present(
    session,
    *,
    reservation_id: int | None,
) -> None:
    if reservation_id is not None:
        reservation = await session.scalar(
            select(AccountBalanceReservation)
            .where(
                AccountBalanceReservation.id == reservation_id,
                AccountBalanceReservation.status == "active",
            )
            .with_for_update()
        )
        if reservation is not None:
            await resolve_reservation(
                session, reservation_id=reservation.id, outcome="consumed"
            )


async def _update_topup_after_refund(session, payment: Payment) -> Decimal:
    total = Decimal(
        await session.scalar(
            select(func.coalesce(func.sum(PaymentRefund.amount), 0)).where(
                PaymentRefund.payment_id == payment.id,
                PaymentRefund.provider_status == "succeeded",
            )
        )
        or 0
    )
    if total > Decimal(payment.amount):
        raise BalanceRefundError("provider_refund_total_exceeds_payment")
    if total == Decimal(payment.amount):
        payment.provider_status = "refunded"
        if payment.fulfillment_status != "manual_review":
            payment.fulfillment_status = "reversed"
        payment.reversed_at = payment.reversed_at or now_utc()
    else:
        payment.provider_status = "succeeded"
        if payment.fulfillment_status != "manual_review":
            payment.fulfillment_status = "succeeded"
    if payment.reconciliation_status not in {"mismatch", "manual_review"}:
        payment.reconciliation_status = "ok"
    if payment.manual_review_reason in ("partial_refund", "chargeback_debt") and payment.fulfillment_status != "manual_review":
        payment.manual_review_reason = None
    return total


async def apply_balance_topup_refund_success(
    session,
    *,
    payment: Payment,
    provider_refund_id: str,
    amount: Decimal,
    currency: str,
    event_key: str,
    reservation_id: int | None = None,
    operation: ProviderRefundOperation | None = None,
) -> PaymentRefund:
    """Apply provider truth once; webhook and outbox finalizers share this path."""
    amount = whole_rubles(amount)
    if currency != "RUB" or payment.currency != "RUB":
        raise BalanceRefundError("refund_currency_invalid")
    if operation is None:
        operation = await _find_matching_active_operation(
            session,
            payment=payment,
            provider_refund_id=provider_refund_id,
            amount=amount,
            currency=currency,
        )
        if operation is not None:
            reservation_id = operation.reservation_id
    elif (
        operation.payment_id != payment.id
        or Decimal(operation.amount) != amount
        or operation.currency != currency
        or operation.provider_payment_id != payment.external_id
        or operation.provider_refund_id not in {None, provider_refund_id}
    ):
        raise BalanceRefundError("provider_refund_operation_mismatch")
    already = Decimal(
        await session.scalar(
            select(func.coalesce(func.sum(PaymentRefund.amount), 0)).where(
                PaymentRefund.payment_id == payment.id,
                PaymentRefund.provider_status == "succeeded",
                PaymentRefund.provider_refund_id != provider_refund_id,
            )
        )
        or 0
    )
    if already + amount > Decimal(payment.amount):
        raise BalanceRefundError("provider_refund_total_exceeds_payment")
    refund = await _get_or_create_payment_refund(
        session,
        payment=payment,
        provider_refund_id=provider_refund_id,
        amount=amount,
        currency=currency,
        provider_status="succeeded",
        event_key=event_key,
    )
    is_full_refund = bool(already + amount >= Decimal(payment.amount))

    # Check if payment was ever credited to user's internal ledger
    credit_entry = await session.scalar(
        select(AccountLedgerEntry).where(
            AccountLedgerEntry.payment_id == payment.id,
            AccountLedgerEntry.entry_type == "payment_credit",
        )
    )

    created_debit = False
    async with session.begin_nested():
        if credit_entry is None and payment.credited_at is None:
            # Case A: Payment was NEVER credited to user's ledger (e.g. race condition
            # where refund.succeeded arrives before payment.succeeded, or canceled before settlement).
            # Do NOT create refund_debit, do NOT deduct from balance, do NOT cut subscriptions.
            await _consume_reservation_if_present(session, reservation_id=reservation_id)
            await _update_topup_after_refund(session, payment)
            session.add(
                PaymentEvent(
                    payment_id=payment.id,
                    event_type="uncredited_refund_applied",
                    provider_status=payment.provider_status,
                    reason="payment_not_credited_no_debit_needed",
                    source="provider_refund",
                    details=f"refund={provider_refund_id}; amount={int(amount)} RUB",
                )
            )
        else:
            # Case B: Payment was credited. Inspect if funds were spent on a service or remain unspent.
            from database.models import AccountLedgerAllocation
            allocations = (
                await session.scalars(
                    select(AccountLedgerAllocation).where(
                        AccountLedgerAllocation.credit_entry_id == (credit_entry.id if credit_entry else -1)
                    )
                )
            ).all()

            topup_ctx = payment.topup_context if isinstance(payment.topup_context, dict) else {}
            topup_action = topup_ctx.get("auto_fulfill_action")
            auto_fulfill_status = topup_ctx.get("auto_fulfill_status")
            quote_raw = topup_ctx.get("quote_public_id")

            # True spent purchase: allocations exist OR auto-fulfillment requested/succeeded (and not failed)
            auto_fulfill_failed = auto_fulfill_status == "failed"
            has_spent_purchase = bool(
                allocations
                or (not auto_fulfill_failed and topup_action in (
                    "purchase",
                    "renew",
                    "tariff_change",
                    "white_internet_buy",
                    "white_internet_renew",
                    "white_internet_add_device",
                    "white_internet_pack",
                ))
            )

            if not has_spent_purchase:
                # Sub-case B1: Unspent balance top-up.
                # Deduct funds from user's internal balance. Subscriptions are untouched.
                debit, created_debit = await create_payment_debit(
                    session,
                    payment_id=payment.id,
                    entry_type="refund_debit",
                    amount=amount,
                    idempotency_key=f"provider-refund-debit:{provider_refund_id}",
                    metadata={
                        "provider_refund_id": provider_refund_id,
                        "event_key": event_key,
                        "source": "provider_refund",
                    },
                )
                await _consume_reservation_if_present(session, reservation_id=reservation_id)
                await _update_topup_after_refund(session, payment)
                from services.referral_bonus import reverse_referral_bonus_for_topup
                await reverse_referral_bonus_for_topup(session, payment_id=payment.id)
                from database.repositories.account_ledger_repo import get_account_balance
                balance = await get_account_balance(session, user_id=payment.user_id)
                if balance.debt > 0:
                    await place_financial_hold(session, payment=payment, reason="chargeback_debt")
            elif not is_full_refund:
                # Sub-case B2: Partial refund of an indivisible service.
                # Must NOT create refund_debit (which would create false debt and block user).
                # Route to manual review for administrator inspection.
                payment.reconciliation_status = "manual_review"
                payment.manual_review_reason = "partial_refund_requires_admin_review"
                session.add(
                    PaymentEvent(
                        payment_id=payment.id,
                        event_type="partial_refund_manual_review",
                        provider_status=payment.provider_status,
                        reason="partial_refund_requires_admin_review",
                        source="provider_refund",
                        details=f"amount={int(amount)} RUB of {int(payment.amount)} RUB",
                    )
                )
                await _update_topup_after_refund(session, payment)
            elif topup_action == "tariff_change":
                # Sub-case B3: Refund of a tariff_change payment (upgrade difference).
                # Cannot be trivially rolled back by subtracting target duration.
                # Route to manual review without corrupting subscription dates or blocking user.
                payment.reconciliation_status = "manual_review"
                payment.manual_review_reason = "tariff_change_refund_requires_admin_review"
                session.add(
                    PaymentEvent(
                        payment_id=payment.id,
                        event_type="tariff_change_refund_manual_review",
                        provider_status=payment.provider_status,
                        reason="tariff_change_refund_requires_admin_review",
                        source="provider_refund",
                        details=f"payment_id={payment.id}; amount={int(amount)} RUB",
                    )
                )
                await _update_topup_after_refund(session, payment)
            else:
                # Sub-case B4: Full refund of spent service (AWG or White Internet).
                from database.models import Tariff, TariffQuote, TariffVersion
                from database.repositories.account_ledger_repo import create_purchase_reversal

                # 1. Reverse purchase debits to balance the account ledger
                reversed_quote_ids = set()
                for alloc in allocations:
                    p_debit = await session.get(AccountLedgerEntry, alloc.debit_entry_id)
                    if p_debit is not None and p_debit.entry_type == "purchase_debit":
                        already_rev = await session.scalar(
                            select(AccountLedgerEntry.id).where(
                                AccountLedgerEntry.entry_type == "purchase_reversal",
                                AccountLedgerEntry.reversal_of_id == p_debit.id,
                            )
                        )
                        if not already_rev:
                            await create_purchase_reversal(
                                session,
                                debit_id=p_debit.id,
                                metadata={
                                    "reason": "payment_refunded",
                                    "provider_refund_id": provider_refund_id,
                                    "payment_id": payment.id,
                                },
                            )
                        if p_debit.quote_id:
                            reversed_quote_ids.add(p_debit.quote_id)

                if not reversed_quote_ids and quote_raw:
                    try:
                        q_uuid = uuid.UUID(str(quote_raw))
                        ctx_quote = await session.scalar(
                            select(TariffQuote).where(TariffQuote.public_id == q_uuid)
                        )
                        if ctx_quote:
                            reversed_quote_ids.add(ctx_quote.id)
                            ctx_debit = await session.scalar(
                                select(AccountLedgerEntry).where(
                                    AccountLedgerEntry.entry_type == "purchase_debit",
                                    AccountLedgerEntry.quote_id == ctx_quote.id,
                                )
                            )
                            if ctx_debit:
                                already_rev = await session.scalar(
                                    select(AccountLedgerEntry.id).where(
                                        AccountLedgerEntry.entry_type == "purchase_reversal",
                                        AccountLedgerEntry.reversal_of_id == ctx_debit.id,
                                    )
                                )
                                if not already_rev:
                                    await create_purchase_reversal(
                                        session,
                                        debit_id=ctx_debit.id,
                                        metadata={
                                            "reason": "payment_refunded",
                                            "provider_refund_id": provider_refund_id,
                                            "payment_id": payment.id,
                                        },
                                    )
                    except (ValueError, TypeError) as exc:
                        _logger.warning("Failed to parse quote_raw %s: %s", quote_raw, exc)

                # 2. Post the refund debit (reversal + refund_debit = net 0 change to balance)
                debit, created_debit = await create_payment_debit(
                    session,
                    payment_id=payment.id,
                    entry_type="refund_debit",
                    amount=amount,
                    idempotency_key=f"provider-refund-debit:{provider_refund_id}",
                    metadata={
                        "provider_refund_id": provider_refund_id,
                        "event_key": event_key,
                        "source": "provider_refund",
                    },
                )
                await _consume_reservation_if_present(session, reservation_id=reservation_id)

                # 3. Determine services to revoke
                awg_duration_to_revoke = timedelta(0)
                has_white_internet_subscription = False
                white_internet_pack_gb = 0

                for q_id in reversed_quote_ids:
                    q = await session.get(TariffQuote, q_id)
                    if q:
                        q.status = "cancelled"
                        if q.target_tariff_version_id:
                            t_ver = await session.get(TariffVersion, q.target_tariff_version_id)
                            if t_ver:
                                tar = await session.get(Tariff, t_ver.tariff_id)
                                if tar and tar.service_type == "awg":
                                    if t_ver.duration_hours:
                                        awg_duration_to_revoke += timedelta(hours=t_ver.duration_hours)
                                elif tar and tar.service_type == "white_internet":
                                    has_white_internet_subscription = True

                if topup_action in ("purchase", "renew") and awg_duration_to_revoke == timedelta(0):
                    if quote_raw:
                        try:
                            q_uuid = uuid.UUID(str(quote_raw))
                            q = await session.scalar(
                                select(TariffQuote).where(TariffQuote.public_id == q_uuid)
                            )
                            if q:
                                q.status = "cancelled"
                                if q.target_tariff_version_id:
                                    t_ver = await session.get(TariffVersion, q.target_tariff_version_id)
                                    if t_ver and t_ver.duration_hours:
                                        awg_duration_to_revoke += timedelta(hours=t_ver.duration_hours)
                        except (ValueError, TypeError) as exc:
                            _logger.warning("Failed to parse quote_raw fallback %s: %s", quote_raw, exc)

                if topup_action in ("white_internet_buy", "white_internet_renew"):
                    has_white_internet_subscription = True
                elif topup_action == "white_internet_pack":
                    white_internet_pack_gb = int(topup_ctx.get("pack_gb") or 0)

                # Revoke AWG access if applicable
                if awg_duration_to_revoke > timedelta(0):
                    from config.constants import VPN_ACCESS_GRACE_HOURS
                    from services.subscription import SubscriptionService
                    from services.user_cache import invalidate_user_cache

                    user = await session.scalar(
                        select(User).where(User.id == payment.user_id).with_for_update()
                    )
                    if user is not None and user.subscription_end is not None:
                        now = now_utc()
                        expired_threshold = now - timedelta(
                            hours=VPN_ACCESS_GRACE_HOURS, seconds=1
                        )
                        new_end = user.subscription_end - awg_duration_to_revoke
                        user.subscription_end = (
                            expired_threshold if new_end <= now else new_end
                        )

                        if user.subscription_end <= now:
                            user.notified_3d = False
                            user.notified_1d = False
                            user.notified_2h = False
                            user.notified_expired = False
                            user.notified_grace_12h = False
                            user.notification_retry_count = 0
                            user.last_notification_attempt = None

                        await session.flush()
                        await SubscriptionService._sync_access_state(session, user)
                        invalidate_user_cache(user.telegram_id)

                # Revoke White Internet access if applicable
                if has_white_internet_subscription:
                    from config.enums import WhiteInternetProvisioningStatus, WhiteInternetStatus
                    from database.models import WhiteInternetSubscription

                    wi_subs = (
                        await session.scalars(
                            select(WhiteInternetSubscription).where(
                                WhiteInternetSubscription.user_id == payment.user_id,
                                WhiteInternetSubscription.status != WhiteInternetStatus.DISABLED,
                            ).with_for_update()
                        )
                    ).all()
                    for wi_sub in wi_subs:
                        wi_sub.status = WhiteInternetStatus.DISABLED
                        wi_sub.status_reason = "payment_refunded"
                        wi_sub.desired_version += 1
                        wi_sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_DELETE
                    await session.flush()

                if white_internet_pack_gb > 0:
                    from database.models import WhiteInternetSubscription

                    wi_sub = await session.scalar(
                        select(WhiteInternetSubscription).where(
                            WhiteInternetSubscription.user_id == payment.user_id,
                        ).order_by(WhiteInternetSubscription.id.desc()).with_for_update()
                    )
                    if wi_sub is not None:
                        pack_bytes = white_internet_pack_gb * (1024 ** 3)
                        wi_sub.traffic_limit_bytes = max(
                            0, (wi_sub.traffic_limit_bytes or 0) - pack_bytes
                        )
                        await session.flush()

                await _update_topup_after_refund(session, payment)
                from services.referral_bonus import reverse_referral_bonus_for_topup
                await reverse_referral_bonus_for_topup(session, payment_id=payment.id)
                from database.repositories.account_ledger_repo import get_account_balance
                balance = await get_account_balance(session, user_id=payment.user_id)
                if balance.debt > 0:
                    await place_financial_hold(session, payment=payment, reason="chargeback_debt")
    if operation is not None:
        operation.provider_refund_id = provider_refund_id
        operation.provider_status = "succeeded"
        operation.status = "completed"
        operation.completed_at = now_utc()
        operation.last_error_code = None
        operation.last_error = None
        operation.locked_at = None
        operation.locked_by = None
    if created_debit:
        session.add(
            PaymentEvent(
                payment_id=payment.id,
                event_type="balance_refund_applied",
                provider_status="succeeded",
                reason="provider_refund_confirmed",
                source="provider_refund",
                details=f"refund={provider_refund_id}; amount={int(amount)} RUB",
            )
        )
    return refund


async def place_financial_hold(
    session,
    *,
    payment: Payment,
    reason: str,
) -> None:
    user = await session.scalar(
        select(User).where(User.id == payment.user_id).with_for_update()
    )
    if user is not None:
        user.financial_hold = True
        user.topup_blocked = True
        user.financial_block_reason = str(reason)[:255]
    payment.reconciliation_status = "manual_review"
    payment.fulfillment_status = "manual_review"
    payment.manual_review_reason = str(reason)[:255]


async def finalize(
    session,
    claim: ProviderRefundClaim,
    result: YooKassaResult[dict],
) -> None:
    payment = await session.scalar(
        select(Payment).where(Payment.id == claim.payment_id).with_for_update()
    )
    operation = await session.scalar(
        select(ProviderRefundOperation)
        .where(ProviderRefundOperation.id == claim.operation_id)
        .with_for_update()
    )
    if (
        operation is None
        or operation.status != "processing"
        or operation.locked_by != claim.worker_id
        or operation.attempts != claim.attempt_number
    ):
        raise ProviderRefundOwnershipError(claim.operation_id)
    if payment is None:
        await place_financial_hold_for_missing_payment(session, operation)
        return

    if result.ok:
        try:
            refund_id, provider_payment_id, amount, currency, status = (
                _parse_provider_refund(result.value or {})
            )
            if (
                provider_payment_id != operation.provider_payment_id
                or amount != Decimal(operation.amount)
                or currency != operation.currency
            ):
                raise BalanceRefundError("provider_refund_economics_mismatch")
        except BalanceRefundError as exc:
            _logger.warning("FINALIZE: BalanceRefundError during parse: %s", exc.code)
            result = YooKassaResult(
                False,
                error_kind=YooKassaErrorKind.INVALID_RESPONSE,
                retryable=True,
                ambiguous=True,
            )
            operation.last_error_code = exc.code
        else:
            operation.provider_refund_id = refund_id
            operation.provider_status = status
            await _get_or_create_payment_refund(
                session,
                payment=payment,
                provider_refund_id=refund_id,
                amount=amount,
                currency=currency,
                provider_status=status,
                event_key=f"provider-operation:{operation.operation_id}",
            )
            if status == "succeeded":
                _logger.debug("FINALIZE: applying refund success, operation_id=%s", operation.id)
                await apply_balance_topup_refund_success(
                    session,
                    payment=payment,
                    provider_refund_id=refund_id,
                    amount=amount,
                    currency=currency,
                    event_key=f"provider-operation:{operation.operation_id}",
                    reservation_id=operation.reservation_id,
                    operation=operation,
                )
            elif status == "pending":
                _logger.debug("FINALIZE: provider refund still pending, operation_id=%s", operation.id)
                operation.status = "retry"
                operation.next_attempt_at = now_utc() + timedelta(seconds=10)
            else:
                _logger.debug("FINALIZE: provider refund status=%s, operation_id=%s", status, operation.id)
                await resolve_reservation(
                    session,
                    reservation_id=operation.reservation_id,
                    outcome="released",
                )
                operation.status = "failed"
                operation.completed_at = now_utc()
                operation.last_error_code = "provider_refund_canceled"
                session.add(
                    PaymentEvent(
                        payment_id=payment.id,
                        event_type="balance_refund_failed",
                        provider_status=status,
                        reason="provider_refund_canceled",
                        source="provider_refund",
                    )
                )
    if not result.ok:
        operation.last_error_code = operation.last_error_code or (
            result.error_kind.value if result.error_kind else "provider_refund_error"
        )
        operation.last_error = None
        exhausted = operation.attempts >= operation.max_attempts
        terminal = exhausted or not result.retryable
        operation.status = "failed" if terminal else "retry"
        operation.next_attempt_at = now_utc() + timedelta(
            seconds=min(300, 2 ** min(operation.attempts, 8))
        )
        if terminal:
            operation.completed_at = now_utc()
            if result.ambiguous:
                await place_financial_hold(
                    session,
                    payment=payment,
                    reason="provider_refund_outcome_ambiguous",
                )
            else:
                await resolve_reservation(
                    session,
                    reservation_id=operation.reservation_id,
                    outcome="released",
                )
            session.add(
                PaymentEvent(
                    payment_id=payment.id,
                    event_type="balance_refund_failed",
                    provider_status=payment.provider_status,
                    reason=operation.last_error_code,
                    source="provider_refund",
                )
            )
    operation.locked_at = None
    operation.locked_by = None
    await session.flush()


async def place_financial_hold_for_missing_payment(
    session, operation: ProviderRefundOperation
) -> None:
    operation.status = "failed"
    operation.completed_at = now_utc()
    operation.last_error_code = "refund_payment_missing"
    operation.locked_at = None
    operation.locked_by = None


async def recover_stale(session, lease_seconds=REFUND_LEASE_SECONDS) -> int:
    stale_rows = (
        await session.execute(
            select(ProviderRefundOperation.id, ProviderRefundOperation.payment_id)
            .where(
                ProviderRefundOperation.status == "processing",
                ProviderRefundOperation.locked_at
                < now_utc() - timedelta(seconds=lease_seconds),
            )
            .limit(100)
        )
    ).all()
    count = 0
    for op_id, payment_id in stale_rows:
        # Respect global hierarchy: Payment -> ProviderRefundOperation
        payment = await session.scalar(
            select(Payment)
            .where(Payment.id == payment_id)
            .with_for_update(skip_locked=True)
        )
        if payment is None:
            continue
        operation = await session.scalar(
            select(ProviderRefundOperation)
            .where(
                ProviderRefundOperation.id == op_id,
                ProviderRefundOperation.status == "processing"
            )
            .with_for_update(skip_locked=True)
        )
        if operation is None:
            continue

        dead = operation.attempts >= operation.max_attempts
        operation.status = "failed" if dead else "retry"
        operation.completed_at = now_utc() if dead else None
        operation.next_attempt_at = now_utc()
        operation.locked_at = None
        operation.locked_by = None
        if dead:
            await place_financial_hold(
                session,
                payment=payment,
                reason="provider_refund_lease_exhausted",
            )
        count += 1
    return count


async def finalize_provider_failure(
    session,
    claim: ProviderRefundClaim,
    *,
    error_code: str,
    retryable: bool,
) -> None:
    payment = await session.scalar(
        select(Payment).where(Payment.id == claim.payment_id).with_for_update()
    )
    operation = await session.scalar(
        select(ProviderRefundOperation)
        .where(ProviderRefundOperation.id == claim.operation_id)
        .with_for_update()
    )
    if (
        operation is None
        or operation.status != "processing"
        or operation.locked_by != claim.worker_id
        or operation.attempts != claim.attempt_number
    ):
        raise ProviderRefundOwnershipError(claim.operation_id)
    dead = (not retryable) or operation.attempts >= operation.max_attempts
    operation.status = "failed" if dead else "retry"
    operation.completed_at = now_utc() if dead else None
    operation.next_attempt_at = now_utc() + timedelta(
        seconds=min(300, 2 ** min(operation.attempts, 8))
    )
    operation.last_error_code = str(error_code)[:100]
    operation.last_error = None
    operation.locked_at = None
    operation.locked_by = None
    if dead and payment is not None:
        await place_financial_hold(
            session,
            payment=payment,
            reason="provider_refund_worker_failure",
        )
