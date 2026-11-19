import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup
from cachetools import TTLCache
from sqlalchemy import or_, select, update

from bot.keyboards.notifications import (
    get_action_notification_keyboard,
    get_autorenew_failed_keyboard,
    get_post_expiry_keyboard,
    get_pre_expiry_keyboard,
)
from bot.texts import (
    NOTIFY_AUTORENEW_INSUFFICIENT_FUNDS,
)
from bot.texts.runtime.notifications import (
    NOTIFY_1D,
    NOTIFY_2H,
    NOTIFY_3D,
    NOTIFY_EXPIRED,
    NOTIFY_STANDARD_AUTORENEWED,
    NOTIFY_WI_AUTORENEWED,
    TIME_DAYS_HOURS_FORMAT,
    TIME_HOURS_MINUTES_FORMAT,
    TIME_SOON_LABEL,
)
from config.constants import (
    NOTIFICATION_INTERVAL,
    WORKER_ERROR_SLEEP_INTERVAL,
)
from database.connection import session_scope
from database.models import User, VlessSubscription
from utils.datetime_helpers import is_permanent_subscription, now_utc
from utils.formatters import format_tg_time
from utils.rate_limiter import global_send_limiter
from utils.telegram import safe_send_message

logger = logging.getLogger(__name__)

MAX_RETRY_COUNT = 4
NOTIFICATION_CHECK_INTERVAL = max(NOTIFICATION_INTERVAL, 900)
BACKOFF_BASE_INTERVAL = NOTIFICATION_CHECK_INTERVAL
NOTIFICATION_BATCH_SIZE = 20
NOTIFICATION_START_DELAY = 60.0

_last_notification_type: TTLCache[int, str] = TTLCache(
    maxsize=10000,
    ttl=86400,
)
_autorenew_fail_notified: TTLCache[tuple[str, int], str] = TTLCache(
    maxsize=10000,
    ttl=86400 * 2,
)


def _get_backoff_delay(retry_count: int) -> int:
    capped = min(retry_count, MAX_RETRY_COUNT)
    return BACKOFF_BASE_INTERVAL * (2**capped)


def _format_countdown(delta: timedelta) -> str:
    if delta.total_seconds() <= 0:
        return TIME_SOON_LABEL

    days = delta.days
    hours = delta.seconds // 3600

    if days > 0:
        return TIME_DAYS_HOURS_FORMAT.format(days=days, hours=hours)

    minutes = (delta.seconds % 3600) // 60

    return TIME_HOURS_MINUTES_FORMAT.format(hours=hours, minutes=minutes)


def _maybe_reset_retry_on_type_change(
    user: User,
    notification_type: str,
    vless_sub: VlessSubscription | None = None,
) -> None:
    last_type = _last_notification_type.get(user.id)
    if last_type is None:
        last_type = _infer_last_notification_type(vless_sub)

    if last_type and last_type != notification_type:
        user.notification_retry_count = 0
        user.last_notification_attempt = None

    _last_notification_type[user.id] = notification_type


def _infer_last_notification_type(vless_sub: VlessSubscription | None) -> str | None:
    if vless_sub is None:
        return None
    if vless_sub.notified_expired:
        return "expired"
    if vless_sub.notified_2h:
        return "2h"
    if vless_sub.notified_1d:
        return "1d"
    if vless_sub.notified_3d:
        return "3d"
    return None


async def subscription_notifications_loop(
    bot: Bot,
    shutdown_event: asyncio.Event,
):
    try:
        await asyncio.wait_for(
            shutdown_event.wait(),
            timeout=NOTIFICATION_START_DELAY,
        )

        logger.info("Notifications worker stopped during start delay (shutdown)")

        return

    except asyncio.TimeoutError:
        pass

    while not shutdown_event.is_set():
        try:
            current_time = now_utc()

            await _process_auto_renewals(
                bot,
                current_time,
            )

            await _send_pre_expiry_notifications(
                bot,
                current_time,
            )

            await _send_post_expiry_notifications(
                bot,
                current_time,
            )

            await _send_white_internet_notifications(
                bot,
                current_time,
            )

        except asyncio.CancelledError:
            logger.info("Notifications worker cancelled")
            break

        except Exception as e:
            logger.error(
                "Критическая ошибка в цикле уведомлений: %s",
                e,
                exc_info=True,
            )

            if shutdown_event.is_set():
                break

            try:
                await asyncio.wait_for(
                    shutdown_event.wait(),
                    timeout=WORKER_ERROR_SLEEP_INTERVAL,
                )
                break
            except asyncio.TimeoutError:
                continue

        try:
            await asyncio.wait_for(
                shutdown_event.wait(),
                timeout=NOTIFICATION_CHECK_INTERVAL,
            )

            break

        except asyncio.TimeoutError:
            continue

    logger.info("Notifications worker stopped gracefully")


