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
            meta = dict(order.metadata_ or {})
            if user.subscription_end:
                meta["previous_subscription_end"] = user.subscription_end.isoformat()
            if user.current_tariff_id:
                meta["previous_tariff_id"] = user.current_tariff_id
            order.metadata_ = meta

            is_tariff_change = bool(meta.get("is_tariff_change"))
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
            meta_op = (order.metadata_ or {}).get("operation")
            if meta_op == "add_device_slot" or (
                order.device_limit
                and not (order.traffic_bytes or 0)
                and (order.duration_days or 0) == 0
            ):
                ok, msg, _ = await WhiteInternetService.purchase_device_slot(
                    session,
                    user.id,
                    actor_telegram_id=user.telegram_id,
                    debit_balance=False,
                )
                if not ok:
                    raise RuntimeError(f"White Internet device slot purchase failed: {msg}")
            elif meta_op == "topup" or (
                (order.traffic_bytes or 0) > 0 and (order.duration_days or 0) == 0
            ):
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
            else:
                if sub and sub.status in ("ACTIVE", "EXPIRED", "EXHAUSTED"):
                    ok, msg, created_sub = await WhiteInternetService.renew_subscription(
                        session, user.id, debit_balance=False
                    )
                else:
                    ok, msg, created_sub = await WhiteInternetService.purchase_subscription(
                        session, user.id, debit_balance=False
                    )
                if not ok:
                    raise RuntimeError(f"White Internet subscription fulfillment failed: {msg}")
                if created_sub and hasattr(created_sub, "id"):
                    meta = dict(order.metadata_ or {})
                    meta["subscription_id"] = created_sub.id
                    order.metadata_ = meta
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
        user = await session.get(User, order.user_id, with_for_update=True)
        if not user:
            return

        if order.service_type == "awg":
            meta = dict(order.metadata_ or {})
            is_tariff_change = bool(meta.get("is_tariff_change"))
            prev_end_str = meta.get("previous_subscription_end")
            if is_tariff_change and prev_end_str:
                try:
                    from datetime import datetime
                    prev_end = datetime.fromisoformat(prev_end_str)
                    user.subscription_end = prev_end
                    prev_tariff = meta.get("previous_tariff_id")
                    if prev_tariff:
                        user.current_tariff_id = prev_tariff
                except Exception as exc:
                    logger.warning("Failed parsing previous_subscription_end on order %s: %s", order.id, exc)
            elif order.duration_days > 0 and user.subscription_end:
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
                "Revoked AWG order %s for user %s (subscription_end=%s)",
                order.id,
                user.id,
                user.subscription_end,
            )

        elif order.service_type == "white_internet":
            target_sub_id = (order.metadata_ or {}).get("subscription_id")
            await WhiteInternetService.deactivate_user_subscriptions(
                session,
                user.id,
                reason="order_refunded",
                target_subscription_id=target_sub_id,
            )
            logger.info("Revoked White Internet order %s (sub %s) for user %s", order.id, target_sub_id, user.id)
