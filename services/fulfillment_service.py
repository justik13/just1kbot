"""Direct, linear fulfillment service for simple billing orders."""

from __future__ import annotations

from datetime import timedelta
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from config.constants import STANDARD_DOWNGRADE_COOLDOWN_DAYS, VPN_ACCESS_GRACE_HOURS
from database.models import Order, User
from services.subscription import SubscriptionService
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

        if order.service_type == "standard":
            from database.repositories import vless_subscription_repo

            vless_sub, _ = await vless_subscription_repo.get_or_create_subscription(session, user.id)
            now = now_utc()
            meta = dict(order.metadata_ or {})
            if vless_sub.expires_at:
                meta["previous_subscription_end"] = vless_sub.expires_at.isoformat()
            if getattr(vless_sub, "device_limit", None) is not None:
                meta["previous_device_limit"] = vless_sub.device_limit
            order.metadata_ = meta

            is_tariff_change = bool(meta.get("is_tariff_change"))
            op = meta.get("operation")
            if op == "upgrade":
                if order.device_limit:
                    vless_sub.device_limit = order.device_limit
                vless_sub.downgrade_cooldown_until = now + timedelta(
                    days=STANDARD_DOWNGRADE_COOLDOWN_DAYS
                )
            elif is_tariff_change and order.duration_days > 0:
                vless_sub.expires_at = now + timedelta(days=order.duration_days)
                if order.device_limit:
                    vless_sub.device_limit = order.device_limit
            else:
                base = max(vless_sub.expires_at, now) if vless_sub.expires_at else now
                if order.duration_days > 0:
                    vless_sub.expires_at = base + timedelta(days=order.duration_days)
                if order.device_limit:
                    vless_sub.device_limit = order.device_limit
                if (order.device_limit or 0) > 2:
                    vless_sub.downgrade_cooldown_until = now + timedelta(
                        days=STANDARD_DOWNGRADE_COOLDOWN_DAYS
                    )
            vless_sub.notified_3d = False
            vless_sub.notified_1d = False
            vless_sub.notified_2h = False
            vless_sub.notified_expired = False
            user.notification_retry_count = 0
            user.last_notification_attempt = None
            await SubscriptionService._sync_access_state(session, user, vless_sub=vless_sub)
            logger.info(
                "Fulfilled standard order %s for user %s: +%s days (until %s, tariff_change=%s)",
                order.id,
                user.id,
                order.duration_days,
                vless_sub.expires_at,
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
                    extra_bytes=order.traffic_bytes if order.traffic_bytes else None,
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
                )
                if not ok:
                    raise RuntimeError(f"White Internet quota topup failed: {msg}")
            else:
                if sub and sub.status in ("ACTIVE", "EXPIRED", "EXHAUSTED"):
                    ok, msg, created_sub = await WhiteInternetService.renew_subscription(
                        session,
                        user.id,
                        target_device_limit=order.device_limit,
                    )
                else:
                    ok, msg, created_sub = await WhiteInternetService.purchase_subscription(
                        session,
                        user.id,
                        device_limit=order.device_limit or 1,
                    )
                if not ok:
                    raise RuntimeError(f"White Internet subscription fulfillment failed: {msg}")
                if created_sub and hasattr(created_sub, "id"):
                    meta = dict(order.metadata_ or {})
                    meta["subscription_id"] = created_sub.id
                    order.metadata_ = meta
                    await session.flush()
            logger.info("Fulfilled White Internet order %s for user %s", order.id, user.id)

        elif order.service_type == "topup":
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

        if order.service_type == "standard":
            from database.repositories import vless_subscription_repo

            vless_sub = await vless_subscription_repo.get_subscription_by_user_id(session, user.id)
            if not vless_sub:
                return
            meta = dict(order.metadata_ or {})
            is_tariff_change = bool(meta.get("is_tariff_change"))
            prev_end_str = meta.get("previous_subscription_end")
            op = meta.get("operation")
            if op == "upgrade":
                if "previous_device_limit" in meta:
                    try:
                        vless_sub.device_limit = int(meta["previous_device_limit"])
                    except (ValueError, TypeError):
                        pass
            elif is_tariff_change and prev_end_str:
                try:
                    from datetime import datetime
                    prev_end = datetime.fromisoformat(prev_end_str)
                    vless_sub.expires_at = prev_end
                    if "previous_device_limit" in meta:
                        try:
                            vless_sub.device_limit = int(meta["previous_device_limit"])
                        except (ValueError, TypeError):
                            pass
                except Exception as exc:
                    logger.warning("Failed parsing previous_subscription_end on order %s: %s", order.id, exc)
            elif order.duration_days > 0 and vless_sub.expires_at:
                now = now_utc()
                new_end = vless_sub.expires_at - timedelta(days=order.duration_days)
                cutoff = now - timedelta(hours=VPN_ACCESS_GRACE_HOURS + 1)
                if new_end <= now:
                    vless_sub.expires_at = cutoff
                else:
                    vless_sub.expires_at = new_end
            await SubscriptionService._sync_access_state(session, user, vless_sub=vless_sub)
            logger.info(
                "Revoked standard order %s for user %s (expires_at=%s)",
                order.id,
                user.id,
                vless_sub.expires_at,
            )

        elif order.service_type == "white_internet":
            meta = dict(order.metadata_ or {})
            meta_op = meta.get("operation")
            target_sub_id = meta.get("subscription_id")

            if meta_op == "add_device_slot" or (
                order.device_limit
                and (order.duration_days or 0) == 0
                and meta_op != "topup"
            ):
                from config.constants import (
                    WHITE_INTERNET_DEFAULT_DEVICE_LIMIT,
                    WHITE_INTERNET_EXTRA_DEVICE_TRAFFIC_BYTES,
                )
                from database.repositories.white_internet_repo import (
                    get_subscription_by_user_id,
                )

                sub = await get_subscription_by_user_id(session, user.id)
                if sub:
                    sub.device_limit = max(
                        WHITE_INTERNET_DEFAULT_DEVICE_LIMIT,
                        (sub.device_limit or 1) - 1,
                    )
                    revoked_bytes = order.traffic_bytes or WHITE_INTERNET_EXTRA_DEVICE_TRAFFIC_BYTES
                    sub.base_traffic_bytes = max(
                        0,
                        (sub.base_traffic_bytes or 0) - revoked_bytes,
                    )
                    sub.desired_version = (sub.desired_version or 0) + 1
                    await session.flush()
                    logger.info(
                        "Revoked White Internet device slot order %s for user %s (device_limit=%s, base_traffic_bytes=%s)",
                        order.id,
                        user.id,
                        sub.device_limit,
                        sub.base_traffic_bytes,
                    )
                return

            if meta_op == "topup" or (
                (order.traffic_bytes or 0) > 0 and (order.duration_days or 0) == 0
            ):
                from database.repositories.white_internet_repo import (
                    get_subscription_by_user_id,
                )

                sub = await get_subscription_by_user_id(session, user.id)
                if sub:
                    sub.extra_traffic_bytes = max(
                        0, (sub.extra_traffic_bytes or 0) - (order.traffic_bytes or 0)
                    )
                    sub.desired_version = (sub.desired_version or 0) + 1
                    await session.flush()
                    logger.info(
                        "Revoked White Internet topup order %s for user %s (-%s bytes, extra_traffic_bytes=%s)",
                        order.id,
                        user.id,
                        order.traffic_bytes,
                        sub.extra_traffic_bytes,
                    )
                return

            if meta_op == "renew" or (order.duration_days and order.duration_days > 0):
                from database.repositories.white_internet_repo import (
                    get_subscription_by_user_id,
                )

                sub = await get_subscription_by_user_id(session, user.id)
                if sub and sub.expires_at:
                    now = now_utc()
                    new_expires = sub.expires_at - timedelta(days=order.duration_days)
                    if new_expires <= now:
                        await WhiteInternetService.deactivate_user_subscriptions(
                            session,
                            user.id,
                            reason="order_refunded",
                            target_subscription_id=target_sub_id,
                        )
                    else:
                        sub.expires_at = new_expires
                        sub.desired_version = (sub.desired_version or 0) + 1
                        await session.flush()
                        logger.info(
                            "Revoked White Internet renewal order %s for user %s (expires_at rolled back to %s)",
                            order.id,
                            user.id,
                            sub.expires_at,
                        )
                    return

            await WhiteInternetService.deactivate_user_subscriptions(
                session,
                user.id,
                reason="order_refunded",
                target_subscription_id=target_sub_id,
            )
            logger.info("Revoked White Internet order %s (sub %s) for user %s", order.id, target_sub_id, user.id)
