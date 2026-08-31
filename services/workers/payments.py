"""Periodic recovery and alerting for stalled balance top-ups."""

import asyncio
import logging
import uuid
from datetime import timedelta

from aiogram import Bot
from bot.texts.common.status import STATUS_NOT_SPECIFIED
from bot.texts.runtime.alerts import (
    ALERT_STALE_BTN_DISMISS,
    ALERT_STALE_BTN_OPEN_CARD,
    ALERT_STALE_BTN_PAYMENTS,
    ALERT_STALE_BTN_QUEUES,
    ALERT_STALE_PAYMENT_ROW,
    ALERT_STALE_PAYMENTS_HEADER,
    ALERT_STALE_PAYMENTS_MORE,
)
from cachetools import TTLCache
from sqlalchemy import and_, or_, select
from config.constants import STALE_PAYMENT_THRESHOLD, WORKER_ERROR_SLEEP_INTERVAL
from config.settings import get_settings
from database.connection import session_scope
from database.models import Payment, User
from services.account_topup import settle_succeeded_topup_by_id
from services.payment_provider_operations import ensure_reconcile_payment_operation
from services.payment_status import payment_display_status
from services.referral_bonus import grant_referral_bonus_for_topup
from utils.datetime_helpers import now_utc

logger = logging.getLogger("BackgroundWorker")
_alerted_stale_payments: TTLCache[int, bool] = TTLCache(maxsize=50000, ttl=7200)
# Chat-hygiene state for the stale payments card: one editable message per
# admin instead of a new message every reminder cycle, plus the last rendered
# text so unchanged reminders stay silent.
_stale_alert_message_ids: dict[int, int] = {}
_stale_alert_last_text: dict[int, str] = {}
PAYMENTS_START_DELAY = 60.0

AUTO_FULFILL_MAX_ATTEMPTS = 5
# Unambiguously permanent settlement failures: retrying can never succeed
# because the frozen quote/user/tariff state contradicts the settlement
# preconditions. Deliberately retryable (may legitimately resolve, bounded
# by the 5-attempt cap): financial_hold, too_many_devices, account_debt,
# insufficient_balance, change_cooldown_active.
_AUTO_FULFILL_PERMANENT_CODES = {
    "quote_expired",
    "quote_not_found",
    "quote_not_active",
    "quote_operation_mismatch",
    "quote_price_mismatch",
    "consumed_quote_incomplete",
    "user_not_found",
    "purchase_user_missing",
    "purchase_user_ineligible",
    "tariff_unavailable",
    "tariff_price_changed",
    "subscription_state_changed",
    "change_user_ineligible",
    "quote_amount_invalid",
    "quote_tariff_version_invalid",
    "quote_currency_invalid",
    "quote_economics_invalid",
    "quote_economics_changed",
    "subscription_balance_untracked",
    "quote_source_history_changed",
    "active_quote_has_existing_debit",
    "paid_value_ledger_conflict",
    "invalid_auto_fulfill_action",
    "invalid_auto_fulfill_attempts",
    "auto_fulfill_attempts_exhausted",
    "missing_quote_public_id",
}


def _needs_attention():
    return or_(
        Payment.provider_status.in_(("creating", "pending", "waiting_for_capture", "unknown")),
        Payment.reconciliation_status.in_(("required", "mismatch", "manual_review")),
        Payment.fulfillment_status.in_(("failed", "manual_review")),
    )


def _needs_recovery():
    from sqlalchemy import or_, select, text
    from sqlalchemy.orm import aliased

    from database.models import User

    purchaser = aliased(User)
    referrer = aliased(User)

    user_subquery = (
        select(1)
        .select_from(purchaser)
        .join(referrer, referrer.telegram_id == purchaser.referred_by)
        .where(
            purchaser.id == Payment.user_id,
            purchaser.is_deleted.is_(False),
            purchaser.referred_by.is_not(None),
            purchaser.referred_by != purchaser.telegram_id,
            referrer.is_deleted.is_(False),
            referrer.is_banned.is_(False),
            referrer.id != purchaser.id,
        )
    )

    needs_bonus_retry = and_(
        text("payments.provider_status = 'succeeded'"),
        Payment.provider_confirmed_at.is_not(None),
        text("payments.fulfillment_status = 'succeeded'"),
        user_subquery.exists(),
        text("NOT (COALESCE(payments.topup_context, '{}'::jsonb) @> '{\"referral_bonus_processed\": true}'::jsonb)"),
    )

    needs_auto_fulfill_retry = and_(
        text("payments.provider_status = 'succeeded'"),
        text("payments.provider_confirmed_at IS NOT NULL"),
        text("payments.fulfillment_status = 'succeeded'"),
        text("payments.topup_context ? 'auto_fulfill_action'"),
        text("payments.topup_context->>'auto_fulfill_status' = 'failed'"),
    )

    return or_(
        text("payments.external_id IS NOT NULL AND payments.provider_status IN ('creating', 'pending', 'waiting_for_capture', 'unknown')"),
        text("payments.provider_status = 'succeeded' AND payments.provider_confirmed_at IS NOT NULL AND payments.fulfillment_status NOT IN ('succeeded', 'reversed', 'manual_review')"),
        needs_bonus_retry,
        needs_auto_fulfill_retry,
    )


