import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.middlewares.user_context import invalidate_user_cache
from database.models import User
from database.repositories.users_repo import (
    get_user_by_telegram_id,
    update_user,
)
from services.subscription import SubscriptionService

logger = logging.getLogger(__name__)

MIN_DURATION_FOR_REFERRAL = 30

REFERRAL_FIRST_PURCHASE_BONUS = 5
REFERRER_FIRST_PURCHASE_BONUS = 3
REFERRER_RENEWAL_BONUS = 1


class ReferralService:
    @staticmethod
    async def process_bonus(
        session: AsyncSession,
        user_telegram_id: int,
        referrer_telegram_id: int,
        *,
        is_first_payment: bool = False,
        duration_days: int = 0,
    ) -> tuple[int, int]:
        """
        Возвращает:
        (
            user_bonus_days_applied,
            referrer_bonus_days_applied,
        )
        """
        if duration_days < MIN_DURATION_FOR_REFERRAL:
            logger.info(
                "Referral bonus SKIPPED: tariff %s days "
                "< %s days minimum. user=%s, referrer=%s",
                duration_days,
                MIN_DURATION_FOR_REFERRAL,
                user_telegram_id,
                referrer_telegram_id,
            )
            return 0, 0

        if referrer_telegram_id == user_telegram_id:
            logger.warning(
                "Referral bonus: self-referral attempt by %s",
                user_telegram_id,
            )
            return 0, 0

        user = await get_user_by_telegram_id(
            session,
            user_telegram_id,
        )

        if not user:
            logger.warning(
                "Referral bonus: user %s not found in DB",
                user_telegram_id,
            )
            return 0, 0

        if user.is_deleted or user.is_banned:
            logger.info(
                "Referral bonus SKIPPED: user %s is deleted or banned",
                user_telegram_id,
            )
            return 0, 0

        referrer = await session.scalar(
            select(User)
            .where(
                User.telegram_id == referrer_telegram_id,
                User.is_deleted == False,
            )
            .with_for_update()
        )

        if not referrer:
            logger.warning(
                "Referral bonus: referrer %s not found in DB",
                referrer_telegram_id,
            )
            return 0, 0

        if referrer.is_deleted or referrer.is_banned:
            logger.info(
                "Referral bonus SKIPPED: referrer %s is deleted or banned",
                referrer_telegram_id,
            )
            return 0, 0

        user_bonus = REFERRAL_FIRST_PURCHASE_BONUS if is_first_payment else 0
        referrer_bonus = (
            REFERRER_FIRST_PURCHASE_BONUS
            if is_first_payment
            else REFERRER_RENEWAL_BONUS
        )

        applied_user_bonus = 0
        applied_referrer_bonus = 0

        if user_bonus > 0:
            try:
                await SubscriptionService.extend_subscription(
                    session,
                    user_telegram_id,
                    user_bonus,
                )

                invalidate_user_cache(user_telegram_id)

                applied_user_bonus = user_bonus

                logger.info(
                    "Referral bonus: user %s got +%s days (first purchase)",
                    user_telegram_id,
                    user_bonus,
                )

            except Exception as e:
                logger.error(
                    "Referral bonus: failed to extend user %s: %s",
                    user_telegram_id,
                    e,
                    exc_info=True,
                )

        if referrer_bonus > 0:
            try:
                await SubscriptionService.extend_subscription(
                    session,
                    referrer_telegram_id,
                    referrer_bonus,
                )

                invalidate_user_cache(referrer_telegram_id)

                new_referral_days = (
                    (referrer.referral_days or 0) + referrer_bonus
                )

                await update_user(
                    session,
                    referrer,
                    referral_days=new_referral_days,
                )

                applied_referrer_bonus = referrer_bonus

                logger.info(
                    "Referral bonus: referrer %s got +%s days. "
                    "Total referral_days: %s",
                    referrer_telegram_id,
                    referrer_bonus,
                    new_referral_days,
                )

            except Exception as e:
                logger.error(
                    "Referral bonus: failed to extend referrer %s: %s",
                    referrer_telegram_id,
                    e,
                    exc_info=True,
                )

        return applied_user_bonus, applied_referrer_bonus