import logging
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.middlewares.user_context import invalidate_user_cache
from database.connection import queue_post_commit_task, session_scope
from database.models import Payment, TariffQuote, User
from database.repositories.payments_repo import (
    create_payment,
    get_payment_by_id,
    get_payment_by_id_for_update,
    get_payment_by_id_simple,
    get_user_payments,
)
from database.repositories.tariffs_repo import get_tariff_by_id
from database.repositories.tariff_quotes_repo import CheckoutQuoteConflictError, get_or_create_checkout_quote, lock_checkout_user
from services.audit_service import AuditService
from services.yookassa_service import YooKassaService
from services.subscription import SubscriptionService
from utils.datetime_helpers import now_utc
from utils.formatters import format_datetime

from .alerts import (
    _notify_client_chargeback_now,
    _notify_client_manual_review_now,
    _notify_client_paid_after_cancel_now,
    _send_cancel_after_completed_alert_now,
    _send_chargeback_alert_now,
    _send_manual_review_alert_now,
    _send_paid_after_cancel_alert_now,
)
from .common import (
    MANUAL_GRANT_ALLOWED_STATUSES,
    _build_payment_snapshot,
    _get_payment_snapshot_device_limit,
    _get_payment_snapshot_duration,
    _get_redis,
    _safe_decimal,
    _to_decimal,
)

try:
    from database.repositories.payments_repo import log_payment_event
except Exception:
    log_payment_event = None

logger = logging.getLogger(__name__)


async def _log_event_safe(
    session: AsyncSession,
    payment_id: int,
    event_type: str,
    *,
    provider_status: str | None = None,
    reason: str | None = None,
    source: str | None = None,
    details: str | None = None,
) -> None:
    if log_payment_event is None:
        return
    try:
        async with session.begin_nested():
            await log_payment_event(
                session,
                payment_id,
                event_type,
                provider_status=provider_status,
                reason=reason,
                source=source,
                details=details,
            )
    except Exception as e:
        logger.warning(
            "Failed to log payment event %s for payment %s: %s",
            event_type,
            payment_id,
            e,
        )


async def _invalidate_cache_task(telegram_id: int) -> None:
    invalidate_user_cache(telegram_id)


async def _notify_payment_success(
    telegram_id: int,
    tariff_name: str,
    valid_until: str,
) -> None:
    from aiogram.exceptions import TelegramForbiddenError
    from bot.keyboards import get_payment_success_keyboard
    from database.repositories.users_repo import mark_user_bot_blocked
    from services.workers.heartbeat import get_bot_ref
    from utils.telegram import render_hub

    bot = get_bot_ref()
    if bot is None:
        return

    msg = (
        f"✅ <b>Доступ активирован!</b>\n"
        f"{'─' * 20}\n"
        f"💎 <b>Тариф:</b> {tariff_name}\n"
        f"📅 <b>Действует до:</b> {valid_until}\n"
        f"{'─' * 20}\n"
        f"Спасибо за покупку! 🎉"
    )
    try:
        await render_hub(
            bot,
            telegram_id,
            msg,
            get_payment_success_keyboard(),
        )
    except TelegramForbiddenError:
        try:
            async with session_scope() as session:
                await mark_user_bot_blocked(session, telegram_id)
        except Exception as e:
            logger.warning(
                "mark_user_bot_blocked failed in notify_payment_success: %s",
                e,
                exc_info=True,
            )
    except Exception as e:
        logger.error(
            "Failed to send payment success notification to %s: %s",
            telegram_id,
            e,
        )