async def _preload_alerted_stale_payments():
    try:
        async with session_scope() as session:
            threshold = now_utc() - timedelta(hours=1)
            ids = await session.scalars(
                select(Payment.id)
                .where(_needs_attention(), Payment.created_at < threshold)
                .order_by(Payment.created_at.desc())
                .limit(10000)
            )
            for payment_id in ids:
                _alerted_stale_payments[payment_id] = True
    except Exception as exc:
        logger.warning("Failed to preload stale payment IDs: %s", exc)


async def stale_payments_checker_loop(bot: Bot, shutdown_event: asyncio.Event):
    settings = get_settings()
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=PAYMENTS_START_DELAY)
        return
    except asyncio.TimeoutError:
        pass
    await _preload_alerted_stale_payments()
    while not shutdown_event.is_set():
        try:
            await _recover_stale_topups(bot)
            await _alert_new_stale_payments(bot, settings)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Stale top-up worker failed")
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(), timeout=WORKER_ERROR_SLEEP_INTERVAL
                )
                break
            except asyncio.TimeoutError:
                continue
        try:
            await asyncio.wait_for(
                shutdown_event.wait(), timeout=STALE_PAYMENT_THRESHOLD
            )
            break
        except asyncio.TimeoutError:
            continue


async def _recover_stale_topups(bot: Bot | None = None):
    max_batches = 10
    batch_size = 100
    last_processed_id = 0

    for _ in range(max_batches):
        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(Payment.id, Payment.user_id)
                    .where(
                        Payment.id > last_processed_id,
                        _needs_recovery(),
                        Payment.created_at < now_utc() - timedelta(minutes=5),
                    )
                    .order_by(Payment.id.asc())
                    .limit(batch_size)
                )
            ).all()

        if not rows:
            break

        for pid, _uid in rows:
            last_processed_id = pid
            try:
                async with session_scope() as session:
                    payment = await session.scalar(
                        select(Payment)
                        .where(Payment.id == pid)
                        .with_for_update(skip_locked=True)
                    )
                    if not payment:
                        continue

                    ctx = payment.topup_context or {}
                    
                    if payment.external_id and payment.provider_status in {
                        "creating",
                        "pending",
                        "waiting_for_capture",
                        "unknown",
                    }:
                        await ensure_reconcile_payment_operation(
                            session, payment, reason="stale_topup_worker"
                        )

                    if (
                        payment.provider_status == "succeeded"
                        and payment.provider_confirmed_at is not None
                        and payment.fulfillment_status
                        not in {"succeeded", "reversed", "manual_review"}
                    ):
                        await settle_succeeded_topup_by_id(
                            session,
                            payment_id=payment.id,
                            source="stale_topup_recovery",
                            bot=bot,
                        )

                    # Referral bonuses are ledger entries with their own idempotency key.
                    # Retry them in a savepoint so a transient bonus failure cannot roll
                    # back a verified user payment. The next worker pass retries it.
                    if (
                        payment.provider_status == "succeeded"
                        and payment.provider_confirmed_at is not None
                        and payment.fulfillment_status == "succeeded"
                        and not (payment.topup_context and payment.topup_context.get("referral_bonus_processed"))
                    ):
                        try:
                            async with session.begin_nested():
                                await grant_referral_bonus_for_topup(
                                    session,
                                    purchaser_user_id=payment.user_id,
                                    payment_id=payment.id,
                                    topup_amount=payment.amount,
                                )
                                ctx = payment.topup_context or {}
                                payment.topup_context = {**ctx, "referral_bonus_processed": True}
                        except Exception:
                            logger.exception(
                                "Referral bonus retry failed for topup payment %s",
                                payment.id,
                            )

                    # Auto-fulfillment retry lane: a paid purchase/renew/tariff change
                    # whose settlement savepoint failed (transient error, or the
                    # provider webhook arrived after the quote's 15-minute lifetime).
                    # Without this lane the payment stays "succeeded" forever while
                    # the subscription is never granted.
                    if (
                        payment.provider_status == "succeeded"
                        and payment.provider_confirmed_at is not None
                        and payment.fulfillment_status == "succeeded"
                        and isinstance(payment.topup_context, dict)
                        and payment.topup_context.get("auto_fulfill_action")
                        and payment.topup_context.get("auto_fulfill_status") == "failed"
                    ):
                        await _retry_auto_fulfillment(session, payment)
            except Exception:
                logger.exception("Failed to recover stale topup payment %s", pid)