async def _send_autorenew_notice(
    bot: Bot,
    telegram_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    try:
        await global_send_limiter.acquire()
        await safe_send_message(
            bot,
            telegram_id,
            text,
            reply_markup=reply_markup or get_action_notification_keyboard("⚡ Открыть кабинет", tab="connect"),
            parse_mode="HTML",
            raise_forbidden=True,
        )
    except TelegramForbiddenError:
        async with session_scope() as session:
            u = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if u:
                u.is_bot_blocked = True
    except Exception as exc:
        logger.warning("Failed to send auto-renew notice to %s: %s", telegram_id, exc)


async def _process_auto_renewals(bot: Bot, current_time: datetime) -> None:
    """Automatically renew Standard and White Internet subscriptions from wallet balance when enabled."""
    from config.enums import WhiteInternetStatus
    from database.models import WhiteInternetSubscription
    from database.repositories.account_ledger_repo import get_account_balance
    from services.order_service import OrderService
    from services.subscription import SubscriptionService
    from services.white_internet_service import get_white_internet_tier_price

    window_start = current_time - timedelta(hours=24)
    window_end = current_time + timedelta(hours=24)

    # 1. Standard subscription auto-renewal (30 days at current device_limit)
    async with session_scope() as session:
        std_ids = list(
            (
                await session.execute(
                    select(User.id)
                    .join(VlessSubscription, VlessSubscription.user_id == User.id)
                    .where(
                        VlessSubscription.auto_renew_enabled.is_(True),
                        User.is_deleted.is_(False),
                        User.financial_hold.is_(False),
                        VlessSubscription.expires_at.is_not(None),
                        VlessSubscription.expires_at >= window_start,
                        VlessSubscription.expires_at <= window_end,
                    )
                    .order_by(VlessSubscription.expires_at.asc())
                    .limit(200)
                )
            ).scalars().all()
        )

    for uid in std_ids:
        push_tg_id: int | None = None
        push_text: str | None = None
        push_kb: InlineKeyboardMarkup | None = None
        try:
            async with session_scope() as session:
                user = await session.scalar(
                    select(User).where(User.id == uid)
                )
                vless_sub = await session.scalar(
                    select(VlessSubscription).where(VlessSubscription.user_id == uid)
                )
                if (
                    user is None
                    or vless_sub is None
                    or not vless_sub.auto_renew_enabled
                    or user.is_deleted
                    or user.financial_hold
                    or not vless_sub.expires_at
                    or is_permanent_subscription(vless_sub.expires_at)
                    or vless_sub.expires_at < window_start
                    or vless_sub.expires_at > window_end
                ):
                    continue

                std_limit = max(2, await SubscriptionService.get_effective_device_limit(session, user))
                quote = await OrderService.quote_standard_change(session, user.id, std_limit, 30)
                due_rub = int(quote["due_rub"])
                bal = await get_account_balance(session, user_id=user.id)
                if bal.debt > 0 or bal.available < due_rub:
                    cycle_key = vless_sub.expires_at.strftime("%Y-%m-%d") if vless_sub.expires_at else ""
                    cache_key = ("std", user.id)
                    if _autorenew_fail_notified.get(cache_key) != cycle_key and not user.is_bot_blocked:
                        _autorenew_fail_notified[cache_key] = cycle_key
                        push_tg_id = user.telegram_id
                        push_text = NOTIFY_AUTORENEW_INSUFFICIENT_FUNDS.format(
                            service="Стандарт",
                            due_rub=due_rub,
                            available_rub=int(bal.available),
                        )
                        push_kb = get_autorenew_failed_keyboard()
                    continue

                res = await OrderService.checkout_order(
                    session,
                    user_id=user.id,
                    service_type="standard",
                    device_limit=std_limit,
                    duration_days=30,
                    use_balance=True,
                )
                if res.get("status") == "paid" and not user.is_bot_blocked:
                    await session.refresh(vless_sub)
                    push_tg_id = user.telegram_id
                    push_text = NOTIFY_STANDARD_AUTORENEWED.format(
                        amount=res.get("charged_balance", due_rub),
                        devices=std_limit,
                        date=format_tg_time(vless_sub.expires_at) if vless_sub.expires_at else "",
                    )
        except Exception as exc:
            logger.warning("Standard auto-renew failed for user_id=%s: %s", uid, exc)
            continue

        if push_tg_id and push_text:
            await _send_autorenew_notice(bot, push_tg_id, push_text, reply_markup=push_kb)

    # 2. White Internet auto-renewal (30 days at effective device_limit)
    async with session_scope() as session:
        wi_items = (
            await session.execute(
                select(WhiteInternetSubscription.id, WhiteInternetSubscription.user_id)
                .join(User, User.id == WhiteInternetSubscription.user_id)
                .where(
                    WhiteInternetSubscription.auto_renew_enabled.is_(True),
                    WhiteInternetSubscription.status.in_([
                        WhiteInternetStatus.ACTIVE,
                        WhiteInternetStatus.EXHAUSTED,
                        WhiteInternetStatus.EXPIRED,
                    ]),
                    WhiteInternetSubscription.expires_at.is_not(None),
                    WhiteInternetSubscription.expires_at >= window_start,
                    WhiteInternetSubscription.expires_at <= window_end,
                    User.is_deleted.is_(False),
                    User.financial_hold.is_(False),
                )
                .order_by(WhiteInternetSubscription.expires_at.asc())
                .limit(200)
            )
        ).all()

    for sid, uid in wi_items:
        push_tg_id = None
        push_text = None
        push_kb = None
        try:
            async with session_scope() as session:
                user = await session.scalar(
                    select(User).where(User.id == uid)
                )
                if user is None or user.is_deleted or user.financial_hold:
                    continue

                sub = await session.scalar(
                    select(WhiteInternetSubscription)
                    .where(WhiteInternetSubscription.id == sid)
                    .with_for_update(skip_locked=True)
                )
                if (
                    sub is None
                    or not sub.auto_renew_enabled
                    or not sub.expires_at
                    or sub.expires_at < window_start
                    or sub.expires_at > window_end
                    or sub.status not in (
                        WhiteInternetStatus.ACTIVE,
                        WhiteInternetStatus.EXHAUSTED,
                        WhiteInternetStatus.EXPIRED,
                    )
                ):
                    continue

                eff_limit = int(sub.pending_device_limit or sub.device_limit or 1)
                due_rub = get_white_internet_tier_price(eff_limit)
                bal = await get_account_balance(session, user_id=user.id)
                if bal.debt > 0 or bal.available < due_rub:
                    cycle_key = sub.expires_at.strftime("%Y-%m-%d") if sub.expires_at else ""
                    cache_key = ("wi", user.id)
                    if _autorenew_fail_notified.get(cache_key) != cycle_key and not user.is_bot_blocked:
                        _autorenew_fail_notified[cache_key] = cycle_key
                        push_tg_id = user.telegram_id
                        push_text = NOTIFY_AUTORENEW_INSUFFICIENT_FUNDS.format(
                            service="Белый Интернет",
                            due_rub=int(due_rub),
                            available_rub=int(bal.available),
                        )
                        push_kb = get_autorenew_failed_keyboard()
                    continue

                res = await OrderService.checkout_order(
                    session,
                    user_id=user.id,
                    service_type="white_internet",
                    device_limit=eff_limit,
                    operation="renew",
                    use_balance=True,
                    allow_early_downgrade=True,
                )
                if res.get("status") == "paid" and not user.is_bot_blocked:
                    await session.refresh(sub)
                    push_tg_id = user.telegram_id
                    push_text = NOTIFY_WI_AUTORENEWED.format(
                        amount=res.get("charged_balance", int(due_rub)),
                        devices=eff_limit,
                        date=format_tg_time(sub.expires_at) if sub.expires_at else "",
                    )
        except Exception as exc:
            logger.warning("WI auto-renew failed for sub_id=%s: %s", sid, exc)
            continue

        if push_tg_id and push_text:
            await _send_autorenew_notice(bot, push_tg_id, push_text, reply_markup=push_kb)


async def _send_pre_expiry_notifications(
    bot: Bot,
    current_time,
):
    async with session_scope() as session:
        stmt = (
            select(User.id)
            .join(VlessSubscription, VlessSubscription.user_id == User.id)
            .where(
                VlessSubscription.expires_at > current_time,
                VlessSubscription.expires_at <= current_time + timedelta(days=3),
                User.is_bot_blocked.is_(False),
                User.is_deleted.is_(False),
                or_(
                    VlessSubscription.notified_3d.is_(False),
                    VlessSubscription.notified_1d.is_(False),
                    VlessSubscription.notified_2h.is_(False),
                ),
            )
            .order_by(VlessSubscription.expires_at.asc())
            .limit(500)
        )

        result = await session.execute(stmt)

        user_ids = [row[0] for row in result.all()]

    if not user_ids:
        return

    for i in range(0, len(user_ids), NOTIFICATION_BATCH_SIZE):
        batch_ids = user_ids[i : i + NOTIFICATION_BATCH_SIZE]

        for uid in batch_ids:
            async with session_scope() as session:
                user = await session.scalar(
                    select(User).where(User.id == uid).with_for_update(skip_locked=True)
                )
                if user is None:
                    continue

                vless_sub = await session.scalar(
                    select(VlessSubscription).where(VlessSubscription.user_id == uid).with_for_update(skip_locked=True)
                )
                if vless_sub is None:
                    continue

                if user.is_bot_blocked or user.is_deleted:
                    continue

                if not vless_sub.expires_at or vless_sub.expires_at <= current_time:
                    continue

                if vless_sub.expires_at > current_time + timedelta(days=3):
                    continue

                retry_count = user.notification_retry_count or 0

                if retry_count >= MAX_RETRY_COUNT:
                    # Не помечаем notified_* как True при исчерпании ретраев, чтобы не терять уведомления при сбое Telegram.
                    user.notification_retry_count = 0
                    user.last_notification_attempt = None
                    await session.flush()

                    logger.warning(
                        "Max retries reached for user %s, will retry in next global cycle",
                        user.telegram_id,
                    )
                    continue

                if retry_count > 0 and user.last_notification_attempt:
                    backoff_delay = _get_backoff_delay(retry_count - 1)

                    time_since_last = (
                        current_time - user.last_notification_attempt
                    ).total_seconds()

                    if time_since_last < backoff_delay:
                        continue

                time_left = vless_sub.expires_at - current_time

                msg = None
                notification_type = None

                if time_left <= timedelta(hours=2) and not vless_sub.notified_2h:
                    msg = NOTIFY_2H
                    notification_type = "2h"

                elif time_left <= timedelta(days=1) and not vless_sub.notified_1d:
                    msg = NOTIFY_1D
                    notification_type = "1d"

                elif time_left <= timedelta(days=3) and not vless_sub.notified_3d:
                    msg = NOTIFY_3D
                    notification_type = "3d"

                if not msg:
                    continue

                _maybe_reset_retry_on_type_change(
                    user,
                    notification_type,
                    vless_sub=vless_sub,
                )

                retry_count = user.notification_retry_count or 0

                try:
                    await global_send_limiter.acquire()

                    await safe_send_message(
                        bot,
                        user.telegram_id,
                        msg,
                        reply_markup=get_pre_expiry_keyboard(),
                        parse_mode="HTML",
                        raise_forbidden=True,
                    )

                    user.notification_retry_count = 0
                    user.last_notification_attempt = current_time
                    await session.flush()

                    if notification_type == "2h":
                        vless_sub.notified_2h = True
                        vless_sub.notified_1d = True
                        vless_sub.notified_3d = True

                    elif notification_type == "1d":
                        vless_sub.notified_1d = True
                        vless_sub.notified_3d = True

                    elif notification_type == "3d":
                        vless_sub.notified_3d = True

                except TelegramForbiddenError:
                    user.is_bot_blocked = True

                except Exception as e:
                    user.notification_retry_count = retry_count + 1
                    user.last_notification_attempt = current_time
                    await session.flush()

                    logger.warning(
                        "Failed to send pre-expiry notification to %s: %s",
                        user.telegram_id,
                        e,
                    )


async def _send_post_expiry_notifications(
    bot: Bot,
    current_time,
):
    expiry_lookback = current_time - timedelta(days=14)

    async with session_scope() as session:
        stmt = (
            select(User.id)
            .join(VlessSubscription, VlessSubscription.user_id == User.id)
            .where(
                VlessSubscription.expires_at.is_not(None),
                VlessSubscription.expires_at < current_time,
                VlessSubscription.expires_at > expiry_lookback,
                User.is_bot_blocked.is_(False),
                User.is_deleted.is_(False),
                VlessSubscription.notified_expired.is_(False),
            )
            .order_by(VlessSubscription.expires_at.asc())
            .limit(500)
        )

        result = await session.execute(stmt)

        user_ids = [row[0] for row in result.all()]

    if not user_ids:
        return

    for i in range(0, len(user_ids), NOTIFICATION_BATCH_SIZE):
        batch_ids = user_ids[i : i + NOTIFICATION_BATCH_SIZE]

        for uid in batch_ids:
            async with session_scope() as session:
                user = await session.scalar(
                    select(User).where(User.id == uid).with_for_update(skip_locked=True)
                )
                if user is None:
                    continue

                vless_sub = await session.scalar(
                    select(VlessSubscription).where(VlessSubscription.user_id == uid).with_for_update(skip_locked=True)
                )
                if vless_sub is None or not vless_sub.expires_at or vless_sub.expires_at >= current_time:
                    continue

                if user.is_bot_blocked or user.is_deleted or vless_sub.notified_expired:
                    continue

                retry_count = user.notification_retry_count or 0

                if retry_count >= MAX_RETRY_COUNT:
                    user.notification_retry_count = 0
                    user.last_notification_attempt = None
                    await session.flush()

                    logger.warning(
                        "Max retries reached for user %s, will retry in next global cycle",
                        user.telegram_id,
                    )
                    continue

                if retry_count > 0 and user.last_notification_attempt:
                    backoff_delay = _get_backoff_delay(retry_count - 1)

                    time_since_last = (
                        current_time - user.last_notification_attempt
                    ).total_seconds()

                    if time_since_last < backoff_delay:
                        continue

                _maybe_reset_retry_on_type_change(
                    user,
                    "expired",
                    vless_sub=vless_sub,
                )

                retry_count = user.notification_retry_count or 0

                try:
                    await global_send_limiter.acquire()

                    await safe_send_message(
                        bot,
                        user.telegram_id,
                        NOTIFY_EXPIRED,
                        reply_markup=get_post_expiry_keyboard(),
                        parse_mode="HTML",
                        raise_forbidden=True,
                    )

                    user.notification_retry_count = 0
                    user.last_notification_attempt = current_time
                    vless_sub.notified_expired = True
                    await session.flush()

                except TelegramForbiddenError:
                    user.is_bot_blocked = True

                except Exception as e:
                    user.notification_retry_count = retry_count + 1
                    user.last_notification_attempt = current_time
                    await session.flush()

                    logger.warning(
                        "Failed to send post-expiry notification to %s: %s",
                        user.telegram_id,
                        e,
                    )


async def _send_white_internet_notifications(
    bot: Bot | None,
    current_time: datetime,
) -> None:
    if bot is None:
        return

    from config.enums import WhiteInternetStatus
    from database.models import User, WhiteInternetSubscription
    from utils.formatters import format_tg_time
    from bot.texts.runtime.notifications import (
        NOTIFY_WI_1D,
        NOTIFY_WI_2H,
        NOTIFY_WI_3D,
        NOTIFY_WI_EXPIRED,
    )

    expired_cutoff = current_time - timedelta(days=14)
    cutoff = current_time + timedelta(days=3)

    async with session_scope() as session:
        # Sweep stale subscriptions older than expired_cutoff so they exit ix_wi_subs_expiring_notify
        await session.execute(
            update(WhiteInternetSubscription)
            .where(
                WhiteInternetSubscription.status.in_([
                    WhiteInternetStatus.ACTIVE,
                    WhiteInternetStatus.EXHAUSTED,
                    WhiteInternetStatus.EXPIRED,
                ]),
                WhiteInternetSubscription.expires_at.is_not(None),
                WhiteInternetSubscription.expires_at < expired_cutoff,
                or_(
                    WhiteInternetSubscription.notified_3d.is_(False),
                    WhiteInternetSubscription.notified_1d.is_(False),
                    WhiteInternetSubscription.notified_2h.is_(False),
                    WhiteInternetSubscription.notified_expired.is_(False),
                ),
            )
            .values(
                notified_3d=True,
                notified_1d=True,
                notified_2h=True,
                notified_expired=True,
            )
        )

        stmt = (
            select(WhiteInternetSubscription.id)
            .join(User, WhiteInternetSubscription.user_id == User.id)
            .where(
                WhiteInternetSubscription.status.in_([
                    WhiteInternetStatus.ACTIVE,
                    WhiteInternetStatus.EXHAUSTED,
                    WhiteInternetStatus.EXPIRED,
                ]),
                WhiteInternetSubscription.expires_at.is_not(None),
                WhiteInternetSubscription.expires_at >= expired_cutoff,
                WhiteInternetSubscription.expires_at <= cutoff,
                or_(
                    WhiteInternetSubscription.notified_3d.is_(False),
                    WhiteInternetSubscription.notified_1d.is_(False),
                    WhiteInternetSubscription.notified_2h.is_(False),
                    WhiteInternetSubscription.notified_expired.is_(False),
                ),
                User.is_bot_blocked.is_(False),
                User.is_deleted.is_(False),
            )
            .order_by(WhiteInternetSubscription.expires_at.asc())
            .limit(500)
        )
        result = await session.execute(stmt)
        sub_ids = [row[0] for row in result.all()]

    if not sub_ids:
        return

    for i in range(0, len(sub_ids), NOTIFICATION_BATCH_SIZE):
        batch_ids = sub_ids[i : i + NOTIFICATION_BATCH_SIZE]
        for sid in batch_ids:
            async with session_scope() as session:
                sub = await session.scalar(
                    select(WhiteInternetSubscription)
                    .where(WhiteInternetSubscription.id == sid)
                    .with_for_update(skip_locked=True)
                )
                if sub is None:
                    continue

                if sub.status not in (
                    WhiteInternetStatus.ACTIVE,
                    WhiteInternetStatus.EXHAUSTED,
                    WhiteInternetStatus.EXPIRED,
                ):
                    continue

                if not sub.expires_at or sub.expires_at > cutoff or sub.expires_at < expired_cutoff:
                    continue

                user = await session.scalar(
                    select(User).where(User.id == sub.user_id)
                )
                if (
                    not user
                    or user.is_bot_blocked
                    or user.is_deleted
                ):
                    continue

                time_left = sub.expires_at - current_time
                notify_type = None
                msg = None

                if time_left.total_seconds() <= 0:
                    if not sub.notified_expired:
                        notify_type = "expired"
                        msg = NOTIFY_WI_EXPIRED
                elif time_left <= timedelta(hours=2):
                    if not sub.notified_2h:
                        notify_type = "2h"
                        msg = NOTIFY_WI_2H.format(countdown=_format_countdown(time_left))
                elif time_left <= timedelta(days=1):
                    if not sub.notified_1d:
                        notify_type = "1d"
                        msg = NOTIFY_WI_1D.format(countdown=_format_countdown(time_left))
                elif time_left <= timedelta(days=3):
                    if not sub.notified_3d:
                        notify_type = "3d"
                        msg = NOTIFY_WI_3D.format(
                            date=format_tg_time(sub.expires_at)
                        )

                if not notify_type or not msg:
                    if time_left.total_seconds() <= 0:
                        logger.warning(
                            "WI sub %s expired without notify_type "
                            "(status=%s, flags 3d=%s 1d=%s 2h=%s expired=%s); "
                            "marking notified to avoid rescan loop",
                            sub.id,
                            sub.status,
                            sub.notified_3d,
                            sub.notified_1d,
                            sub.notified_2h,
                            sub.notified_expired,
                        )
                        sub.notified_expired = True
                        sub.notified_2h = True
                        sub.notified_1d = True
                        sub.notified_3d = True
                        await session.flush()
                    continue

                try:
                    await global_send_limiter.acquire()
                    wi_kb = (
                        get_post_expiry_keyboard()
                        if notify_type == "expired"
                        else get_pre_expiry_keyboard()
                    )
                    await safe_send_message(
                        bot,
                        user.telegram_id,
                        msg,
                        reply_markup=wi_kb,
                        parse_mode="HTML",
                        raise_forbidden=True,
                    )
                    if notify_type == "expired":
                        sub.notified_expired = True
                        sub.notified_2h = True
                        sub.notified_1d = True
                        sub.notified_3d = True
                    elif notify_type == "2h":
                        sub.notified_2h = True
                        sub.notified_1d = True
                        sub.notified_3d = True
                    elif notify_type == "1d":
                        sub.notified_1d = True
                        sub.notified_3d = True
                    elif notify_type == "3d":
                        sub.notified_3d = True
                    await session.flush()
                except TelegramForbiddenError:
                    user.is_bot_blocked = True
                    await session.flush()
                except Exception as e:
                    logger.warning(
                        "Failed to send WI notification to %s: %s",
                        user.telegram_id,
                        e,
                    )