class PaymentService:
    @staticmethod
    async def _apply_payment_snapshot(
        session: AsyncSession,
        payment: Payment,
        tariff,
    ) -> None:
        if not tariff:
            return
        snapshot_fields = {
            "snapshot_duration_days": getattr(
                tariff, "duration_days", None
            ),
            "snapshot_device_limit": getattr(
                tariff, "device_limit", None
            ),
            "snapshot_amount": payment.amount,
            "snapshot_currency": payment.currency,
        }
        changed = False
        for field_name, field_value in snapshot_fields.items():
            if hasattr(payment, field_name):
                setattr(payment, field_name, field_value)
                changed = True
        if changed:
            await session.flush()

    @staticmethod
    async def _mark_manual_review_direct(
        session: AsyncSession,
        payment: Payment,
        reason: str,
        source: str,
    ) -> None:
        payment.status = "requires_manual_review"
        payment.manual_review_reason = reason
        payment.fulfillment_status = "manual_review"
        await session.flush()

        await _log_event_safe(
            session,
            payment.id,
            "manual_review",
            reason=reason,
            source=source,
        )

        snapshot = _build_payment_snapshot(payment)

        await AuditService.log_action(
            session,
            admin_id=0,
            action="PAYMENT_MANUAL_REVIEW",
            target_type="Payment",
            target_id=payment.id,
            details=f"reason={reason}, source={source}",
        )

        queue_post_commit_task(
            session,
            lambda s=snapshot, r=reason, src=source: (
                _send_manual_review_alert_now(s, r, src)
            ),
        )

        queue_post_commit_task(
            session,
            lambda s=snapshot: (
                _notify_client_manual_review_now(s)
            ),
        )

    # ──────────────────────────────────────────────────────────
    # ВОЗВРАЩЁН: был случайно удалён в коммите 0318fc1,
    # но вызовы в handle_successful_payment и
    # handle_yookassa_callback остались.
    # Без этого метода → AttributeError при webhook.
    # ──────────────────────────────────────────────────────────
    @staticmethod
    async def _mark_paid_after_cancel(
        session: AsyncSession,
        payment: Payment,
        source: str,
    ) -> tuple:
        if (
            payment.status == "requires_manual_review"
            and payment.manual_review_reason == "paid_after_cancel"
        ):
            return True, "paid_after_cancel"

        snapshot = _build_payment_snapshot(payment)

        await _log_event_safe(
            session,
            payment.id,
            "paid_after_cancel",
            source=source,
        )

        payment.status = "requires_manual_review"
        payment.manual_review_reason = "paid_after_cancel"
        if not payment.paid_at:
            payment.paid_at = now_utc()
        await session.flush()

        await AuditService.log_action(
            session,
            admin_id=0,
            action="PAID_AFTER_CANCEL",
            target_type="Payment",
            target_id=payment.id,
            details=(
                f"user={snapshot.get('user_telegram_id')}, "
                f"amount={snapshot.get('amount')} "
                f"{snapshot.get('currency')}, source={source}"
            ),
        )

        queue_post_commit_task(
            session,
            lambda s=snapshot: (
                _send_paid_after_cancel_alert_now(s)
            ),
        )
        queue_post_commit_task(
            session,
            lambda s=snapshot: (
                _notify_client_paid_after_cancel_now(s)
            ),
        )

        return True, "paid_after_cancel"

    @staticmethod
    async def handle_successful_payment(session: AsyncSession, payment_id: int, notify_user: bool = True) -> tuple:
        """Compatibility orchestration only; entitlement is worker-owned."""
        from services.workers.webhook_inbox import ensure_fulfillment
        payment=await get_payment_by_id_for_update(session,payment_id)
        if not payment:return False,"not_found"
        if payment.provider_status!="succeeded":return False,"provider_not_succeeded"
        from services.payment_kind import is_tariff_change_payment
        if await is_tariff_change_payment(session,payment):
            return False,"tariff_change_legacy_grant_forbidden"
        await ensure_fulfillment(session,payment,"grant_subscription")
        return True,"queued"

    @staticmethod
    async def force_grant_payment(session: AsyncSession, payment_id: int, admin_id: int, *, force_without_provider_confirmation: bool = False) -> tuple:
        from database.models import PaymentFulfillmentOperation
        from services.payment_fulfillment import retry_dead_fulfillment_operation
        from services.payment_lifecycle import project_legacy_status
        payment=await get_payment_by_id_for_update(session,payment_id)
        if not payment:return False,"Платёж не найден"
        from services.payment_kind import is_tariff_change_payment
        if await is_tariff_change_payment(session,payment):
            return False,"tariff_change_legacy_grant_forbidden"
        operation=await session.scalar(select(PaymentFulfillmentOperation).where(PaymentFulfillmentOperation.idempotency_key==f"payment-grant:{payment.id}").with_for_update())
        if operation and operation.status=="processing":return False,"already_processing"
        if operation and operation.status=="succeeded":return True,"already_succeeded"
        if payment.provider_status!="succeeded" and not force_without_provider_confirmation:return False,"Требуется явное force_without_provider_confirmation=True"
        payload={"manual_without_provider_confirmation":force_without_provider_confirmation,"admin_id":admin_id}
        if operation and operation.status=="dead":
            await retry_dead_fulfillment_operation(session,operation.id,reset_attempts=True,reason=f"manual grant by {admin_id}"); operation.payload=payload
        elif operation and operation.status=="cancelled":
            operation.status="retry"; operation.attempts=0; operation.completed_at=operation.locked_at=operation.locked_by=None; operation.last_error_code=operation.last_error=None; operation.next_attempt_at=now_utc(); operation.payload=payload
        elif operation and operation.status in {"pending","retry"}: operation.payload=payload
        elif not operation:
            operation=PaymentFulfillmentOperation(payment_id=payment.id,operation_type="grant_subscription",idempotency_key=f"payment-grant:{payment.id}",status="pending",payload=payload,next_attempt_at=now_utc()); session.add(operation)
        if force_without_provider_confirmation:
            payment.reconciliation_status="manual_review"
            await _log_event_safe(session,payment.id,"manual_grant_without_provider_confirmation",source="force_grant_payment",details=f"admin_id={admin_id}")
        payment.fulfillment_status="pending"; project_legacy_status(payment)
        await AuditService.log_action(session,admin_id=admin_id,action="MANUAL_GRANT_QUEUED",target_type="Payment",target_id=payment.id,details="force_without_provider_confirmation="+str(force_without_provider_confirmation))
        await session.flush(); return True,"Выдача поставлена в очередь"

    @staticmethod
    async def create_yookassa_payment(session: AsyncSession,user_id:int,tariff_id:int,amount:Decimal,telegram_id:int,bot_username:str)->tuple:
        """Commit local order and immutable command before any provider HTTP."""
        import uuid
        from config.settings import get_settings
        from services.payment_provider_operations import enqueue_create
        decimal_amount=_to_decimal(amount); tariff=await get_tariff_by_id(session,tariff_id)
        if decimal_amount is None or not tariff: return None,None
        user = await lock_checkout_user(session, user_id)
        if user is None: return None,"checkout_user_missing"
        from services.checkout_conflicts import get_unfinished_financial_checkouts, is_valid_reusable_purchase_intent
        conflicts = await get_unfinished_financial_checkouts(session, user_id=user_id)
        if conflicts:
            if len(conflicts) == 1 and await is_valid_reusable_purchase_intent(
                    session, conflicts[0], user_id=user_id, tariff_id=tariff_id):
                return conflicts[0].payment, conflicts[0].payment.payment_url
            return None,"unfinished_checkout_exists"
        active = bool(user and user.subscription_end and user.subscription_end > now_utc())
        if active and user.current_tariff_id is not None:
            current_tariff = await get_tariff_by_id(session, user.current_tariff_id)
            if current_tariff is None:
                # Тариф удалён → явная ошибка вместо молчаливого renew
                return None, "current_tariff_deleted"
            current_limit = getattr(current_tariff, "device_limit", 0)
            new_limit = getattr(tariff, "device_limit", 0)
            if current_limit != new_limit:
                # Foundation only: a later PR will consume a confirmed change quote.
                return None, "active_tariff_change_temporarily_unavailable"
        operation_type = "renew" if active and user.current_tariff_id is not None else "purchase"
        try:
            quote, version = await get_or_create_checkout_quote(
                session, user_id=user_id, tariff=tariff, operation_type=operation_type,
            )
        except CheckoutQuoteConflictError as exc:
            if str(exc) == "active_tariff_change_quote_exists":
                return None, "active_tariff_change_quote_exists"
            return None,"active_checkout_quote_conflict"
        existing = await session.scalar(select(Payment).where(
            Payment.tariff_quote_id == quote.id,
            Payment.checkout_status == "active",
        ).order_by(Payment.id.desc()).limit(1))
        if existing:
            return existing, existing.payment_url
        # The caller's amount is intentionally not authoritative.
        decimal_amount = Decimal(version.price_rub)
        payment=await create_payment(
            session,user_id,tariff_id,decimal_amount,"RUB",
            snapshot_duration_days=version.duration_hours // 24,
            snapshot_device_limit=version.device_limit,
            snapshot_amount=version.price_rub,snapshot_currency=version.currency,
            tariff_quote_id=quote.id,tariff_version_id=version.id,
        )
        quote.payment_id = payment.id
        payment.public_order_id="pay_"+uuid.uuid4().hex; payment.provider_idempotency_key=uuid.uuid4().hex
        payment.provider_status="creating"; payment.fulfillment_status="not_ready"; payment.reconciliation_status="ok"
        settings=get_settings(); description=f"Предоставление доступа к вычислительному серверу ({version.name_snapshot}, {version.duration_hours // 24} дн.)"
        return_url=settings.YOOKASSA_RETURN_URL.format(bot_username=bot_username.lstrip("@"))
        operation=await enqueue_create(session,payment,description,return_url)
        await _log_event_safe(session,payment.id,"payment_created",source="yookassa")
        await session.commit()  # worker performs HTTP after this durability boundary
        return payment,payment.payment_url

    @staticmethod
    async def handle_yookassa_callback(transaction_id: str, status: str, payload: str = "", callback_amount=None, callback_currency=None) -> tuple:
        """Deprecated boundary: production webhook persists WebhookInbox directly."""
        logger.warning("Legacy YooKassa callback ignored transaction=%s; use webhook inbox",transaction_id)
        return False,"deprecated_use_webhook_inbox"

    @staticmethod
    async def check_yookassa_payment(session: AsyncSession, payment_id: int, notify_user: bool = True) -> tuple:
        from services.payment_provider_operations import ensure_reconcile_payment_operation
        from services.workers.webhook_inbox import ensure_fulfillment
        payment=await get_payment_by_id_for_update(session,payment_id)
        if not payment:return False,{"error":"not_found"}
        if payment.external_id and payment.provider_status not in {"refunded","canceled"}: await ensure_reconcile_payment_operation(session,payment,reason="user_refresh")
        from services.payment_kind import is_tariff_change_payment
        is_change=await is_tariff_change_payment(session,payment)
        if payment.provider_status=="succeeded" and not is_change and payment.fulfillment_status not in {"succeeded","reversed","manual_review"}: await ensure_fulfillment(session,payment,"grant_subscription")
        return True,{"provider_status":payment.provider_status,"fulfillment_status":payment.fulfillment_status,"reconciliation_status":payment.reconciliation_status}

    @staticmethod
    async def cancel_payment_via_api(session: AsyncSession, payment_id: int) -> bool:
        from services.payment_provider_operations import ensure_cancel_payment_operation, ensure_reconcile_payment_operation
        from database.models import PaymentProviderOperation
        payment=await get_payment_by_id_for_update(session,payment_id)
        if not payment or payment.provider_status in {"succeeded","refunded","canceled"}:return False
        payment.checkout_status="abandoned"; payment.user_cancel_requested_at=now_utc(); payment.payment_url=None
        if payment.tariff_quote_id:
            quote=await session.scalar(select(TariffQuote).where(
                TariffQuote.id==payment.tariff_quote_id).with_for_update())
            if quote and quote.status=="active":
                quote.status="cancelled"
                quote.diagnostic_reason="checkout_abandoned_by_user"
        create_op=await session.scalar(select(PaymentProviderOperation).where(PaymentProviderOperation.payment_id==payment.id,PaymentProviderOperation.operation_type=="create_payment").with_for_update())
        if not payment.external_id:
            if create_op and create_op.status=="pending" and create_op.attempts==0:
                create_op.status="cancelled"; create_op.completed_at=now_utc(); payment.provider_status="not_created"
            elif create_op is None and not payment.provider_required:
                payment.provider_status="not_created"
            elif create_op and create_op.attempts>0: payment.reconciliation_status="required"
            return True
        if payment.provider_status=="waiting_for_capture": await ensure_cancel_payment_operation(session,payment)
        else: await ensure_reconcile_payment_operation(session,payment,reason="checkout_abandoned")
        return True

    @staticmethod
    async def _set_manual_review(
        session: AsyncSession,
        payment_id: int,
        reason: str,
        source: str,
    ) -> tuple:
        stmt = (
            update(Payment)
            .where(
                Payment.id == payment_id,
                Payment.status.in_(
                    ["pending", "failed", "cancelled"]
                ),
            )
            .values(
                status="requires_manual_review",
                manual_review_reason=reason,
            )
        )
        result = await session.execute(stmt)
        await session.flush()

        if result.rowcount == 0:
            current = await session.get(Payment, payment_id)
            if current and current.status == "completed":
                return True, "already_processed"
            if current and current.status == "requires_manual_review":
                return True, "manual_review"
            return (
                False,
                current.status if current else "not_found",
            )

        payment = await get_payment_by_id(session, payment_id)
        await _log_event_safe(
            session,
            payment_id,
            "manual_review",
            reason=reason,
            source=source,
        )

        snapshot = _build_payment_snapshot(payment)

        await AuditService.log_action(
            session,
            admin_id=0,
            action="PAYMENT_MANUAL_REVIEW",
            target_type="Payment",
            target_id=payment_id,
            details=(
                f"reason={reason}, source={source}, "
                f"user={payment.user_id if payment else '—'}"
            ),
        )

        queue_post_commit_task(
            session,
            lambda s=snapshot, r=reason, src=source: (
                _send_manual_review_alert_now(s, r, src)
            ),
        )

        queue_post_commit_task(
            session,
            lambda s=snapshot: (
                _notify_client_manual_review_now(s)
            ),
        )

        return True, "manual_review"
