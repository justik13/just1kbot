"""Provider reconciliation helpers for user-triggered balance-topup refreshes."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Payment
from database.repositories.tariff_quotes_repo import lock_checkout_user
from services.account_topup import AccountTopupError, settle_succeeded_topup


async def request_topup_status_refresh(
    session: AsyncSession,
    *,
    payment_id: int,
    source: str = "user_refresh",
) -> Payment:
    """Queue provider reconciliation and recover a verified but uncredited top-up."""
    payment = await session.scalar(
        select(Payment).where(Payment.id == payment_id).with_for_update()
    )
    if payment is None:
        raise AccountTopupError("topup_not_found")

    user = await lock_checkout_user(session, payment.user_id)
    if user is not None:
        recovery_topup = (
            user.financial_hold
            and user.financial_block_reason == "chargeback_debt"
        )
        if user.topup_blocked or (user.financial_hold and not recovery_topup):
            payment.fulfillment_status = "manual_review"
            payment.reconciliation_status = "manual_review"
            payment.manual_review_reason = "user_financially_blocked"
            return payment

    if payment.external_id and payment.provider_status not in {
        "refunded",
        "canceled",
    }:
        from services.payment_provider_operations import (
            ensure_reconcile_payment_operation,
        )

        await ensure_reconcile_payment_operation(
            session,
            payment,
            reason=source,
        )

    if (
        payment.provider_status == "succeeded"
        and payment.provider_confirmed_at is not None
        and payment.fulfillment_status
        not in {"succeeded", "reversed", "manual_review"}
    ):
        await settle_succeeded_topup(
            session,
            payment=payment,
            source=f"{source}_recovery",
        )

    return payment
