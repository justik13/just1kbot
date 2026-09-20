"""Direct, linear fulfillment service for simple billing orders."""

from __future__ import annotations

from datetime import timedelta
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Order, User
from services.subscription import SubscriptionService
from services.user_cache import invalidate_user_cache
from services.white_internet_service import WhiteInternetService
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)


class FulfillmentService:
    @staticmethod
    async def fulfill_order(session: AsyncSession, order: Order) -> None:
        """Fulfill paid order linearly based on service_type."""
        user = await session.get(User, order.user_id)
        if not user:
            logger.error(
                "Cannot fulfill order %s: user %s not found",
                order.id,
                order.user_id,
            )
            return

        if order.service_type == "awg":
            now = now_utc()
            base = max(user.subscription_end, now) if user.subscription_end else now
            if order.duration_days > 0:
                user.subscription_end = base + timedelta(days=order.duration_days)
            if order.device_limit:
                user.device_limit = order.device_limit
            if order.tariff_id:
                user.current_tariff_id = order.tariff_id
            await SubscriptionService.sync_access_state(session, user)
            invalidate_user_cache(user.telegram_id)
            logger.info(
                "Fulfilled AWG order %s for user %s: +%s days (until %s)",
                order.id,
                user.id,
                order.duration_days,
                user.subscription_end,
            )

        elif order.service_type == "white_internet":
            from database.repositories.white_internet_repo import (
                get_subscription_by_user_id,
            )

            sub = await get_subscription_by_user_id(session, user.id)
            if order.traffic_bytes > 0 and order.duration_days == 0:
                pack_gb = max(1, order.traffic_bytes // (1024**3))
                await WhiteInternetService.topup_quota(
                    session,
                    user.id,
                    pack_gb,
                    actor_telegram_id=user.telegram_id,
                )
            elif order.device_limit and order.duration_days == 0:
                await WhiteInternetService.purchase_device_slot(
                    session,
                    user.id,
                    actor_telegram_id=user.telegram_id,
                )
            else:
                if sub and sub.status in ("ACTIVE", "EXPIRED", "EXHAUSTED"):
                    await WhiteInternetService.renew_subscription(session, user.id)
                else:
                    await WhiteInternetService.purchase_subscription(session, user.id)
            logger.info("Fulfilled White Internet order %s for user %s", order.id, user.id)

        elif order.service_type == "topup":
            invalidate_user_cache(user.telegram_id)
            logger.info(
                "Fulfilled topup order %s for user %s (+%s RUB)",
                order.id,
                user.id,
                order.amount_rub,
            )

    @staticmethod
    async def revoke_order(session: AsyncSession, order: Order) -> None:
        """Revoke order benefits upon external refund."""
        user = await session.get(User, order.user_id)
        if not user:
            return

        if order.service_type == "awg":
            if order.duration_days > 0 and user.subscription_end:
                now = now_utc()
                user.subscription_end = max(
                    now, user.subscription_end - timedelta(days=order.duration_days)
                )
                await SubscriptionService.sync_access_state(session, user)
                invalidate_user_cache(user.telegram_id)
                logger.info(
                    "Revoked AWG order %s for user %s: -%s days",
                    order.id,
                    user.id,
                    order.duration_days,
                )

        elif order.service_type == "white_internet":
            await WhiteInternetService.deactivate_user_subscriptions(session, user.id)
            logger.info("Revoked White Internet order %s for user %s", order.id, user.id)
