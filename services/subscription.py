import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config.constants import (
    AdminAuditAction,
    PERMANENT_END_DATE,
    PERMANENT_SUBSCRIPTION_DAYS,
    VPN_ACCESS_GRACE_HOURS,
)
from database.models import User
from database.repositories.tariffs_repo import get_tariff_by_id
from database.repositories.users_repo import (
    create_user,
    get_user_by_telegram_id,
    get_user_by_telegram_id_any,
)
from services.user_cache import invalidate_user_cache
from utils.datetime_helpers import (
    is_expired,
    is_vpn_access_expired,
    now_utc,
)

logger = logging.getLogger(__name__)


MAX_REFERRAL_CHAIN_DEPTH = 50


class SubscriptionService:
    @staticmethod
    async def get_effective_device_limit(session: AsyncSession, user: User | None) -> int:
        """Canonical device-limit resolution: current tariff first, then user override, else 0.

        Single source of truth used by both the payment and connection UI layers
        (and safe to reuse by domain services).
        """
        if user is None:
            return 0
        current_tariff_id = getattr(user, "current_tariff_id", None)
        if current_tariff_id:
            tariff = await get_tariff_by_id(session, current_tariff_id)
            if tariff and getattr(tariff, "service_type", "standard") == "standard":
                return getattr(tariff, "device_limit", 0) or 0
        return getattr(user, "device_limit", 0) or 0

    @staticmethod
    async def sync_access_state(session: AsyncSession, user: User) -> None:
        await SubscriptionService._sync_access_state(session, user)

    @staticmethod
    def check_vpn_access(user: User | None) -> bool:
        if not user or user.is_deleted or user.is_banned or user.financial_hold or not user.subscription_end:
            return False
        return not is_vpn_access_expired(user.subscription_end, grace_hours=VPN_ACCESS_GRACE_HOURS)

    @staticmethod
    async def check_access(session: AsyncSession, telegram_id: int) -> bool:
        user = await get_user_by_telegram_id(session, telegram_id)

        if not user or user.is_deleted or user.is_banned or user.financial_hold or not user.subscription_end:
            return False

        return not is_expired(user.subscription_end)

    @staticmethod
    async def _validate_referral(
        session: AsyncSession,
        telegram_id: int,
        ref_id: int,
    ) -> bool:
        if ref_id == telegram_id:
            logger.warning("Referral: self-referral attempt by %s", telegram_id)
            return False

        ref_user = await get_user_by_telegram_id(session, ref_id)

        if not ref_user:
            logger.warning("Referral: referrer %s not found in DB", ref_id)
            return False

        current_id = ref_id
        chain_visited = {telegram_id, ref_id}
        depth = 0

        while current_id:
            depth += 1
            if depth > MAX_REFERRAL_CHAIN_DEPTH:
                logger.warning(
                    "Referral chain max depth %s exceeded for user %s, ref_id %s",
                    MAX_REFERRAL_CHAIN_DEPTH,
                    telegram_id,
                    ref_id,
                )
                return False

            current_user = await get_user_by_telegram_id(session, current_id)

            if not current_user or not current_user.referred_by:
                break

            if current_user.referred_by in chain_visited:
                logger.warning(
                    "Circular referral chain detected for user %s, ref_id %s",
                    telegram_id,
                    ref_id,
                )
                return False

            chain_visited.add(current_user.referred_by)
            current_id = current_user.referred_by

        return True

    @staticmethod
    async def process_onboarding(
        session: AsyncSession,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        ref_id: int | None = None,
    ) -> User | None:
        user = await get_user_by_telegram_id_any(session, telegram_id)

        if user is not None and user.is_deleted:
            user.is_deleted = False
            user.deleted_at = None
            user.is_bot_blocked = False

            await session.flush()
            invalidate_user_cache(telegram_id)

            from services.audit_service import AuditService
            await AuditService.log_action(
                session,
                admin_id=0,
                action=AdminAuditAction.USER_RESTORED,
                target_type="user",
                target_id=user.id,
                details={"telegram_id": telegram_id},
            )

            logger.info("Restored soft-deleted user %s on onboarding", telegram_id)

        if user is not None:
            changed = False

            if username is not None and user.username != username:
                user.username = username
                changed = True

            if first_name is not None and user.first_name != first_name:
                user.first_name = first_name
                changed = True

            if user.is_bot_blocked:
                user.is_bot_blocked = False
                changed = True

                logger.info(
                    "Reset is_bot_blocked for user %s on onboarding",
                    telegram_id,
                )

            if ref_id is not None and user.referred_by is None:
                locked_user = await session.scalar(
                    select(User).where(User.id == user.id).with_for_update()
                )
                if isinstance(locked_user, User):
                    user = locked_user
                if user.referred_by is None:
                    from database.repositories.payments_repo import has_successful_topup

                    has_paid = await has_successful_topup(session, user_id=user.id)
                    if not has_paid:
                        is_valid = await SubscriptionService._validate_referral(
                            session,
                            telegram_id,
                            ref_id,
                        )

                        if is_valid:
                            user.referred_by = ref_id
                            changed = True
                            user.is_newly_referred = True

                            from services.audit_service import AuditService
                            await AuditService.log_action(
                                session,
                                admin_id=0,
                                action=AdminAuditAction.REFERRAL_ATTACHED,
                                target_type="user",
                                target_id=user.id,
                                details={"referrer_telegram_id": ref_id},
                            )

                            logger.info(
                                "Late referral binding: user %s bound to referrer %s",
                                telegram_id,
                                ref_id,
                            )
                    else:
                        logger.info(
                            "Late referral binding rejected for user %s: already has top-up history",
                            telegram_id,
                        )

            if changed:
                await session.flush()
                invalidate_user_cache(telegram_id)

            return user

        referred_by = None

        if ref_id is not None:
            is_valid = await SubscriptionService._validate_referral(
                session,
                telegram_id,
                ref_id,
            )

            if is_valid:
                referred_by = ref_id

                logger.info("New user %s referred by %s", telegram_id, ref_id)

        created = False
        try:
            async with session.begin_nested():
                user = await create_user(
                    session,
                    telegram_id,
                    username,
                    first_name,
                    referred_by,
                )
                created = True
        except IntegrityError:
            user = await get_user_by_telegram_id_any(session, telegram_id)

            if user is not None and user.is_deleted:
                user.is_deleted = False
                user.deleted_at = None
                user.is_bot_blocked = False

                await session.flush()

                from services.audit_service import AuditService
                await AuditService.log_action(
                    session,
                    admin_id=0,
                    action=AdminAuditAction.USER_RESTORED,
                    target_type="user",
                    target_id=user.id,
                    details={"telegram_id": telegram_id},
                )

                logger.info(
                    "process_onboarding: IntegrityError caught for telegram_id=%s, "
                    "restored soft-deleted user",
                    telegram_id,
                )
            else:
                logger.info(
                    "process_onboarding: IntegrityError caught for telegram_id=%s, "
                    "re-read existing user",
                    telegram_id,
                )

        if created and user is not None:
            if referred_by:
                user.is_newly_referred = True

            from services.audit_service import AuditService
            await AuditService.log_action(
                session,
                admin_id=0,
                action=AdminAuditAction.USER_REGISTER,
                target_type="user",
                target_id=user.id,
                details={
                    "telegram_id": telegram_id,
                    "username": username or "",
                    "referred_by": referred_by,
                },
            )

        invalidate_user_cache(telegram_id)

        return user

    @staticmethod
    async def extend_subscription(
        session: AsyncSession,
        telegram_id: int,
        days: int,
        new_device_limit: int | None = None,
        new_tariff_id: int | None = None,
        admin_id: int | None = None,
        reason: str | None = None,
    ) -> User | None:
        if days < 0:
            raise ValueError("days must be >= 0")

        stmt = (
            select(User)
            .where(
                User.telegram_id == telegram_id,
                User.is_deleted.is_(False),
            )
            .with_for_update()
        )

        user = await session.scalar(stmt)

        if not user:
            return None

        if new_device_limit is not None:
            from database.repositories import vless_subscription_repo
            vless_count = await vless_subscription_repo.get_active_hwid_count(session, user.id)
            total_count = vless_count

            if total_count > new_device_limit:
                raise ValueError(
                    f"Cannot downgrade: {total_count} devices > "
                    f"{new_device_limit} limit. User must delete devices first."
                )

        now = now_utc()

        had_active_subscription = bool(
            user.subscription_end and user.subscription_end > now
        )

        # Продуктовая логика:
        # - продление всегда добавляет дни к текущему концу подписки;
        # - если подписка неактивна, отсчёт начинается с текущего момента;
        # - смена тарифа/лимита применяется сразу;
        # - даунгрейд запрещён выше по коду.
        if days == 0:
            new_end = user.subscription_end
        else:
            base_end = user.subscription_end if had_active_subscription else now

            if days >= PERMANENT_SUBSCRIPTION_DAYS:
                new_end = PERMANENT_END_DATE
            else:
                new_end = base_end + timedelta(days=days)

        user.subscription_end = new_end

        user.notified_3d = False
        user.notified_1d = False
        user.notified_2h = False
        user.notified_expired = False
        user.notified_grace_12h = False
        user.notification_retry_count = 0
        user.last_notification_attempt = None

        if new_device_limit is not None:
            user.device_limit = new_device_limit

        if new_tariff_id is not None:
            user.current_tariff_id = new_tariff_id

        await session.flush()

        invalidate_user_cache(telegram_id)

        await SubscriptionService._sync_access_state(session, user)

        return user

    @staticmethod
    async def _sync_access_state(session: AsyncSession, user: User) -> None:
        from database.repositories import vless_subscription_repo
        from services.vless_subscription_service import VlessSubscriptionService

        vless_target_active = SubscriptionService.check_vpn_access(user)

        vless_sub = await vless_subscription_repo.get_subscription_by_user_id(session, user.id)
        if vless_sub and vless_sub.is_active != vless_target_active:
            vless_sub.is_active = vless_target_active
            vless_sub.version = (getattr(vless_sub, "version", 1) or 1) + 1
            session.add(vless_sub)
            VlessSubscriptionService.ensure_synced_background(user.id, is_active=vless_target_active, session=session)

        await session.flush()
