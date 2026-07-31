import logging

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.middlewares.user_context import invalidate_user_cache
from database.models import (
    Payment,
    PaymentFulfillmentOperation,
    TariffQuote,
    User,
)
from database.repositories.users_repo import (
    get_user_by_telegram_id,
    update_user,
)
from services.audit_service import AuditService
from services.payment_lifecycle import project_legacy_status
from services.payment_service import PaymentService
from services.profile_deletion_service import ProfileDeletionService
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)


class BanService:
    """
    Сервис бана и разбана пользователей.

    Принятая продуктовая логика:
    - бан сразу удаляет все устройства пользователя;
    - устройства не восстанавливаются после разбана;
    - незавершённые платежи закрываются через durable-операции;
    - статус провайдера не подменяется локальным предположением;
    - оплата после бана не должна автоматически выдавать доступ.
    """

    @staticmethod
    async def toggle_ban(
        session: AsyncSession,
        admin_id: int,
        telegram_id: int,
    ) -> tuple:
        user = await get_user_by_telegram_id(session, telegram_id)

        if not user:
            return False, "Пользователь не найден"

        new_status = not user.is_banned

        if new_status:
            return await BanService._ban_user(
                session=session,
                admin_id=admin_id,
                user=user,
                telegram_id=telegram_id,
            )

        return await BanService._unban_user(
            session=session,
            admin_id=admin_id,
            user=user,
            telegram_id=telegram_id,
        )

    @staticmethod
    async def _ban_user(
        session: AsyncSession,
        admin_id: int,
        user,
        telegram_id: int,
    ) -> tuple:
        # Используем тот же per-user advisory lock, что и checkout.
        # Новый платёж не сможет появиться между чтением платежей и баном.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": -user.id},
        )

        # Блокируем платежи в стабильном порядке до блокировки User.
        # Fulfillment использует порядок Payment -> User, поэтому обратного
        # порядка блокировок здесь быть не должно.
        payment_ids = list(
            (
                await session.scalars(
                    select(Payment.id)
                    .where(Payment.user_id == user.id)
                    .order_by(Payment.id)
                    .with_for_update()
                )
            ).all()
        )

        locked_user = await session.scalar(
            select(User)
            .where(User.id == user.id)
            .with_for_update()
        )
        if locked_user is None or locked_user.is_deleted:
            return False, "Пользователь не найден"

        payments_abandoned = 0

        for payment_id in payment_ids:
            abandoned = await PaymentService.cancel_payment_via_api(
                session,
                payment_id,
                source="ban",
            )
            if abandoned:
                payments_abandoned += 1
                payment = await session.get(Payment, payment_id)
                if payment is not None:
                    project_legacy_status(payment)

        current_time = now_utc()
        fulfillment_cancelled = 0

        if payment_ids:
            # Терминальные provider-платежи helper не меняет, поэтому отдельно
            # закрываем оставшиеся активные checkout без подмены provider_status.
            close_result = await session.execute(
                update(Payment)
                .where(
                    Payment.id.in_(payment_ids),
                    Payment.checkout_status == "active",
                )
                .values(
                    checkout_status="abandoned",
                    payment_url=None,
                )
            )
            payments_abandoned += close_result.rowcount or 0

            await session.execute(
                update(TariffQuote)
                .where(
                    TariffQuote.payment_id.in_(payment_ids),
                    TariffQuote.status == "active",
                )
                .values(
                    status="cancelled",
                    diagnostic_reason="checkout_abandoned_by_ban",
                )
            )

            # Отменяем только ещё не захваченные grant-операции.
            # processing не трогаем: worker уже владеет такой операцией.
            result = await session.execute(
                update(PaymentFulfillmentOperation)
                .where(
                    PaymentFulfillmentOperation.payment_id.in_(payment_ids),
                    PaymentFulfillmentOperation.operation_type.in_(
                        ("grant_subscription", "grant_referral")
                    ),
                    PaymentFulfillmentOperation.status.in_(
                        ("pending", "retry")
                    ),
                )
                .values(
                    status="cancelled",
                    completed_at=current_time,
                    last_error_code="user_banned",
                    last_error="user banned before fulfillment",
                    locked_at=None,
                    locked_by=None,
                )
            )
            fulfillment_cancelled = result.rowcount or 0

            # Провайдер уже подтвердил оплату, но доступ ещё не выдан.
            # Сохраняем provider_status=succeeded и переводим выдачу в review.
            await session.execute(
                update(Payment)
                .where(
                    Payment.id.in_(payment_ids),
                    Payment.provider_status == "succeeded",
                    Payment.fulfillment_status.in_(
                        ("not_ready", "pending", "processing", "failed")
                    ),
                )
                .values(
                    status="requires_manual_review",
                    checkout_status="abandoned",
                    fulfillment_status="manual_review",
                    reconciliation_status="manual_review",
                    manual_review_reason=(
                        "user_banned_before_fulfillment"
                    ),
                    fulfillment_last_error_code="user_banned",
                    payment_url=None,
                )
            )

        await update_user(
            session,
            locked_user,
            is_banned=True,
        )

        # Только durable DB-операции. HTTP к Amnezia здесь не выполняется.
        deleted_profiles = (
            await ProfileDeletionService.delete_profiles_for_user(
                session,
                locked_user.id,
                reason="ban_delete",
                background=True,
            )
        )

        await AuditService.log_action(
            session,
            admin_id,
            "BAN",
            "User",
            telegram_id,
            (
                f"profiles_deleted={deleted_profiles}, "
                f"payments_abandoned={payments_abandoned}, "
                f"fulfillment_cancelled={fulfillment_cancelled}"
            ),
        )

        invalidate_user_cache(telegram_id)

        logger.info(
            "User %s banned by admin %s. "
            "Deleted profiles: %s, abandoned payments: %s, "
            "cancelled fulfillment operations: %s",
            telegram_id,
            admin_id,
            deleted_profiles,
            payments_abandoned,
            fulfillment_cancelled,
        )

        return True, "забанен"

    @staticmethod
    async def _unban_user(
        session: AsyncSession,
        admin_id: int,
        user,
        telegram_id: int,
    ) -> tuple:
        # При разбане устройства НЕ восстанавливаются.
        # Пользователь должен создать их заново, если подписка активна.

        await update_user(session, user, is_banned=False)

        await AuditService.log_action(
            session,
            admin_id,
            "UNBAN",
            "User",
            telegram_id,
            "devices_not_restored",
        )

        invalidate_user_cache(telegram_id)

        logger.info(
            "User %s unbanned by admin %s. Devices were not restored.",
            telegram_id,
            admin_id,
        )

        return True, "разбанен"
