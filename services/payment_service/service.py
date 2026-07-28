import logging
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.middlewares.user_context import invalidate_user_cache
from database.connection import queue_post_commit_task, session_scope
from database.models import Payment, User
from database.repositories.payments_repo import (
    create_payment,
    get_payment_by_id,
    get_payment_by_id_for_update,
    get_payment_by_id_simple,
    get_user_payments,
)
from database.repositories.tariffs_repo import get_tariff_by_id
from services.audit_service import AuditService
from services.yookassa_service import YooKassaService
from services.profile_deletion_service import ProfileDeletionService
from services.referral_service import ReferralService
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
        except Exception:
            pass
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
    async def handle_successful_payment(
        session: AsyncSession,
        payment_id: int,
        notify_user: bool = True,
    ) -> tuple:
        payment_obj = await session.get(Payment, payment_id)
        if not payment_obj:
            return False, "not_found"

        redis_lock = None
        acquired = False
        try:
            redis = await _get_redis()
            user_lock_key = (
                f"lock:payment_bonus:{payment_obj.user_id}"
            )
            redis_lock = redis.lock(
                user_lock_key,
                timeout=30,
                blocking_timeout=15,
            )
            acquired = await redis_lock.acquire()
        except Exception as e:
            logger.warning(
                "Payment %s: Redis unavailable: %s",
                payment_id,
                e,
            )
            redis_lock = None
            acquired = False

        try:
            async with session.begin_nested():
                payment = await get_payment_by_id_for_update(
                    session,
                    payment_id,
                )
                if not payment:
                    return False, "not_found"

                if payment.status == "completed":
                    return True, "already_processed"

                if payment.status == "cancelled":
                    return (
                        await PaymentService._mark_paid_after_cancel(
                            session,
                            payment,
                            "handle_successful_payment",
                        )
                    )

                if payment.status == "refunded":
                    return False, "refunded"

                if payment.status == "requires_manual_review":
                    return False, "manual_review"

                if payment.status == "failed":
                    await PaymentService._mark_manual_review_direct(
                        session,
                        payment,
                        "status_failed",
                        "handle_successful_payment",
                    )
                    return False, "manual_review"

                user = payment.user
                tariff = payment.tariff

                # ── ДОБАВЛЕНО: FOR UPDATE fallback при Redis down ──
                if not acquired and user:
                    await session.execute(
                        select(User.id)
                        .where(User.id == user.id)
                        .with_for_update()
                    )

                manual_review_reason = None
                duration_days = _get_payment_snapshot_duration(payment)
                device_limit = _get_payment_snapshot_device_limit(
                    payment
                )

                if not user:
                    manual_review_reason = "missing_tariff_or_user"
                elif user.is_deleted or user.is_banned:
                    manual_review_reason = "banned_or_deleted"
                elif payment.amount is None or payment.amount <= 0:
                    manual_review_reason = "amount_missing"
                elif duration_days is None or device_limit is None:
                    manual_review_reason = "missing_snapshot"
                elif tariff and not tariff.is_active:
                    manual_review_reason = "inactive_tariff"

                if manual_review_reason:
                    await PaymentService._mark_manual_review_direct(
                        session,
                        payment,
                        manual_review_reason,
                        "handle_successful_payment",
                    )
                    return False, "manual_review"

                payment.status = "completed"
                payment.paid_at = now_utc()
                await session.flush()

                await _log_event_safe(
                    session,
                    payment.id,
                    "completed",
                    source="handle_successful_payment",
                )

                try:
                    await SubscriptionService.extend_subscription(
                        session,
                        user.telegram_id,
                        duration_days,
                        new_device_limit=device_limit,
                        new_tariff_id=(tariff.id if tariff else None),
                    )
                except ValueError:
                    await PaymentService._mark_manual_review_direct(
                        session,
                        payment,
                        "device_limit_exceeded",
                        "handle_successful_payment_extend",
                    )
                    return False, "manual_review"
                except Exception as e:
                    logger.error(
                        "Payment %s: extend error: %s",
                        payment_id,
                        e,
                        exc_info=True,
                    )
                    await PaymentService._mark_manual_review_direct(
                        session,
                        payment,
                        "status_failed",
                        "handle_successful_payment_extend",
                    )
                    return False, "manual_review"

                payments = await get_user_payments(session, user.id)
                successful_payments = [
                    p for p in payments if p.status == "completed"
                ]
                is_first_payment = len(successful_payments) == 1

                user_bonus_days = 0
                referrer_bonus_days = 0
                if user.referred_by:
                    try:
                        user_bonus_days, referrer_bonus_days = (
                            await ReferralService.process_bonus(
                                session,
                                user.telegram_id,
                                user.referred_by,
                                is_first_payment=is_first_payment,
                                duration_days=duration_days,
                            )
                        )
                    except Exception as e:
                        logger.warning(
                            "Referral bonus failed for payment %s: %s",
                            payment_id,
                            e,
                        )

                payment.referral_user_bonus_days = user_bonus_days
                payment.referral_referrer_bonus_days = (
                    referrer_bonus_days
                )
                user.last_payment_at = now_utc()
                await session.flush()

                telegram_id_for_cache = user.telegram_id
                queue_post_commit_task(
                    session,
                    lambda tid=telegram_id_for_cache: (
                        _invalidate_cache_task(tid)
                    ),
                )

                tariff_display = (
                    f"{duration_days} дн. / {device_limit} устр."
                )
                valid_until_str = (
                    format_datetime(user.subscription_end)
                    if user.subscription_end
                    else "—"
                )

                if notify_user:
                    queue_post_commit_task(
                        session,
                        lambda tid=user.telegram_id,
                              tn=tariff_display,
                              vu=valid_until_str: (
                                  _notify_payment_success(tid, tn, vu)
                              ),
                    )

                try:
                    await AuditService.log_action(
                        session,
                        admin_id=0,
                        action="PAYMENT_SUCCESS",
                        target_type="Payment",
                        target_id=payment_id,
                        details=(
                            f"user={user.telegram_id}, "
                            f"amount={payment.amount} "
                            f"{payment.currency}"
                        ),
                    )
                except Exception as e:
                    logger.error(
                        "Failed to log payment success: %s", e
                    )

                return True, "success"

        except Exception as e:
            logger.error(
                "Failed to process payment %s: %s",
                payment_id,
                e,
                exc_info=True,
            )
            return False, "error"
        finally:
            if redis_lock is not None and acquired:
                try:
                    await redis_lock.release()
                except Exception:
                    pass

    @staticmethod
    async def force_grant_payment(session: AsyncSession, payment_id: int, admin_id: int, *, force_without_provider_confirmation: bool = False) -> tuple:
        """Audit and enqueue an idempotent grant; never mutate entitlement here."""
        from database.models import PaymentFulfillmentOperation
        from services.payment_lifecycle import project_legacy_status
        payment = await get_payment_by_id_for_update(session, payment_id)
        if not payment: return False, "Платёж не найден"
        if payment.provider_status != "succeeded" and not force_without_provider_confirmation:
            return False, "Требуется явное force_without_provider_confirmation=True"
        if force_without_provider_confirmation:
            payment.provider_status="succeeded"; payment.reconciliation_status="manual_review"
            await _log_event_safe(session,payment.id,"manual_grant_without_provider_confirmation",source="force_grant_payment",details=f"admin_id={admin_id}")
        payment.fulfillment_status="pending"; project_legacy_status(payment)
        existing=await session.scalar(select(PaymentFulfillmentOperation).where(PaymentFulfillmentOperation.idempotency_key==f"payment-grant:{payment.id}"))
        if existing and existing.status in {"dead","cancelled"}: existing.status="retry"; existing.next_attempt_at=now_utc()
        elif not existing: session.add(PaymentFulfillmentOperation(payment_id=payment.id,operation_type="grant_subscription",idempotency_key=f"payment-grant:{payment.id}",status="pending",payload={"manual":True,"admin_id":admin_id},next_attempt_at=now_utc()))
        await AuditService.log_action(session,admin_id=admin_id,action="MANUAL_GRANT_QUEUED",target_type="Payment",target_id=payment.id,details="force_without_provider_confirmation="+str(force_without_provider_confirmation))
        await session.flush(); return True, "Выдача поставлена в очередь"

    @staticmethod
    async def create_yookassa_payment(session: AsyncSession,user_id:int,tariff_id:int,amount:Decimal,telegram_id:int,bot_username:str)->tuple:
        """Commit local order and immutable command before any provider HTTP."""
        import uuid
        from config.settings import get_settings
        from services.payment_provider_operations import enqueue_create, execute
        decimal_amount=_to_decimal(amount); tariff=await get_tariff_by_id(session,tariff_id)
        if decimal_amount is None or not tariff: return None,None
        payment=await create_payment(session,user_id,tariff_id,decimal_amount,"RUB")
        payment.public_order_id="pay_"+uuid.uuid4().hex; payment.provider_idempotency_key=uuid.uuid4().hex
        payment.provider_status="creating"; payment.fulfillment_status="not_ready"; payment.reconciliation_status="ok"
        await PaymentService._apply_payment_snapshot(session,payment,tariff)
        settings=get_settings(); description=f"Предоставление доступа к вычислительному серверу ({tariff.name}, {tariff.duration_days} дн.)"
        return_url=settings.YOOKASSA_RETURN_URL.format(bot_username=bot_username.lstrip("@"))
        operation=await enqueue_create(session,payment,description,return_url)
        await _log_event_safe(session,payment.id,"payment_created",source="yookassa")
        await session.commit()  # explicit durability boundary before executor HTTP
        try:
            operation=await session.get(type(operation),operation.id)
            await execute(session,operation); await session.commit(); await session.refresh(payment)
        except Exception:
            logger.exception("Immediate provider execution failed; durable worker will retry payment=%s",payment.id)
        return payment,payment.payment_url

    @staticmethod
    async def handle_yookassa_callback(
        transaction_id: str,
        status: str,
        payload: str,
        callback_amount: Decimal | None = None,
        callback_currency: str | None = None,
    ) -> tuple:
        if callback_amount is not None:
            callback_amount = _safe_decimal(callback_amount)

        api_data = None
        if callback_amount is None or callback_currency is None:
            api_data = await YooKassaService.get_payment(
                transaction_id
            )
            if api_data:
                api_amount = api_data.get("amount", {})
                if isinstance(api_amount, dict):
                    if (
                        api_amount.get("value")
                        and callback_amount is None
                    ):
                        callback_amount = _safe_decimal(
                            api_amount["value"]
                        )
                    if (
                        api_amount.get("currency")
                        and callback_currency is None
                    ):
                        callback_currency = api_amount["currency"]

        async with session_scope() as session:
            try:
                await session.execute(
                    text("SET LOCAL statement_timeout = '30s'")
                )
            except Exception as e:
                logger.warning(
                    "Failed to set statement_timeout: %s", e
                )

            stmt = (
                select(Payment)
                .options(
                    selectinload(Payment.user),
                    selectinload(Payment.tariff),
                )
                .where(Payment.external_id == transaction_id)
                .with_for_update()
            )
            result = await session.execute(stmt)
            payment = result.scalar_one_or_none()

            if not payment:
                return False, "not_found"

            await _log_event_safe(
                session,
                payment.id,
                "provider_callback",
                provider_status=status,
                source="yookassa_callback",
                details=f"transaction_id={transaction_id}",
            )

            if status == "CONFIRMED":
                if payment.status == "completed":
                    return True, "already_processed"

                if payment.status == "cancelled":
                    return (
                        await PaymentService._mark_paid_after_cancel(
                            session,
                            payment,
                            "yookassa_callback",
                        )
                    )

                if payment.status == "refunded":
                    return False, "refunded"

                if callback_amount is None:
                    await PaymentService._set_manual_review(
                        session,
                        payment.id,
                        "amount_missing",
                        source="yookassa_callback",
                    )
                    return False, "manual_review"

                callback_decimal = _safe_decimal(callback_amount)
                if callback_decimal is None:
                    await PaymentService._set_manual_review(
                        session,
                        payment.id,
                        "amount_mismatch",
                        source="yookassa_callback",
                    )
                    return False, "manual_review"

                if payment.amount != callback_decimal:
                    logger.error(
                        "YooKassa amount mismatch: DB=%s, "
                        "callback=%s, payment_id=%s",
                        payment.amount,
                        callback_decimal,
                        payment.id,
                    )
                    await PaymentService._set_manual_review(
                        session,
                        payment.id,
                        "amount_mismatch",
                        source="yookassa_callback",
                    )
                    return False, "manual_review"

                if callback_currency:
                    cb_cur = str(callback_currency).upper()
                    db_cur = str(payment.currency).upper()
                    if db_cur != cb_cur:
                        await PaymentService._set_manual_review(
                            session,
                            payment.id,
                            "currency_mismatch",
                            source="yookassa_callback",
                        )
                        return False, "manual_review"

                expected_payload = f"payment_{payment.id}"
                if (
                    payload not in (None, "")
                    and payload != expected_payload
                ):
                    await PaymentService._set_manual_review(
                        session,
                        payment.id,
                        "payload_mismatch",
                        source="yookassa_callback",
                    )
                    return False, "manual_review"

                success, result_code = (
                    await PaymentService.handle_successful_payment(
                        session,
                        payment.id,
                    )
                )
                return success, result_code

            elif status == "CANCELED":
                if payment.status == "refunded":
                    return True, "already_processed"
                if payment.status == "cancelled":
                    return True, "already_processed"

                if payment.status == "completed":
                    snapshot = _build_payment_snapshot(payment)
                    await _log_event_safe(
                        session,
                        payment.id,
                        "cancel_after_completed",
                        provider_status=status,
                        source="yookassa_callback",
                    )
                    await AuditService.log_action(
                        session,
                        admin_id=0,
                        action="PAYMENT_CANCEL_AFTER_COMPLETED",
                        target_type="Payment",
                        target_id=payment.id,
                        details=(
                            f"transaction={transaction_id}, "
                            f"user={payment.user_id}"
                        ),
                    )
                    queue_post_commit_task(
                        session,
                        lambda s=snapshot, tid=transaction_id: (
                            _send_cancel_after_completed_alert_now(
                                s, tid
                            )
                        ),
                    )
                    return True, "manual_review"

                payment.status = "cancelled"
                await session.flush()
                await _log_event_safe(
                    session,
                    payment.id,
                    "cancelled",
                    provider_status=status,
                    source="yookassa_callback",
                )
                try:
                    await AuditService.log_action(
                        session,
                        admin_id=0,
                        action="PAYMENT_CANCELLED",
                        target_type="Payment",
                        target_id=payment.id,
                        details=(
                            f"YooKassa callback: "
                            f"transaction={transaction_id}"
                        ),
                    )
                except Exception:
                    pass
                return True, "success"

            elif status == "CHARGEBACKED":
                return await PaymentService._process_chargeback(
                    session,
                    payment.id,
                    transaction_id,
                )

            return False, "error"

    @staticmethod
    async def check_yookassa_payment(
        session: AsyncSession,
        payment_id: int,
        notify_user: bool = True,
    ) -> tuple:
        payment = await get_payment_by_id(session, payment_id)
        if not payment or not payment.external_id:
            return False, "not_found"

        if payment.status == "completed":
            return True, "success"

        if payment.status == "cancelled":
            api_data = await YooKassaService.get_payment(
                payment.external_id
            )
            if api_data:
                provider_status = api_data.get("status")
                if provider_status == "succeeded":
                    amount_obj = api_data.get("amount", {})
                    cb_amount = _safe_decimal(
                        amount_obj.get("value")
                    )
                    if cb_amount is None:
                        await PaymentService._set_manual_review(
                            session,
                            payment.id,
                            "amount_missing",
                            source="check_yookassa_cancelled",
                        )
                        return False, "manual_review"
                    if payment.amount != cb_amount:
                        await PaymentService._set_manual_review(
                            session,
                            payment.id,
                            "amount_mismatch",
                            source="check_yookassa_cancelled",
                        )
                        return False, "manual_review"
                    cb_currency = amount_obj.get("currency")
                    if cb_currency:
                        if (
                            str(payment.currency).upper()
                            != str(cb_currency).upper()
                        ):
                            await PaymentService._set_manual_review(
                                session,
                                payment.id,
                                "currency_mismatch",
                                source="check_yookassa_cancelled",
                            )
                            return False, "manual_review"
                    return (
                        await PaymentService.handle_successful_payment(
                            session,
                            payment.id,
                            notify_user=notify_user,
                        )
                    )
                if provider_status == "canceled":
                    return False, "cancelled"
            return False, "cancelled"

        if payment.status == "requires_manual_review":
            return False, "manual_review"

        if payment.status == "refunded":
            return False, "refunded"

        if payment.status != "pending":
            return False, "invalid_status"

        api_data = await YooKassaService.get_payment(
            payment.external_id
        )
        if not api_data:
            return False, "api_error"

        provider_status = api_data.get("status")
        if provider_status == "succeeded":
            amount_obj = api_data.get("amount", {})
            cb_amount = _safe_decimal(amount_obj.get("value"))
            if cb_amount is None:
                await PaymentService._set_manual_review(
                    session,
                    payment.id,
                    "amount_missing",
                    source="check_yookassa_payment",
                )
                return False, "manual_review"
            if payment.amount != cb_amount:
                await PaymentService._set_manual_review(
                    session,
                    payment.id,
                    "amount_mismatch",
                    source="check_yookassa_payment",
                )
                return False, "manual_review"
            cb_currency = amount_obj.get("currency")
            if cb_currency:
                if (
                    str(payment.currency).upper()
                    != str(cb_currency).upper()
                ):
                    await PaymentService._set_manual_review(
                        session,
                        payment.id,
                        "currency_mismatch",
                        source="check_yookassa_payment",
                    )
                    return False, "manual_review"
            return await PaymentService.handle_successful_payment(
                session,
                payment.id,
                notify_user=notify_user,
            )
        elif provider_status == "canceled":
            if payment.status == "completed":
                snapshot = _build_payment_snapshot(payment)
                tid = payment.external_id or "—"
                await _log_event_safe(
                    session,
                    payment.id,
                    "cancel_after_completed",
                    provider_status=provider_status,
                    source="check_yookassa_payment",
                )
                queue_post_commit_task(
                    session,
                    lambda s=snapshot, t=tid: (
                        _send_cancel_after_completed_alert_now(s, t)
                    ),
                )
                return False, "manual_review"
            if payment.status != "cancelled":
                payment.status = "cancelled"
                await session.flush()
            return False, "cancelled"

        return False, "pending"

    @staticmethod
    async def cancel_payment_via_api(
        session: AsyncSession,
        payment_id: int,
    ) -> bool:
        payment = await get_payment_by_id_simple(session, payment_id)
        if not payment or not payment.external_id:
            return False
        if payment.status != "pending":
            return False
        result = await YooKassaService.cancel_payment(
            payment.external_id,
            reason="Cancelled by user in bot",
        )
        return result is not None

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
                paid_at=None,
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

    @staticmethod
    async def _process_chargeback(
        session: AsyncSession,
        payment_id: int,
        transaction_id: str,
    ) -> tuple:
        try:
            async with session.begin_nested():
                payment = await get_payment_by_id_for_update(
                    session,
                    payment_id,
                )
                if not payment:
                    return False, "not_found"

                if payment.status == "refunded":
                    return True, "already_processed"

                was_completed = payment.status == "completed"

                payment.status = "refunded"
                payment.manual_review_reason = None
                await session.flush()

                await _log_event_safe(
                    session,
                    payment.id,
                    "chargeback",
                    provider_status="CHARGEBACKED",
                    source="payment_service",
                    details=(
                        f"transaction_id={transaction_id}"
                    ),
                )

                user = payment.user
                if user and was_completed:
                    current_time = now_utc()

                    snapshot_days = (
                        payment.snapshot_duration_days
                    )
                    if (
                        snapshot_days
                        and user.subscription_end
                        and user.subscription_end.year < 2100
                    ):
                        new_end = (
                            user.subscription_end
                            - timedelta(days=snapshot_days)
                        )
                        if new_end < current_time:
                            new_end = current_time
                        user.subscription_end = new_end

                        if new_end <= current_time:
                            user.current_tariff_id = None
                            user.device_limit = 0
                    else:
                        user.subscription_end = current_time
                        user.current_tariff_id = None
                        user.device_limit = 0

                    await session.flush()

                    referrer_bonus = (
                        payment.referral_referrer_bonus_days or 0
                    )
                    if referrer_bonus > 0 and user.referred_by:
                        try:
                            referrer_stmt = (
                                select(User)
                                .where(
                                    User.telegram_id
                                    == user.referred_by,
                                    User.is_deleted == False,
                                )
                                .with_for_update()
                            )
                            referrer = await session.scalar(
                                referrer_stmt
                            )
                            if referrer:
                                old_referral_days = (
                                    referrer.referral_days or 0
                                )
                                referrer.referral_days = max(
                                    0,
                                    old_referral_days
                                    - referrer_bonus,
                                )
                                if (
                                    referrer.subscription_end
                                    and referrer.subscription_end
                                    > current_time
                                    and referrer.subscription_end.year
                                    < 2100
                                ):
                                    referrer.subscription_end = (
                                        referrer.subscription_end
                                        - timedelta(
                                            days=referrer_bonus
                                        )
                                    )
                        except Exception as e:
                            logger.error(
                                "Chargeback referral rollback "
                                "failed: %s",
                                e,
                                exc_info=True,
                            )

                    # Откат бонусных дней самого пользователя (referral_user_bonus_days)
                    user_bonus = payment.referral_user_bonus_days or 0
                    if user_bonus > 0 and user.subscription_end:
                        old_user_subscription_end = user.subscription_end
                        user.subscription_end = max(
                            current_time,
                            user.subscription_end
                            - timedelta(days=user_bonus),
                        )
                        logger.info(
                            "Chargeback: откат бонуса пользователя %s: "
                            "%s дн. (%s → %s)",
                            user.telegram_id,
                            user_bonus,
                            format_datetime(old_user_subscription_end),
                            format_datetime(user.subscription_end),
                        )

                    try:
                        await ProfileDeletionService.delete_profiles_for_user(
                            session,
                            user.id,
                            reason="chargeback_delete",
                            background=True,
                        )
                    except Exception as e:
                        logger.error(
                            "Chargeback profile delete failed: %s",
                            e,
                            exc_info=True,
                        )

                    telegram_id_for_cache = user.telegram_id
                    queue_post_commit_task(
                        session,
                        lambda tid=telegram_id_for_cache: (
                            _invalidate_cache_task(tid)
                        ),
                    )

                snapshot = _build_payment_snapshot(payment)

                try:
                    await AuditService.log_action(
                        session,
                        admin_id=0,
                        action="PAYMENT_CHARGEBACK",
                        target_type="Payment",
                        target_id=payment.id,
                        details=(
                            f"YooKassa chargeback: "
                            f"transaction={transaction_id}, "
                            f"was_completed={was_completed}"
                        ),
                    )
                except Exception:
                    pass

                queue_post_commit_task(
                    session,
                    lambda s=snapshot, tid=transaction_id: (
                        _send_chargeback_alert_now(s, tid)
                    ),
                )
                queue_post_commit_task(
                    session,
                    lambda s=snapshot: (
                        _notify_client_chargeback_now(s)
                    ),
                )

                return True, "success"

        except Exception as e:
            logger.error(
                "Chargeback processing failed: %s",
                e,
                exc_info=True,
            )
            return False, "error"