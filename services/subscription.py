import logging
from datetime import timedelta
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.constants import (
    PERMANENT_SUBSCRIPTION_DAYS,
    PERMANENT_END_DATE,
)
from bot.middlewares.user_context import invalidate_user_cache
from database.models import User, VPNProfile
from services.api_operations_queue import enqueue_api_operation
from database.repositories.profiles_repo import (
    get_user_profiles,
    get_user_profiles_count,
)
from database.repositories.servers_repo import get_server_by_id
from database.repositories.users_repo import (
    create_user,
    get_user_by_telegram_id,
    get_user_by_telegram_id_any,
)
from utils.datetime_helpers import is_expired, now_utc

logger = logging.getLogger(__name__)


class SubscriptionService:
    @staticmethod
    async def sync_access_state(session: AsyncSession, user: User) -> None:
        await SubscriptionService._sync_access_state(session, user)

    @staticmethod
    async def check_access(session: AsyncSession, telegram_id: int) -> bool:
        user = await get_user_by_telegram_id(session, telegram_id)

        if not user or user.is_banned or not user.subscription_end:
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

        for _ in range(5):
            if not current_id:
                break

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
    ) -> Optional[User]:
        user = await get_user_by_telegram_id_any(session, telegram_id)

        if user is not None and user.is_deleted:
            user.is_deleted = False
            user.deleted_at = None
            user.is_bot_blocked = False

            await session.flush()
            invalidate_user_cache(telegram_id)

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
                is_valid = await SubscriptionService._validate_referral(
                    session,
                    telegram_id,
                    ref_id,
                )

                if is_valid:
                    user.referred_by = ref_id
                    changed = True

                    logger.info(
                        "Late referral binding: user %s bound to referrer %s",
                        telegram_id,
                        ref_id,
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

        try:
            user = await create_user(
                session,
                telegram_id,
                username,
                first_name,
                referred_by,
            )
        except IntegrityError:
            await session.rollback()

            user = await get_user_by_telegram_id_any(session, telegram_id)

            if user is not None and user.is_deleted:
                user.is_deleted = False
                user.deleted_at = None
                user.is_bot_blocked = False

                await session.flush()

                logger.info(
                    "process_onboarding: IntegrityError caught for telegram_id=%s, "
                    "re-read existing user",
                    telegram_id,
                )

        invalidate_user_cache(telegram_id)

        return user

    @staticmethod
    async def extend_subscription(
        session: AsyncSession,
        telegram_id: int,
        days: int,
        new_device_limit: Optional[int] = None,
        new_tariff_id: Optional[int] = None,
    ) -> Optional[User]:
        if days < 0:
            raise ValueError("days must be >= 0")

        stmt = (
            select(User)
            .where(
                User.telegram_id == telegram_id,
                User.is_deleted == False,
            )
            .with_for_update()
        )

        user = await session.scalar(stmt)

        if not user:
            return None

        if new_device_limit is not None:
            profiles_count = await get_user_profiles_count(session, user.id)

            if profiles_count > new_device_limit:
                raise ValueError(
                    f"Cannot downgrade: {profiles_count} devices > "
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
            base_end = (
                user.subscription_end
                if had_active_subscription
                else now
            )

            new_end = (
                PERMANENT_END_DATE
                if days >= PERMANENT_SUBSCRIPTION_DAYS
                else base_end + timedelta(days=days)
            )

        user.subscription_end = new_end

        user.notified_3d = False
        user.notified_1d = False
        user.notified_2h = False
        user.notified_expired = False
        user.notified_grace_12h = False
        user.notification_retry_count = 0
        user.last_notification_attempt = None

        if new_device_limit is not None:
            old_device_limit = user.device_limit or 0
            user.device_limit = new_device_limit

            if new_device_limit > old_device_limit:
                user.device_creations_today = 0
                user.last_creation_date = None

                logger.info(
                    "extend_subscription: user %s upgraded from %s to %s devices. "
                    "Daily creations counter reset to 0.",
                    telegram_id,
                    old_device_limit,
                    new_device_limit,
                )

        if new_tariff_id is not None:
            user.current_tariff_id = new_tariff_id

        await session.flush()

        invalidate_user_cache(telegram_id)

        await SubscriptionService._sync_access_state(session, user)

        return user

    @staticmethod
    async def _sync_access_state(session: AsyncSession, user: User) -> None:
        target_active = bool(user.subscription_end and not is_expired(user.subscription_end) and not user.is_banned)
        profiles = await get_user_profiles(session, user.id)
        for profile in profiles:
            if profile.provisioning_status in {"deleting", "create_failed", "delete_failed"}:
                continue
            was_pending_create = profile.provisioning_status == "pending_create"
            profile.desired_version += 1
            profile.desired_is_active = target_active
            profile.desired_expires_at = user.subscription_end if target_active else None
            profile.is_active = target_active
            profile.provisioning_status = "pending_create" if was_pending_create else "pending_update"
            if was_pending_create:
                continue
            permanent = bool(target_active and user.subscription_end and user.subscription_end.year >= 2100)
            expires_at = int(user.subscription_end.timestamp()) if target_active and user.subscription_end and not permanent else None
            server = profile.server
            await enqueue_api_operation(session, operation_type="update_peer",
                idempotency_key=f"update-peer:{profile.id}:v{profile.desired_version}",
                server_id=profile.server_id, profile_id=profile.id, peer_id=profile.peer_id,
                server_name_snapshot=server.name if server else None,
                api_url_snapshot=server.api_url if server else None,
                api_key_snapshot=server.api_key if server else None,
                client_name=profile.client_name, payload={
                    "desired_version": profile.desired_version,
                    "status": "active" if target_active else "disabled",
                    "expires_at": expires_at,
                    "clear_expires_at": target_active and expires_at is None,
                })
        await session.flush()
