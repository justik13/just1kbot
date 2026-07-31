import logging

from sqlalchemy.ext.asyncio import AsyncSession

from bot.middlewares.user_context import invalidate_user_cache
from database.repositories.payments_repo import get_user_payments
from database.repositories.users_repo import (
    get_user_by_telegram_id,
    update_user,
)
from services.audit_service import AuditService
from services.payment_service import PaymentService
from services.profile_deletion_service import ProfileDeletionService

logger = logging.getLogger(__name__)


class BanService:
    """
    Сервис бана и разбана пользователей.

    Принятая продуктовая логика:
    - бан сразу удаляет все устройства пользователя;
    - устройства не восстанавливаются после разбана;
    - ожидающие платежи банящегося пользователя отменяются через durable-оркестрацию;
    - если пользователь оплатит после бана, платёж должен попасть
      в ручную проверку, а не выдавать доступ автоматически.
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
        # 1. Удаляем все устройства пользователя.
        deleted_profiles = (
            await ProfileDeletionService.delete_profiles_for_user(
                session,
                user.id,
                reason="ban_delete",
                background=True,
            )
        )

        # 2. Отменяем все «живые» платежи через durable-оркестрацию.
        #    Живой платёж: provider_status НЕ терминальный (succeeded/refunded/canceled)
        #    И checkout_status == "active" (ещё не abandoned).
        terminal_provider_statuses = {"succeeded", "refunded", "canceled"}
        payments = await get_user_payments(session, user.id)
        payments_cancelled = 0
        for payment in payments:
            if (
                payment.provider_status not in terminal_provider_statuses
                and payment.checkout_status == "active"
            ):
                try:
                    cancelled = await PaymentService.cancel_payment_via_api(
                        session, payment.id
                    )
                    if cancelled:
                        payments_cancelled += 1
                except Exception as e:
                    # Логируем ошибку отмены конкретного платежа, но не роняем весь бан.
                    # Системные ошибки (DB недоступна) propagate выше через другие механизмы.
                    logger.error(
                        "Failed to cancel payment %s on ban user %s: %s",
                        payment.id,
                        telegram_id,
                        e,
                    )
                    continue

        # 3. Ставим бан.
        await update_user(session, user, is_banned=True)

        # 4. Аудит.
        await AuditService.log_action(
            session,
            admin_id,
            "BAN",
            "User",
            telegram_id,
            f"profiles_deleted={deleted_profiles}, payments_cancelled={payments_cancelled}",
        )

        # 5. Инвалидация кэша пользователя.
        invalidate_user_cache(telegram_id)

        logger.info(
            "User %s banned by admin %s. Deleted profiles: %s, payments cancelled: %s",
            telegram_id,
            admin_id,
            deleted_profiles,
            payments_cancelled,
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