async def _retry_auto_fulfillment(session, payment: Payment) -> None:
    """Retry a failed auto-fulfillment inside a savepoint with bounded attempts."""
    from services.account_purchase import (
        AccountPurchaseError,
        settle_account_purchase,
    )
    from services.account_tariff_change import (
        AccountTariffChangeError,
        settle_account_tariff_change,
    )

    ctx = dict(payment.topup_context or {})
    action = ctx.get("auto_fulfill_action")
    quote_raw = ctx.get("quote_public_id")
    attempts = 0

    def _mark(status: str, error=None) -> None:
        current = dict(payment.topup_context or {})
        update = {
            **current,
            "auto_fulfill_status": status,
            "auto_fulfill_attempts": attempts,
        }
        if error is not None:
            update["auto_fulfill_error"] = str(error)[:500]
        elif status == "succeeded":
            # Stale durable telemetry must not outlive a successful retry.
            update.pop("auto_fulfill_error", None)
        payment.topup_context = update

    # The attempts counter lives in durable JSONB and is written only by this
    # worker as an integer. Anything else (str, float, dict, list, bool) is a
    # poisoned recovery record and must dead-letter instead of crashing the
    # recovery transaction forever or being silently coerced by `or 0`.
    raw_attempts = ctx.get("auto_fulfill_attempts", 0)
    if isinstance(raw_attempts, bool) or not isinstance(raw_attempts, int):
        _mark("dead", "invalid_auto_fulfill_attempts")
        logger.error(
            "Auto-fulfillment dead for payment %s: poisoned auto_fulfill_attempts %r",
            payment.id,
            raw_attempts,
        )
        return
    # The bounded-retry invariant is checked BEFORE any settlement attempt:
    # a corrupted-but-int counter must never buy an extra attempt, and a
    # negative counter must never disable the cap.
    if raw_attempts < 0:
        _mark("dead", "invalid_auto_fulfill_attempts")
        logger.error(
            "Auto-fulfillment dead for payment %s: corrupted negative attempts %r",
            payment.id,
            raw_attempts,
        )
        return
    if raw_attempts >= AUTO_FULFILL_MAX_ATTEMPTS:
        _mark("dead", "auto_fulfill_attempts_exhausted")
        logger.error(
            "Auto-fulfillment dead for payment %s: attempts counter at cap (%r)",
            payment.id,
            raw_attempts,
        )
        return
    attempts = raw_attempts + 1

    if not quote_raw:
        _mark("dead", "missing_quote_public_id")
        logger.error(
            "Auto-fulfillment dead for payment %s: missing quote_public_id",
            payment.id,
        )
        return
    # Fail-closed: an unknown durable action must never be silently executed
    # as a purchase by the recovery subsystem. The isinstance guard comes
    # FIRST: a poisoned JSONB list/dict would raise TypeError (unhashable)
    # in the set-membership test, crash outside the try block and poison the
    # recovery lane forever.
    if not isinstance(action, str) or action not in {"purchase", "tariff_change"}:
        _mark("dead", "invalid_auto_fulfill_action")
        logger.error(
            "Auto-fulfillment dead for payment %s: invalid auto_fulfill_action %r",
            payment.id,
            action,
        )
        return
    try:
        quote_uuid = uuid.UUID(str(quote_raw))
        async with session.begin_nested():
            if action == "tariff_change":
                await settle_account_tariff_change(
                    session,
                    user_id=payment.user_id,
                    quote_public_id=quote_uuid,
                )
            else:
                await settle_account_purchase(
                    session,
                    user_id=payment.user_id,
                    quote_public_id=quote_uuid,
                )
        _mark("succeeded")
        logger.info(
            "Auto-fulfillment retry succeeded for payment %s (action=%s)",
            payment.id,
            action,
        )
    except (AccountPurchaseError, AccountTariffChangeError) as exc:
        code = getattr(exc, "code", "") or str(exc)
        if code in _AUTO_FULFILL_PERMANENT_CODES or attempts >= AUTO_FULFILL_MAX_ATTEMPTS:
            _mark("dead", code)
            logger.error(
                "Auto-fulfillment retry dead for payment %s: %s",
                payment.id,
                code,
            )
        else:
            _mark("failed", code)
    except Exception as exc:
        if attempts >= AUTO_FULFILL_MAX_ATTEMPTS:
            _mark("dead", type(exc).__name__)
            logger.exception(
                "Auto-fulfillment retry dead for payment %s", payment.id
            )
        else:
            _mark("failed", type(exc).__name__)
            logger.warning(
                "Auto-fulfillment retry failed for payment %s (attempt %s/%s): %s",
                payment.id,
                attempts,
                AUTO_FULFILL_MAX_ATTEMPTS,
                type(exc).__name__,
            )


