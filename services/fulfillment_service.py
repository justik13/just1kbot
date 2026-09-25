"""Direct, linear fulfillment service for simple billing orders."""

from __future__ import annotations

from datetime import timedelta
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from config.constants import VPN_ACCESS_GRACE_HOURS
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
        user = await session.get(User, order.user_id, with_for_update=True)
        if not user:
            logger.error(
                "Cannot fulfill order %s: user %s not found",
                order.id,
                order.user_id,
            )
            return

        if order.service_type == "awg":
            now = now_utc()
            is_tariff_change = bool(order.metadata_ and order.metadata_.get("is_tariff_change"))
            if is_tariff_change and order.duration_days > 0:
                user.subscription_end = now + timedelta(days=order.duration_days)
            else:
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
                "Fulfilled AWG order %s for user %s: +%s days (until %s, tariff_change=%s)",
                order.id,
                user.id,
                order.duration_days,
                user.subscription_end,
                is_tariff_change,
            )

        elif order.service_type == "white_internet":
            from database.repositories.white_internet_repo import (
                get_subscription_by_user_id,
            )

            sub = await get_subscription_by_user_id(session, user.id)
            if (order.traffic_bytes or 0) > 0 and (order.duration_days or 0) == 0:
                pack_gb = max(1, order.traffic_bytes // (1024**3))
                ok, msg, _ = await WhiteInternetService.topup_quota(
                    session,
                    user.id,
                    pack_gb,
                    actor_telegram_id=user.telegram_id,
                    debit_balance=False,
                )
                if not ok:
                    raise RuntimeError(f"White Internet quota topup failed: {msg}")
            elif order.device_limit and (order.duration_days or 0) == 0:
                ok, msg, _ = await WhiteInternetService.purchase_device_slot(
                    session,
                    user.id,
                    actor_telegram_id=user.telegram_id,
                    debit_balance=False,
                )
                if not ok:
                    raise RuntimeError(f"White Internet device slot purchase failed: {msg}")
            else:
                if sub and sub.status in ("ACTIVE", "EXPIRED", "EXHAUSTED"):
                    ok, msg, _ = await WhiteInternetService.renew_subscription(
                        session, user.id, debit_balance=False
                    )
                else:
                    ok, msg, _ = await WhiteInternetService.purchase_subscription(
                        session, user.id, debit_balance=False
                    )
                if not ok:
                    raise RuntimeError(f"White Internet subscription fulfillment failed: {msg}")
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
                new_end = user.subscription_end - timedelta(days=order.duration_days)
                cutoff = now - timedelta(hours=VPN_ACCESS_GRACE_HOURS + 1)
                if new_end <= now:
                    user.subscription_end = cutoff
                else:
                    user.subscription_end = new_end
                await SubscriptionService.sync_access_state(session, user)
                invalidate_user_cache(user.telegram_id)
                logger.info(
                    "Revoked AWG order %s for user %s: -%s days (subscription_end=%s)",
                    order.id,
                    user.id,
                    order.duration_days,
                    user.subscription_end,
                )

        elif order.service_type == "white_internet":
            await WhiteInternetService.deactivate_user_subscriptions(session, user.id)
            logger.info("Revoked White Internet order %s for user %s", order.id, user.id)
