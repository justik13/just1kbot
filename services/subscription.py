import logging
from datetime import timedelta
from typing import Any

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
from database.repositories.users_repo import (
    create_user,
    get_user_by_telegram_id,
    get_user_by_telegram_id_any,
)
from utils.datetime_helpers import (
    is_vpn_access_expired,
    now_utc,
)

logger = logging.getLogger(__name__)


MAX_REFERRAL_CHAIN_DEPTH = 50


class SubscriptionService:
    @staticmethod
    async def get_effective_device_limit(session: AsyncSession, user: User | None) -> int:
        """Canonical device-limit resolution from VlessSubscription.device_limit."""
        if user is None:
            return 0
        from database.repositories import vless_subscription_repo

        vless_sub = await vless_subscription_repo.get_subscription_by_user_id(session, user.id)
        if not vless_sub:
            return 0
        direct_limit = getattr(vless_sub, "device_limit", 0) or 0
        return int(direct_limit) if direct_limit > 0 else 0

    @staticmethod
    async def get_device_slot_usage(
        session: AsyncSession,
        user: User,
        *,
        vless_sub: Any = None,
        wi_sub: Any = None,
        now: Any = None,
    ) -> dict[str, Any]:
        """Compute active device slot usage independently for Standard and White Internet."""
        from config.constants import WhiteInternetStatus
        from database.repositories import (
            user_devices_repo,
            vless_subscription_repo,
            white_internet_repo,
        )

        ref_now = now or now_utc()
        if vless_sub is None:
            vless_sub = await vless_subscription_repo.get_subscription_by_user_id(session, user.id)
        if wi_sub is None:
            wi_sub = await white_internet_repo.get_subscription_by_user_id(session, user.id)

        is_hold = bool(getattr(user, "financial_hold", False))
        std_active = bool(
            vless_sub
            and getattr(vless_sub, "is_active", False)
            and not is_hold
            and SubscriptionService.check_vpn_access(user, vless_sub=vless_sub)
        )
        std_limit = int(getattr(vless_sub, "device_limit", 0) or 0) if (std_active and vless_sub) else 0

        wi_has_period = bool(
            wi_sub is not None
            and not is_hold
            and getattr(wi_sub, "status", None)
            in (
                WhiteInternetStatus.ACTIVE,
                WhiteInternetStatus.PENDING,
                WhiteInternetStatus.EXHAUSTED,
            )
            and getattr(wi_sub, "expires_at", None)
            and wi_sub.expires_at > ref_now
        )
        wi_slot_limit = (
            max(1, int(getattr(wi_sub, "device_limit", 1) or 1))
            if (wi_has_period and wi_sub)
            else 0
        )

        devices = await user_devices_repo.list_user_devices(
            session,
            user.id,
            wi_device_limit=wi_slot_limit,
            std_device_limit=std_limit,
        )
        return {
            "devices": devices,
            "total_devices": len(devices),
            "std_assigned_count": sum(1 for d in devices if d.get("is_std_assigned")),
            "bi_assigned_count": sum(1 for d in devices if d.get("is_bi_assigned")),
            "std_active": std_active,
            "std_limit": std_limit,
            "wi_has_period": wi_has_period,
            "wi_slot_limit": wi_slot_limit,
            "total_limit": (std_limit + wi_slot_limit) if (std_active or wi_has_period) else 0,
        }

    @staticmethod
    def check_standard_limit_fits(
        usage: dict[str, Any],
        target_std_limit: int,
    ) -> tuple[bool, str | None]:
        """Validate that a target Standard device limit fits current Standard assignments and total cap."""
        std_assigned = int(usage.get("std_assigned_count") or 0)
        total_devices = int(usage.get("total_devices") or 0)
        wi_slot_limit = int(usage.get("wi_slot_limit") or 0)
        new_total_limit = int(target_std_limit) + wi_slot_limit

        if std_assigned > target_std_limit:
            return (
                False,
                f"На тарифе «Стандарт» занято устройств ({std_assigned}) больше нового лимита ({target_std_limit}). "
                "Отключите слот «Стандарт» на лишнем устройстве или удалите его.",
            )
        if total_devices > new_total_limit:
            return (
                False,
                f"Привязано устройств ({total_devices}) больше нового общего лимита ({new_total_limit}). "
                "Удалите лишние устройства.",
            )
        return True, None

    @staticmethod
    async def sync_access_state(session: AsyncSession, user: User) -> None:
        await SubscriptionService._sync_access_state(session, user)

    @staticmethod
    def check_vpn_access(user: User | None, vless_sub: Any = None) -> bool:
        if not user or user.is_deleted or user.financial_hold:
            return False
        sub = vless_sub if vless_sub is not None else getattr(user, "vless_subscription", None)
        if not sub or not sub.expires_at:
            return False
        return not is_vpn_access_expired(sub.expires_at, grace_hours=VPN_ACCESS_GRACE_HOURS)

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
                ref_user_id = await session.scalar(
                    select(User.id).where(
                        User.telegram_id == ref_id,
                        User.is_deleted.is_(False),
                    )
                )
                if ref_user_id is not None and ref_user_id != user.id:
                    first_id, second_id = (
                        (user.id, ref_user_id)
                        if user.id < ref_user_id
                        else (ref_user_id, user.id)
                    )
                    locked_first = await session.scalar(
                        select(User).where(User.id == first_id).with_for_update()
                    )
                    locked_second = await session.scalar(
                        select(User).where(User.id == second_id).with_for_update()
                    )
                    locked_user = locked_first if user.id == first_id else locked_second
                else:
                    locked_user = await session.scalar(
                        select(User).where(User.id == user.id).with_for_update()
                    )
                if isinstance(locked_user, User):
                    user = locked_user
                if user.referred_by is None:
                    from sqlalchemy import func, or_
                    from database.models import Order

                    order_count = await session.scalar(
                        select(func.count(Order.id)).where(
                            Order.user_id == user.id,
                            or_(
                                Order.status.in_(["paid", "refunded"]),
                                Order.paid_at.is_not(None),
                            ),
                            or_(
                                Order.service_type == "topup",
                                Order.payment_method != "wallet",
                            ),
                        )
                    )
                    has_paid = (order_count or 0) > 0
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

        return user

    @staticmethod
    async def extend_subscription(
        session: AsyncSession,
        telegram_id: int,
        days: int,
        new_device_limit: int | None = None,
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
            slot_usage = await SubscriptionService.get_device_slot_usage(session, user)
            fits, reason = SubscriptionService.check_standard_limit_fits(
                slot_usage, new_device_limit
            )
            if not fits:
                raise ValueError(reason or "Cannot downgrade device limit.")

        from database.repositories import vless_subscription_repo

        vless_sub = await vless_subscription_repo.get_or_create_subscription(session, user.id)
        now = now_utc()

        had_active_subscription = bool(
            vless_sub.expires_at and vless_sub.expires_at > now
        )

        if days == 0:
            new_end = vless_sub.expires_at
        else:
            base_end = vless_sub.expires_at if had_active_subscription else now

            if days >= PERMANENT_SUBSCRIPTION_DAYS:
                new_end = PERMANENT_END_DATE
            else:
                new_end = base_end + timedelta(days=days)

        vless_sub.expires_at = new_end
        vless_sub.notified_3d = False
        vless_sub.notified_1d = False
        vless_sub.notified_2h = False
        vless_sub.notified_expired = False
        user.notification_retry_count = 0
        user.last_notification_attempt = None

        limit_changed = (
            new_device_limit is not None
            and int(getattr(vless_sub, "device_limit", 0) or 0) != int(new_device_limit)
        )
        if new_device_limit is not None:
            vless_sub.device_limit = new_device_limit

        await session.flush()

        await SubscriptionService._sync_access_state(
            session, user, vless_sub=vless_sub, force_sync=limit_changed
        )

        return user

    @staticmethod
    async def reduce_standard_subscription(
        session: AsyncSession,
        user: User,
        *,
        days: int,
        new_device_limit: int | None = None,
    ) -> dict[str, Any]:
        """Reduce active Standard subscription period (and optionally device limit) with sync."""
        if days >= 0:
            raise ValueError("days must be < 0 for reduce_standard_subscription")

        from database.repositories import vless_subscription_repo
        from services.vless_subscription_service import VlessSubscriptionService

        vless_sub = await vless_subscription_repo.get_subscription_by_user_id(session, user.id)
        now = now_utc()
        if vless_sub is None or not vless_sub.expires_at or vless_sub.expires_at <= now:
            raise ValueError("Подписка «Стандарт» неактивна или уже истекла")

        before_end = vless_sub.expires_at.isoformat() if vless_sub.expires_at else None
        before_limit = int(getattr(vless_sub, "device_limit", 0) or 0)
        new_end = vless_sub.expires_at - timedelta(days=abs(days))

        if (
            new_end > now
            and new_device_limit is not None
            and new_device_limit < before_limit
        ):
            slot_usage = await SubscriptionService.get_device_slot_usage(
                session, user, vless_sub=vless_sub, now=now
            )
            fits, limit_reason = SubscriptionService.check_standard_limit_fits(
                slot_usage, new_device_limit
            )
            if not fits:
                raise ValueError(limit_reason or "Активных устройств больше нового лимита")

        expired_now = new_end <= now
        vless_sub.expires_at = (
            now - timedelta(hours=VPN_ACCESS_GRACE_HOURS + 1)
            if expired_now
            else new_end
        )
        limit_changed = (
            new_device_limit is not None
            and new_device_limit != before_limit
        )
        if new_device_limit is not None:
            vless_sub.device_limit = new_device_limit

        if expired_now:
            vless_sub.is_active = False
            vless_sub.version = (getattr(vless_sub, "version", 1) or 1) + 1
            await session.flush()
            VlessSubscriptionService.ensure_synced_background(
                user.id, is_active=False, session=session
            )
        elif limit_changed:
            vless_sub.version = (getattr(vless_sub, "version", 1) or 1) + 1
            await session.flush()
            VlessSubscriptionService.ensure_synced_background(user.id, session=session)
        else:
            await session.flush()

        return {
            "vless_sub": vless_sub,
            "before_end": before_end,
            "before_limit": before_limit,
            "expired_now": expired_now,
        }

    @staticmethod
    async def _sync_access_state(
        session: AsyncSession,
        user: User,
        vless_sub: Any = None,
        *,
        force_sync: bool = False,
    ) -> None:
        from database.repositories import vless_subscription_repo
        from services.vless_subscription_service import VlessSubscriptionService

        if vless_sub is None:
            vless_sub = await vless_subscription_repo.get_subscription_by_user_id(session, user.id)
        vless_target_active = SubscriptionService.check_vpn_access(user, vless_sub=vless_sub)
        if vless_sub:
            if vless_sub.is_active != vless_target_active:
                vless_sub.is_active = vless_target_active
                vless_sub.version = (getattr(vless_sub, "version", 1) or 1) + 1
                session.add(vless_sub)
                VlessSubscriptionService.ensure_synced_background(user.id, is_active=vless_target_active, session=session)
            elif force_sync and vless_target_active:
                vless_sub.version = (getattr(vless_sub, "version", 1) or 1) + 1
                session.add(vless_sub)
                VlessSubscriptionService.ensure_synced_background(user.id, session=session)

        if user and (user.is_deleted or user.financial_hold):
            from services.white_internet_service import WhiteInternetService
            await WhiteInternetService.deactivate_user_subscriptions(
                session,
                user.id,
                reason="financial_hold" if user.financial_hold else "user_deleted",
            )

        await session.flush()