async def _alert_new_stale_payments(bot: Bot, settings):
    threshold = now_utc() - timedelta(hours=1)
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Payment, User.telegram_id)
                .join(User, Payment.user_id == User.id)
                .where(_needs_attention(), Payment.created_at < threshold)
                .order_by(Payment.created_at.desc())
                .limit(1000)
            )
        ).all()
    if not rows:
        return
    # The persistent card renders the CURRENT stale-payment snapshot (not
    # just first-time rows), so resolved rows leave the card and unresolved
    # ones never silently disappear when a newer payment appears.
    new_rows = [
        (payment, telegram_id)
        for payment, telegram_id in rows
        if payment.id not in _alerted_stale_payments
    ]
    details = []
    for payment, telegram_id in rows[:10]:
        icon = "⚠️" if payment_display_status(payment) == "requires_manual_review" else "⏳"
        method = payment.payment_method or STATUS_NOT_SPECIFIED
        details.append(
            ALERT_STALE_PAYMENT_ROW.format(
                icon=icon,
                payment_id=payment.id,
                telegram_id=telegram_id,
                amount=payment.amount,
                currency=payment.currency,
                method=method,
            )
        )
    if len(rows) > 10:
        details.append(
            ALERT_STALE_PAYMENTS_MORE.format(
                more_count=len(rows) - 10,
            )
        )
    message = ALERT_STALE_PAYMENTS_HEADER.format(
        count=len(rows),
        details="".join(details),
    )
    markup = _build_stale_alert_markup(rows)
    delivered_any = False
    for admin_id in settings.ADMIN_IDS:
        try:
            # Chat hygiene: never spam new messages. One persistent card per
            # admin; unchanged snapshots are silent, changed state edits the
            # card in place. Dismiss removes it until the state changes.
            last_text = _stale_alert_last_text.get(admin_id)
            if last_text == message:
                continue
            delivered = False
            old_id = _stale_alert_message_ids.get(admin_id)
            if old_id is not None:
                try:
                    await bot.edit_message_text(
                        chat_id=admin_id,
                        message_id=old_id,
                        text=message,
                        reply_markup=markup,
                        parse_mode="HTML",
                    )
                    delivered = True
                except Exception:
                    delivered = False
            if not delivered:
                sent = await bot.send_message(
                    admin_id, message, reply_markup=markup, parse_mode="HTML"
                )
                _stale_alert_message_ids[admin_id] = sent.message_id
            _stale_alert_last_text[admin_id] = message
            delivered_any = True
        except Exception as exc:
            logger.error("Stale alert failed to %s: %s", admin_id, exc)
    # A payment counts as alerted only when at least one admin actually
    # received/updated the card; otherwise it retries on the next cycle
    # instead of disappearing behind the TTL cache during a Telegram outage.
    if delivered_any:
        for payment, _ in new_rows:
            _alerted_stale_payments[payment.id] = True


def dismiss_stale_alert_card(admin_id: int) -> None:
    """Dismiss semantics: hide the card now, but keep the last rendered
    snapshot — an unchanged state stays silent, a changed state re-delivers
    a fresh card (the old message id is dropped, so the next delivery is a
    new message after the admin deleted the card)."""
    _stale_alert_message_ids.pop(admin_id, None)


def _build_stale_alert_markup(rows):
    """Actionable keyboard: per-payment card for the first rows, queue
    diagnostics, payments list and a dismiss button."""
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    builder = InlineKeyboardBuilder()
    for payment, _ in rows[:5]:
        builder.button(
            text=ALERT_STALE_BTN_OPEN_CARD.format(payment_id=payment.id),
            callback_data=f"admin_payment_card:{payment.id}",
        )
    builder.button(text=ALERT_STALE_BTN_QUEUES, callback_data="aq:home")
    builder.button(text=ALERT_STALE_BTN_PAYMENTS, callback_data="admin_payments")
    builder.button(text=ALERT_STALE_BTN_DISMISS, callback_data="stale_alerts:dismiss")
    builder.adjust(1)
    return builder.as_markup()
