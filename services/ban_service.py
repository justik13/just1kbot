from enum import StrEnum
import logging

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from services.user_cache import invalidate_user_cache
from database.models import Payment, User
from database.repositories.users_repo import (
    get_user_by_telegram_id,
    update_user,
)
from services.audit_service import AuditService
from services.payment_provider_operations import ensure_reconcile_payment_operation
from services.profile_deletion_service import ProfileDeletionService
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)


class BanStatus(StrEnum):
    USER_NOT_FOUND = "USER_NOT_FOUND"
    ALREADY_BANNED = "ALREADY_BANNED"
    BANNED = "BANNED"
    ALREADY_UNBANNED = "ALREADY_UNBANNED"
    UNBANNED = "UNBANNED"


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
    async def ban_user(
        session: AsyncSession,
        admin_id: int,
        telegram_id: int,
    ) -> tuple:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            return False, BanStatus.USER_NOT_FOUND
        if user.is_banned:
            return True, BanStatus.ALREADY_BANNED
        return await BanService._ban_user(
            session=session,
            admin_id=admin_id,
            user=user,
            telegram_id=telegram_id,
        )

    @staticmethod
    async def unban_user(
        session: AsyncSession,
        admin_id: int,
        telegram_id: int,
    ) -> tuple:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            return False, BanStatus.USER_NOT_FOUND
        if not user.is_banned:
            return True, BanStatus.ALREADY_UNBANNED
        return await BanService._unban_user(
            session=session,
            admin_id=admin_id,
            user=user,
            telegram_id=telegram_id,
        )

    @staticmethod
    async def toggle_ban(
        session: AsyncSession,
        admin_id: int,
        telegram_id: int,
    ) -> tuple:
        user = await get_user_by_telegram_id(session, telegram_id)

        if not user:
            return False, BanStatus.USER_NOT_FOUND

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
            return False, BanStatus.USER_NOT_FOUND

        payments_closed = 0
        reconciliations_queued = 0
        current_time = now_utc()

        for payment_id in payment_ids:
            payment = await session.get(Payment, payment_id)
            if payment is None:
                continue
            if payment.provider_status not in {"succeeded", "canceled", "refunded"}:
                if payment.checkout_status != "abandoned" or payment.ui_visible:
                    payments_closed += 1
                payment.checkout_status = "abandoned"
                payment.ui_visible = False
                payment.payment_url = None
                payment.user_cancel_requested_at = (
                    payment.user_cancel_requested_at or current_time
                )
                if payment.external_id:
                    operation = await ensure_reconcile_payment_operation(
                        session, payment, reason="user_banned"
                    )
                    if operation is not None:
                        reconciliations_queued += 1

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
            admin_id=admin_id,
            action="BAN_USER",
            target_type="user",
            target_id=locked_user.id,
            details={
                "profiles_deleted": deleted_profiles,
                "payments_closed": payments_closed,
                "reconciliations_queued": reconciliations_queued,
            },
        )

        invalidate_user_cache(telegram_id)

        logger.info(
            "User %s banned by admin %s. "+
            "Deleted profiles: %s, closed top-ups: %s, "+
            "queued reconciliations: %s",
            telegram_id,
            admin_id,
            deleted_profiles,
            payments_closed,
            reconciliations_queued,
        )

        return True, BanStatus.BANNED

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
            admin_id=admin_id,
            action="UNBAN_USER",
            target_type="user",
            target_id=user.id,
            details={"devices_restored": False},
        )

        invalidate_user_cache(telegram_id)

        logger.info(
            "User %s unbanned by admin %s. Devices were not restored.",
            telegram_id,
            admin_id,
        )

        return True, BanStatus.UNBANNED
