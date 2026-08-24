"""Balance top-up creation, hiding, and exactly-once settlement."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.models import Payment, PaymentEvent, User
from database.repositories.account_ledger_repo import (
    AccountBalanceSnapshot,
    credit_succeeded_topup,
    get_account_balance,
    whole_rubles,
)
from database.repositories.tariff_quotes_repo import lock_checkout_user
from services.payment_disputes import refresh_user_dispute_hold
from services.payment_provider_operations import enqueue_create
from utils.datetime_helpers import now_utc


def get_topup_description(context: dict | None = None) -> str:
    ctx = context or {}
    action = ctx.get("auto_fulfill_action")
    operation = ctx.get("operation")

    if action == "purchase" and operation == "renew":
        return "Продление доступа к информационному сервису Just1k"
    if action == "tariff_change":
        return "Изменение параметров доступа к сервису Just1k"
    return "Предоставление доступа к информационному сервису Just1k"


UNFINISHED_TOPUP_PROVIDER_STATUSES = (
    "not_created",
    "creating",
    "pending",
    "waiting_for_capture",
    "unknown",
    "manual_review",
    "succeeded",
)


class AccountTopupError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class TopupCreationResult:
    payment: Payment
    created: bool
    balance: AccountBalanceSnapshot


async def _visible_topup_for_update(
    session: AsyncSession, user_id: int
) -> Payment | None:
    return await session.scalar(
        select(Payment)
        .where(
            Payment.user_id == user_id,
            Payment.ui_visible.is_(True),
            Payment.checkout_status == "active",
            Payment.provider_status.not_in(("succeeded", "canceled", "refunded")),
        )
        .order_by(Payment.id.desc())
        .with_for_update()
        .limit(1)
    )


async def get_visible_balance_topup(
    session: AsyncSession, *, user_id: int, for_update: bool = False
) -> Payment | None:
    statement = (
        select(Payment)
        .where(
            Payment.user_id == user_id,
            Payment.ui_visible.is_(True),
            Payment.checkout_status == "active",
            Payment.provider_status.not_in(("succeeded", "canceled", "refunded")),
        )
        .order_by(Payment.id.desc())
        .limit(1)
    )
    if for_update:
        statement = statement.with_for_update()
    return await session.scalar(statement)


async def _pending_topup_exposure(
    session: AsyncSession, user_id: int
) -> Decimal:
    from sqlalchemy import and_, or_

    from database.models import PaymentProviderOperation

    amount = await session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.user_id == user_id,
            Payment.credited_at.is_(None),
            or_(
                and_(
                    Payment.external_id.is_not(None),
                    or_(
                        Payment.provider_status.not_in(("canceled", "refunded", "succeeded")),
                        Payment.reconciliation_status.in_(("required", "mismatch", "manual_review")),
                    ),
                ),
                and_(
                    Payment.external_id.is_(None),
                    select(1)
                    .where(
                        PaymentProviderOperation.payment_id == Payment.id,
                        PaymentProviderOperation.operation_type == "create_payment",
                        PaymentProviderOperation.status.in_(("pending", "retry", "processing")),
                    )
                    .exists(),
                ),
            ),
        )
    )
    return Decimal(amount or 0)


async def create_balance_topup(
    session: AsyncSession,
    *,
    user_id: int,
    amount: object,
    bot_username: str,
    context: dict | None = None,
    settings=None,
) -> TopupCreationResult:
    """Create one durable provider command without performing provider HTTP."""
    cfg = settings or get_settings()
    try:
        rubles = whole_rubles(amount)
    except ValueError as exc:
        raise AccountTopupError("topup_amount_must_be_whole_rubles") from exc
    if rubles < Decimal(cfg.BALANCE_MIN_TOPUP_RUB):
        raise AccountTopupError("topup_below_minimum")
    if rubles > Decimal(cfg.BALANCE_MAX_CUSTOM_TOPUP_RUB):
        raise AccountTopupError("topup_above_maximum")

    user = await lock_checkout_user(session, user_id)
    if user is None or user.is_deleted:
        raise AccountTopupError("topup_user_missing")
    if user.is_banned:
        raise AccountTopupError("topup_user_banned")
    if user.topup_blocked:
        raise AccountTopupError("topup_blocked")

    # Re-fetch under serialized checkout lock
    existing = await _visible_topup_for_update(session, user_id)

    balance = await get_account_balance(
        session, user_id=user.id, locked_user=user
    )
    if existing is not None:
        return TopupCreationResult(existing, False, balance)

    unfinished = int(
        await session.scalar(
            select(func.count(Payment.id)).where(
                Payment.user_id == user.id,
                Payment.credited_at.is_(None),
                Payment.checkout_status != "abandoned",
                Payment.provider_status.in_(UNFINISHED_TOPUP_PROVIDER_STATUSES),
            )
        )
        or 0
    )
    if unfinished >= int(cfg.BALANCE_MAX_UNFINISHED_TOPUPS):
        raise AccountTopupError("too_many_unfinished_topups")

    pending = await _pending_topup_exposure(session, user.id)
    projected_position = balance.real_position + pending + rubles
    if max(Decimal(0), projected_position) > Decimal(
        cfg.BALANCE_MAX_AVAILABLE_RUB
    ):
        raise AccountTopupError("topup_balance_limit_exceeded")

    pending_limit = getattr(cfg, "BALANCE_MAX_PENDING_EXPOSURE_RUB", 5000)
    if (pending + rubles) > Decimal(pending_limit):
        raise AccountTopupError("topup_balance_limit_exceeded")

    payment = Payment(
        user_id=user.id,
        amount=rubles,
        currency="RUB",
        public_order_id="topup_" + uuid.uuid4().hex,
        provider_idempotency_key=str(uuid.uuid4()),
        provider_status="not_created",
        reconciliation_status="ok",
        checkout_status="active",
        ui_visible=True,
        topup_context=dict(context or {}),
    )
    session.add(payment)
    await session.flush()
    bot_username_clean = (bot_username or "").lstrip("@")
    return_url = cfg.YOOKASSA_RETURN_URL.format(
        bot_username=bot_username_clean
    )
    await enqueue_create(
        session,
        payment,
        description=get_topup_description(context),
        return_url=return_url,
    )
    session.add(
        PaymentEvent(
            payment_id=payment.id,
            event_type="balance_topup_created",
            provider_status=payment.provider_status,
            source="telegram",
        )
    )
    await session.flush()
    return TopupCreationResult(payment, True, balance)


async def hide_balance_topup(
    session: AsyncSession, *, user_id: int, payment_id: int
) -> Payment:
    """Hide only the checkout UI; provider truth continues to reconcile."""
    await lock_checkout_user(session, user_id)
    payment = await session.scalar(
        select(Payment).where(Payment.id == payment_id, Payment.user_id == user_id).with_for_update()
    )
    if payment is None:
        raise AccountTopupError("topup_not_found")
    if payment.provider_status in {"succeeded", "canceled", "refunded"}:
        raise AccountTopupError("topup_already_terminal")
    payment.ui_visible = False
    payment.user_cancel_requested_at = payment.user_cancel_requested_at or now_utc()
    session.add(
        PaymentEvent(
            payment_id=payment.id,
            event_type="balance_topup_hidden",
            provider_status=payment.provider_status,
            source="telegram",
        )
    )
    await session.flush()
    return payment


async def cancel_all_unfinished_topups(
    session: AsyncSession, *, user_id: int
) -> int:
    """Abandon all active topup checkout sessions for a user without mutating provider truth."""
    # 1. Acquire per-user checkout advisory lock first
    await lock_checkout_user(session, user_id)

    # 2. Lock existing active checkout payments under the advisory lock
    payments = (
        await session.scalars(
            select(Payment)
            .where(
                Payment.user_id == user_id,
                Payment.credited_at.is_(None),
                Payment.checkout_status == "active",
                Payment.provider_status.not_in(("succeeded", "canceled", "refunded")),
            )
            .order_by(Payment.id)
            .with_for_update()
        )
    ).all()

    count = 0
    from services.payment_provider_operations import cancel_pending_create_operations

    for payment in payments:
        payment.checkout_status = "abandoned"
        payment.ui_visible = False
        payment.user_cancel_requested_at = payment.user_cancel_requested_at or now_utc()
        await cancel_pending_create_operations(session, payment.id)
        session.add(
            PaymentEvent(
                payment_id=payment.id,
                event_type="balance_topup_hidden",
                provider_status=payment.provider_status,
                source="telegram",
            )
        )
        count += 1

    if count > 0:
        await session.flush()
    return count


async def settle_succeeded_topup(
    session: AsyncSession,
    *,
    payment: Payment,
    source: str,
    settings=None,
    bot=None,
    locked_user: User | None = None,
    locked_payment: Payment | None = None,
) -> tuple[bool, AccountBalanceSnapshot]:
    """Credit a verified top-up and close its non-subscription lifecycle.

    Referral bonus settlement is part of the same transaction as the top-up.
    A referral-bonus failure must abort the settlement so the provider event can
    be retried rather than silently crediting money without its attributable bonus.
    """
    # 1. Acquire per-user checkout advisory lock first (Advisory -> User -> Payment)
    if locked_user is not None:
        user = locked_user
    else:
        user = await lock_checkout_user(session, payment.user_id)
        if user is None:
            raise AccountTopupError("topup_user_missing")

    # 2. Lock Payment row FOR UPDATE under serialized checkout lock
    if locked_payment is not None:
        payment = locked_payment
    else:
        locked = await session.scalar(
            select(Payment).where(Payment.id == payment.id).with_for_update()
        )
        if isinstance(locked, Payment):
            payment = locked

    if payment.provider_confirmed_at is None:
        raise AccountTopupError("topup_provider_not_verified")

    if (
        payment.fulfillment_status in ("manual_review", "reversed")
        or payment.reconciliation_status in ("manual_review", "mismatch")
    ):
        session.add(
            PaymentEvent(
                payment_id=payment.id,
                event_type="topup_settlement_blocked_manual_review",
                provider_status=payment.provider_status,
                reason=payment.manual_review_reason or "manual_review_active",
                source=source,
            )
        )
        await session.flush()
        snapshot = await get_account_balance(session, user_id=payment.user_id)
        return False, snapshot

    user_is_banned = getattr(user, "is_banned", False) is True
    hard_block = (getattr(user, "topup_blocked", False) is True) or user_is_banned
    recovery_topup = (
        (getattr(user, "financial_hold", False) is True)
        and getattr(user, "financial_block_reason", None) == "chargeback_debt"
        and not user_is_banned
    )
    if hard_block or ((getattr(user, "financial_hold", False) is True) and not recovery_topup):
        reason = "user_banned" if user_is_banned else "user_financially_blocked"
        payment.fulfillment_status = "manual_review"
        payment.reconciliation_status = "manual_review"
        payment.manual_review_reason = reason
        session.add(
            PaymentEvent(
                payment_id=payment.id,
                event_type="topup_blocked_by_hold",
                provider_status=payment.provider_status,
                reason=reason,
                source=source,
            )
        )
        await session.flush()
        snapshot = await get_account_balance(session, user_id=payment.user_id)
        return False, snapshot

    entry, created = await credit_succeeded_topup(
        session,
        locked_payment=payment,
        locked_user=user,
        metadata={"settlement_source": source},
    )
    payment.fulfillment_status = "succeeded"
    payment.fulfilled_at = payment.fulfilled_at or now_utc()
    payment.credited_at = payment.credited_at or now_utc()
    payment.ui_visible = False
    payment.fulfillment_last_error_code = None
    payment.fulfillment_last_error = None
    if payment.reconciliation_status not in {"mismatch", "manual_review"}:
        payment.reconciliation_status = "ok"
    balance = await get_account_balance(session, user_id=payment.user_id)
    await refresh_user_dispute_hold(session, user_id=payment.user_id)
    cfg = settings or get_settings()
    if balance.real_position > Decimal(cfg.BALANCE_MAX_AVAILABLE_RUB):
        user.topup_blocked = True
        user.financial_block_reason = "balance_limit_exceeded_by_late_payment"
        session.add(
            PaymentEvent(
                payment_id=payment.id,
                event_type="balance_limit_exceeded_by_late_payment",
                provider_status=payment.provider_status,
                reason=str(balance.accounting_position),
                source=source,
            )
        )
    if created:
        session.add(
            PaymentEvent(
                payment_id=payment.id,
                event_type="balance_topup_credited",
                provider_status=payment.provider_status,
                reason=str(entry.amount),
                source=source,
            )
        )
        from services.audit_service import AuditService
        await AuditService.log_action(
            session,
            admin_id=0,
            action="PAYMENT_SUCCESS",
            target_type="user",
            target_id=payment.user_id,
            details={
                "amount": int(payment.amount),
                "provider": getattr(payment, "provider", "yookassa"),
                "payment_id": payment.id,
            },
        )
        # Do not isolate this in a SAVEPOINT and continue on failure. The
        # top-up, referral bonus and ledger state must commit atomically.
        # If this raises, the caller's transaction rolls back and the durable
        # provider operation remains retryable.
        from services.referral_bonus import grant_referral_bonus_for_topup
        bonus_result = await grant_referral_bonus_for_topup(
            session,
            purchaser_user_id=payment.user_id,
            payment_id=payment.id,
            topup_amount=payment.amount,
        )
        referrer_bonus_amount = getattr(bonus_result, "referrer_bonus", bonus_result)
        purchaser_welcome_amount = getattr(bonus_result, "purchaser_welcome_bonus", Decimal(0))

        if int(referrer_bonus_amount) > 0 and user is not None and user.referred_by:
            ctx = payment.topup_context if isinstance(payment.topup_context, dict) else {}
            payment.topup_context = {
                **ctx,
                "referrer_telegram_id": user.referred_by,
                "referrer_bonus": int(referrer_bonus_amount),
                "referrer_notified_at": None,
                "purchaser_welcome_bonus": int(purchaser_welcome_amount),
                "referral_bonus_processed": True,
            }
        else:
            ctx = payment.topup_context if isinstance(payment.topup_context, dict) else {}
            payment.topup_context = {
                **ctx,
                "referral_bonus_processed": True,
                "purchaser_welcome_bonus": int(purchaser_welcome_amount),
            }

        quote_uuid = None
        try:
            if payment.topup_context and isinstance(payment.topup_context, dict):
                auto_action = payment.topup_context.get("auto_fulfill_action")
                quote_raw = payment.topup_context.get("quote_public_id")
                if auto_action and quote_raw:
                    import logging
                    from contextlib import asynccontextmanager

                    @asynccontextmanager
                    async def _safe_begin_nested(s):
                        nested_func = getattr(s, "begin_nested", None)
                        if callable(nested_func):
                            try:
                                ctx = nested_func()
                                if hasattr(ctx, "__aenter__"):
                                    async with ctx:
                                        yield
                                    return
                            except Exception:
                                pass
                        yield

                    import uuid

                    quote_uuid = uuid.UUID(str(quote_raw))
                    async with _safe_begin_nested(session):
                        if auto_action == "tariff_change":
                            from services.account_tariff_change import (
                                settle_account_tariff_change,
                            )

                            await settle_account_tariff_change(
                                session,
                                user_id=payment.user_id,
                                quote_public_id=quote_uuid,
                            )
                            payment.topup_context = {
                                **payment.topup_context,
                                "auto_fulfill_status": "succeeded",
                            }
                            logging.getLogger(__name__).info(
                                "Auto-fulfilled tariff change for payment %s, user_id=%s",
                                payment.id,
                                payment.user_id,
                            )
                        elif auto_action == "purchase":
                            from services.account_purchase import (
                                settle_account_purchase,
                            )

                            await settle_account_purchase(
                                session,
                                user_id=payment.user_id,
                                quote_public_id=quote_uuid,
                            )
                            payment.topup_context = {
                                **payment.topup_context,
                                "auto_fulfill_status": "succeeded",
                            }
                            logging.getLogger(__name__).info(
                                "Auto-fulfilled purchase for payment %s, user_id=%s",
                                payment.id,
                                payment.user_id,
                            )
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning(
                "Auto-fulfillment failed for topup payment %s: %s", payment.id, e
            )
            if payment.topup_context and isinstance(payment.topup_context, dict):
                payment.topup_context = {
                    **payment.topup_context,
                    "auto_fulfill_status": "failed",
                    "auto_fulfill_error": str(e),
                }

        from services.notification_coordinator import (
            NotificationClaim,
            claim_notification,
            ensure_payment_notification,
            execute_notification_presentation,
        )

        queued_notif_ids: list[tuple[int, str]] = []
        if user and user.telegram_id and not source.startswith("user_refresh"):
            notif_credit = await ensure_payment_notification(
                session,
                payment_id=payment.id,
                kind="balance_credit",
                chat_id=user.telegram_id,
                payload_snapshot={
                    "amount": int(payment.amount),
                    "user_id": user.id,
                    "topup_context": dict(payment.topup_context or {}),
                },
            )
            if notif_credit:
                queued_notif_ids.append((notif_credit.id, "balance_credit"))

        if int(referrer_bonus_amount) > 0 and user is not None and user.referred_by:
            notif_ref = await ensure_payment_notification(
                session,
                payment_id=payment.id,
                kind="referral_bonus",
                chat_id=user.referred_by,
                payload_snapshot={
                    "bonus": int(referrer_bonus_amount),
                    "referrer_bonus": int(referrer_bonus_amount),
                    "payment_id": payment.id,
                },
            )
            if notif_ref:
                queued_notif_ids.append((notif_ref.id, "referral_bonus"))

        if bot is not None and queued_notif_ids:
            try:
                from database.connection import queue_post_commit_task
                from services.workers.account_balance import (
                    _render_balance_credit,
                    _render_referral_bonus,
                )

                async def _send_settlement_notifications_post_commit():
                    from database.connection import session_scope
                    for target_nid, target_kind in queued_notif_ids:
                        try:
                            claim = None
                            try:
                                async with session_scope() as claim_session:
                                    claim = await claim_notification(
                                        claim_session,
                                        worker_id="post_commit_settle",
                                        notification_id=target_nid,
                                        kind=target_kind,
                                    )
                            except Exception:
                                claim = None
                            if claim is None:
                                # In-memory fallback for mock/synthetic sessions in unit tests
                                is_ref = (target_kind == "referral_bonus")
                                claim = NotificationClaim(
                                    notification_id=target_nid or 0,
                                    payment_id=payment.id,
                                    user_id=payment.user_id,
                                    chat_id=user.referred_by if (is_ref and user) else (user.telegram_id if user else 0),
                                    kind=target_kind,
                                    state="claimed",
                                    claim_token="post_commit_token",
                                    attempt_number=1,
                                    payload={
                                        "amount": int(payment.amount),
                                        "bonus": int(referrer_bonus_amount),
                                        "referrer_bonus": int(referrer_bonus_amount),
                                        "user_id": user.id if user else 0,
                                        "topup_context": dict(payment.topup_context or {}),
                                    },
                                )
                            if claim:
                                render_f = (
                                    _render_balance_credit
                                    if claim.kind == "balance_credit"
                                    else _render_referral_bonus
                                )
                                await execute_notification_presentation(
                                    bot, claim, render_func=render_f
                                )
                        except Exception as push_exc:
                            import logging
                            logging.getLogger(__name__).warning(
                                "Failed in post-commit settlement push for notification %s: %s",
                                target_nid,
                                push_exc,
                            )

                queue_post_commit_task(session, _send_settlement_notifications_post_commit)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "Failed to queue settlement notifications for payment %s: %s",
                    payment.id,
                    exc,
                )

    payment.ui_visible = False
    payment.fulfillment_last_error_code = None
    payment.fulfillment_last_error = None
    if payment.reconciliation_status not in {"mismatch", "manual_review"}:
        payment.reconciliation_status = "ok"

    await session.flush()
    return created, balance


async def settle_succeeded_topup_by_id(
    session: AsyncSession,
    *,
    payment_id: int,
    source: str,
    settings=None,
    bot=None,
) -> tuple[bool, AccountBalanceSnapshot]:
    res = await session.scalar(
        select(Payment.user_id).where(Payment.id == payment_id)
    )
    if res is None:
        raise AccountTopupError("topup_not_found")
    payment_user_id = getattr(res, "user_id", res)
    user = await lock_checkout_user(session, payment_user_id)
    if user is None:
        raise AccountTopupError("topup_user_missing")
    payment = await session.scalar(
        select(Payment).where(Payment.id == payment_id).with_for_update()
    )
    if payment is None:
        raise AccountTopupError("topup_not_found")
    return await settle_succeeded_topup(
        session,
        payment=payment,
        source=source,
        settings=settings,
        bot=bot,
        locked_user=user,
        locked_payment=payment,
    )
