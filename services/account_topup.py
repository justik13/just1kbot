"""Balance top-up creation, hiding, and exactly-once settlement."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.models import Payment, PaymentEvent
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
    amount = await session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.user_id == user_id,
            Payment.credited_at.is_(None),
            Payment.provider_status.in_(UNFINISHED_TOPUP_PROVIDER_STATUSES),
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

    # To respect global Payment -> User lock hierarchy, we MUST lock Payment first
    existing = await _visible_topup_for_update(session, user_id)

    user = await lock_checkout_user(session, user_id)
    if user is None or user.is_deleted:
        raise AccountTopupError("topup_user_missing")
    if user.is_banned:
        raise AccountTopupError("topup_user_banned")
    if user.topup_blocked:
        raise AccountTopupError("topup_blocked")

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
                Payment.provider_status.in_(UNFINISHED_TOPUP_PROVIDER_STATUSES),
            )
        )
        or 0
    )
    if unfinished >= cfg.BALANCE_MAX_UNFINISHED_TOPUPS:
        raise AccountTopupError("too_many_unfinished_topups")

    pending = await _pending_topup_exposure(session, user.id)
    projected_position = balance.real_position + pending + rubles
    if max(Decimal(0), projected_position) > Decimal(
        cfg.BALANCE_MAX_AVAILABLE_RUB
    ):
        raise AccountTopupError("topup_balance_limit_exceeded")

    payment = Payment(
        user_id=user.id,
        amount=rubles,
        currency="RUB",
        public_order_id="topup_" + uuid.uuid4().hex,
        provider_idempotency_key=uuid.uuid4().hex,
        provider_status="creating",
        fulfillment_status="not_ready",
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
            source="account_topup",
        )
    )
    await session.flush()
    return TopupCreationResult(payment, True, balance)


async def hide_balance_topup(
    session: AsyncSession, *, user_id: int, payment_id: int
) -> Payment:
    """Hide only the checkout UI; provider truth continues to reconcile."""
    payment = await session.scalar(
        select(Payment).where(Payment.id == payment_id).with_for_update()
    )
    if payment is None or payment.user_id != user_id:
        raise AccountTopupError("topup_not_found")
    await lock_checkout_user(session, user_id)
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
    """Force cancel all unfinished topups for a user."""
    # To prevent deadlocks with provider webhooks/pollers, we MUST lock Payment BEFORE User.
    payments = (
        await session.scalars(
            select(Payment)
            .where(
                Payment.user_id == user_id,
                Payment.credited_at.is_(None),
                Payment.provider_status.in_(UNFINISHED_TOPUP_PROVIDER_STATUSES),
            )
            .order_by(Payment.id)  # Lock multiple payments in a deterministic order
            .with_for_update()
        )
    ).all()
    await lock_checkout_user(session, user_id)

    count = 0
    for payment in payments:
        if payment.provider_status in {"succeeded", "canceled", "refunded"}:
            continue
        payment.provider_status = "canceled"
        payment.checkout_status = "abandoned"
        payment.ui_visible = False
        payment.user_cancel_requested_at = payment.user_cancel_requested_at or now_utc()
        session.add(
            PaymentEvent(
                payment_id=payment.id,
                event_type="balance_topup_hidden",
                provider_status="canceled",
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
) -> tuple[bool, AccountBalanceSnapshot]:
    """Credit a verified top-up and close its non-subscription lifecycle.

    Referral bonus settlement is part of the same transaction as the top-up.
    A referral-bonus failure must abort the settlement so the provider event can
    be retried rather than silently crediting money without its attributable bonus.
    """
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

    user = await lock_checkout_user(session, payment.user_id)
    if user is None:
        raise AccountTopupError("topup_user_missing")

    hard_block = user.topup_blocked
    recovery_topup = (
        user.financial_hold
        and user.financial_block_reason == "chargeback_debt"
    )
    if hard_block or (user.financial_hold and not recovery_topup):
        payment.fulfillment_status = "manual_review"
        payment.reconciliation_status = "manual_review"
        payment.manual_review_reason = "user_financially_blocked"
        session.add(
            PaymentEvent(
                payment_id=payment.id,
                event_type="topup_blocked_by_hold",
                provider_status=payment.provider_status,
                reason="user_financially_blocked",
                source=source,
            )
        )
        await session.flush()
        snapshot = await get_account_balance(session, user_id=payment.user_id)
        return False, snapshot

    entry, created = await credit_succeeded_topup(
        session,
        locked_payment=payment,
        metadata={"settlement_source": source},
    )
    payment.fulfillment_status = "succeeded"
    payment.fulfilled_at = payment.fulfilled_at or now_utc()
    payment.ui_visible = False
    payment.fulfillment_last_error_code = None
    payment.fulfillment_last_error = None
    if payment.reconciliation_status not in {"mismatch", "manual_review"}:
        payment.reconciliation_status = "ok"
    balance = await get_account_balance(session, user_id=payment.user_id)
    await refresh_user_dispute_hold(session, user_id=payment.user_id)
    cfg = settings or get_settings()
    if balance.real_position > Decimal(cfg.BALANCE_MAX_AVAILABLE_RUB):
        user = await lock_checkout_user(session, payment.user_id)
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
    auto_fulfilled_action = None
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
            if bot is not None:
                try:
                    from aiogram.utils.keyboard import InlineKeyboardBuilder
                    b_builder = InlineKeyboardBuilder()
                    b_builder.button(text="🎁 Мой баланс", callback_data="menu_balance")
                    ref_text = f"🎉 <b>Ваш реферал пополнил баланс!</b>\n\nВам зачислено <b>+{int(referrer_bonus_amount)} ₽</b> бонусов на баланс."
                    ref_markup = b_builder.as_markup()
                    ref_target = user.referred_by
                    target_payment_id = payment.id

                    async def _send_ref_push():
                        try:
                            from utils.telegram import EFFECT_FIRE, render_hub
                            await render_hub(
                                bot,
                                ref_target,
                                ref_text,
                                ref_markup,
                                message_effect_id=EFFECT_FIRE,
                            )
                            from database.connection import session_scope
                            async with session_scope() as notify_session:
                                p = await notify_session.get(Payment, target_payment_id)
                                if p and p.topup_context and isinstance(p.topup_context, dict):
                                    if p.topup_context.get("referrer_notified_at") is None:
                                        p.topup_context = {
                                            **p.topup_context,
                                            "referrer_notified_at": now_utc().isoformat(),
                                        }
                        except Exception as exc:
                            import logging
                            logging.getLogger(__name__).warning("Failed to send referrer push notification to %s: %s", ref_target, exc)

                    from database.connection import queue_post_commit_task
                    queue_post_commit_task(session, _send_ref_push)
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning("Failed to queue referrer push notification to %s: %s", user.referred_by, exc)
        else:
            ctx = payment.topup_context if isinstance(payment.topup_context, dict) else {}
            payment.topup_context = {
                **ctx, 
                "referral_bonus_processed": True,
                "purchaser_welcome_bonus": int(purchaser_welcome_amount),
            }
        try:
            if payment.topup_context and isinstance(payment.topup_context, dict):
                auto_action = payment.topup_context.get("auto_fulfill_action")
                quote_raw = payment.topup_context.get("quote_public_id")
                if auto_action and quote_raw:
                    import logging
                    from contextlib import asynccontextmanager

                    @asynccontextmanager
                    async def _safe_begin_nested(s):
                        if callable(getattr(s, "begin_nested", None)):
                            nested = s.begin_nested()
                            if hasattr(nested, "__aenter__"):
                                async with nested:
                                    yield
                                return
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
                            auto_fulfilled_action = "tariff_change"
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
                            auto_fulfilled_action = "purchase"
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

        if bot is not None and user is not None and user.telegram_id:
            try:
                from aiogram.utils.keyboard import InlineKeyboardBuilder
                builder = InlineKeyboardBuilder()

                if auto_fulfilled_action == "tariff_change":
                    text = (
                        "🎉 <b>Оплата получена и тариф успешно обновлен!</b>\n\n"
                        "Ваш новый тариф активирован. Настройки подписки и подключений обновлены."
                    )
                    builder.button(text="📱 Мои подключения", callback_data="menu_connections")
                    builder.button(text="📋 Подписка", callback_data="menu_subscription")
                elif auto_fulfilled_action == "purchase":
                    text = (
                        "🎉 <b>Оплата получена и подписка успешно оформлена!</b>\n\n"
                        "Ваши VPN-ключи и настройки подключений доступны в меню «Мои подключения»."
                    )
                    builder.button(text="📱 Мои подключения", callback_data="menu_connections")
                    builder.button(text="📋 Подписка", callback_data="menu_subscription")
                else:
                    text = (
                        f"✅ <b>Баланс пополнен на +{int(payment.amount)} ₽!</b>\n\n"
                        f"💰 Баланс: <b>{int(balance.real_available)} ₽</b>"
                    )
                    if balance.bonus_available > 0:
                        text += f"\n🎁 Бонусный баланс: <b>{int(balance.bonus_available)} ₽</b>"
                    if (
                        payment.topup_context
                        and isinstance(payment.topup_context, dict)
                        and payment.topup_context.get("purchaser_welcome_bonus", 0) > 0
                    ):
                        wb = payment.topup_context["purchaser_welcome_bonus"]
                        text += (
                            f"\n\n🎁 <b>Вам начислен приветственный бонус +{wb} ₽ "
                            f"за первое пополнение по приглашению!</b>"
                        )
                    builder.button(text="💰 Мой баланс", callback_data="menu_balance")
                    builder.button(text="📦 Купить подписку", callback_data="payment_showcase")
                    builder.button(text="🏠 Главное меню", callback_data="back_to_main_menu")

                builder.adjust(1)
                push_text = text
                push_markup = builder.as_markup()
                target_user_id = user.telegram_id
                target_payment_id = payment.id
                target_quote_uuid = quote_uuid if auto_fulfilled_action else None

                from utils.telegram import EFFECT_CONFETTI

                async def _send_topup_push():
                    try:
                        from utils.telegram import render_hub
                        await render_hub(
                            bot,
                            target_user_id,
                            push_text,
                            push_markup,
                            message_effect_id=EFFECT_CONFETTI,
                        )
                        from database.connection import session_scope
                        async with session_scope() as notify_session:
                            p = await notify_session.get(Payment, target_payment_id)
                            if p and p.credit_notified_at is None:
                                p.credit_notified_at = now_utc()
                            if target_quote_uuid:
                                from sqlalchemy import select

                                from database.models import TariffQuote
                                q = await notify_session.scalar(
                                    select(TariffQuote).where(TariffQuote.public_id == target_quote_uuid)
                                )
                                if q and q.purchase_notified_at is None:
                                    q.purchase_notified_at = now_utc()
                    except Exception as exc:
                        import logging
                        logging.getLogger(__name__).warning("Failed to send push notification via render_hub to user %s: %s", target_user_id, exc)

                from database.connection import queue_post_commit_task

                # When the user manually triggers reconciliation (balance_check),
                # the handler renders the balance screen itself; queuing the push
                # here too would double-render the same chat (flicker + extra API calls).
                if source.startswith("user_refresh"):
                    import logging

                    logging.getLogger(__name__).info(
                        "Skip topup self-push for user %s: manual refresh renders balance screen",
                        user.telegram_id,
                    )
                else:
                    queue_post_commit_task(session, _send_topup_push)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning("Failed to queue push notification for user %s: %s", user.telegram_id, exc)

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
    payment = await session.scalar(
        select(Payment).where(Payment.id == payment_id).with_for_update()
    )
    if payment is None:
        raise AccountTopupError("topup_not_found")
    return await settle_succeeded_topup(
        session, payment=payment, source=source, settings=settings, bot=bot
    )
