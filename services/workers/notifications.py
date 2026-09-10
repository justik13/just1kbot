import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from cachetools import TTLCache
from sqlalchemy import or_, select

from bot.keyboards.notifications import (
    get_post_expiry_keyboard,
    get_pre_expiry_keyboard,
)
from bot.texts.runtime.notifications import (
    BTN_EXTEND_WHITE_INTERNET,
    NOTIFY_1D,
    NOTIFY_2H,
    NOTIFY_3D,
    NOTIFY_EXPIRED,
    NOTIFY_GRACE_12H,
    NOTIFY_WI_1D,
    NOTIFY_WI_2H,
    NOTIFY_WI_3D,
    NOTIFY_WI_EXPIRED,
    TIME_DAYS_HOURS_FORMAT,
    TIME_HOURS_MINUTES_FORMAT,
    TIME_SOON_LABEL,
)
from config.constants import (
    GRACE_PERIOD_HOURS,
    NOTIFICATION_INTERVAL,
    WORKER_ERROR_SLEEP_INTERVAL,
)
from database.connection import session_scope
from database.models import User
from utils.datetime_helpers import now_utc
from utils.rate_limiter import global_send_limiter

logger = logging.getLogger("BackgroundWorker")

MAX_RETRY_COUNT = 4
BACKOFF_BASE_INTERVAL = NOTIFICATION_INTERVAL
NOTIFICATION_BATCH_SIZE = 20
NOTIFICATION_START_DELAY = 60.0

_last_notification_type: TTLCache[int, str] = TTLCache(
    maxsize=10000,
    ttl=86400,
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
) -> None:
    last_type = _last_notification_type.get(user.id)
    if last_type is None:
        last_type = _infer_last_notification_type(user)

    if last_type and last_type != notification_type:
        user.notification_retry_count = 0
        user.last_notification_attempt = None

    _last_notification_type[user.id] = notification_type


def _infer_last_notification_type(user: User) -> str | None:
    if user.notified_grace_12h:
        return "grace_12h"
    if user.notified_expired:
        return "expired"
    if user.notified_2h:
        return "2h"
    if user.notified_1d:
        return "1d"
    if user.notified_3d:
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

            await asyncio.sleep(WORKER_ERROR_SLEEP_INTERVAL)

            continue

        try:
            await asyncio.wait_for(
                shutdown_event.wait(),
                timeout=NOTIFICATION_INTERVAL,
            )

            break

        except asyncio.TimeoutError:
            continue

    logger.info("Notifications worker stopped gracefully")


async def _send_pre_expiry_notifications(
    bot: Bot,
    current_time,
):
    async with session_scope() as session:
        stmt = (
            select(User.id)
            .where(
                User.subscription_end > current_time,
                User.subscription_end <= current_time + timedelta(days=3),
                User.is_banned.is_(False),
                User.is_bot_blocked.is_(False),
                User.is_deleted.is_(False),
                or_(
                    User.notified_3d.is_(False),
                    User.notified_1d.is_(False),
                    User.notified_2h.is_(False),
                ),
            )
            .order_by(User.subscription_end.asc())
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

                if user.is_banned or user.is_bot_blocked or user.is_deleted:
                    continue

                if not user.subscription_end or user.subscription_end <= current_time:
                    continue

                if user.subscription_end > current_time + timedelta(days=3):
                    continue

                retry_count = user.notification_retry_count or 0

                if retry_count >= MAX_RETRY_COUNT:
                    # P0-4: НЕ помечаем notified_* как True при исчерпании ретраев
                    # Это предотвращает потерю уведомлений, если Telegram был недоступен
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

                time_left = user.subscription_end - current_time

                msg = None
                notification_type = None

                if time_left <= timedelta(hours=2) and not user.notified_2h:
                    msg = NOTIFY_2H
                    notification_type = "2h"

                elif time_left <= timedelta(days=1) and not user.notified_1d:
                    msg = NOTIFY_1D
                    notification_type = "1d"

                elif time_left <= timedelta(days=3) and not user.notified_3d:
                    msg = NOTIFY_3D
                    notification_type = "3d"

                if not msg:
                    continue

                _maybe_reset_retry_on_type_change(
                    user,
                    notification_type,
                )

                retry_count = user.notification_retry_count or 0

                try:
                    await global_send_limiter.acquire()

                    await bot.send_message(
                        user.telegram_id,
                        msg,
                        reply_markup=get_pre_expiry_keyboard(),
                        parse_mode="HTML",
                    )

                    user.notification_retry_count = 0
                    user.last_notification_attempt = current_time
                    await session.flush()

                    if notification_type == "2h":
                        user.notified_2h = True
                        user.notified_1d = True
                        user.notified_3d = True

                    elif notification_type == "1d":
                        user.notified_1d = True
                        user.notified_3d = True

                    elif notification_type == "3d":
                        user.notified_3d = True

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
    grace_start = current_time - timedelta(hours=GRACE_PERIOD_HOURS)

    async with session_scope() as session:
        stmt = (
            select(User.id)
            .where(
                User.subscription_end.is_not(None),
                User.subscription_end < current_time,
                User.subscription_end > grace_start,
                User.is_banned.is_(False),
                User.is_bot_blocked.is_(False),
                User.is_deleted.is_(False),
                or_(
                    User.notified_expired.is_(False),
                    User.notified_grace_12h.is_(False),
                ),
            )
            .order_by(User.subscription_end.asc())
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

                if not user.subscription_end or user.subscription_end >= current_time:
                    continue

                if user.is_banned or user.is_bot_blocked or user.is_deleted:
                    continue

                deletion_time = user.subscription_end + timedelta(
                    hours=GRACE_PERIOD_HOURS,
                )

                if current_time >= deletion_time:
                    continue

                retry_count = user.notification_retry_count or 0

                if retry_count >= MAX_RETRY_COUNT:
                    # P0-4: НЕ помечаем notified_* как True при исчерпании ретраев
                    # Это предотвращает потерю уведомлений, если Telegram был недоступен
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

                time_until_delete = deletion_time - current_time

                msg = None
                notification_type = None

                if (
                    not user.notified_grace_12h
                    and current_time >= deletion_time - timedelta(hours=12)
                ):
                    msg = NOTIFY_GRACE_12H
                    notification_type = "grace_12h"

                elif not user.notified_expired:
                    countdown = _format_countdown(time_until_delete)

                    msg = NOTIFY_EXPIRED.format(
                        countdown=countdown,
                    )

                    notification_type = "expired"

                if not msg:
                    continue

                _maybe_reset_retry_on_type_change(
                    user,
                    notification_type,
                )

                retry_count = user.notification_retry_count or 0

                try:
                    await global_send_limiter.acquire()

                    await bot.send_message(
                        user.telegram_id,
                        msg,
                        reply_markup=get_post_expiry_keyboard(),
                        parse_mode="HTML",
                    )

                    user.notification_retry_count = 0
                    user.last_notification_attempt = current_time
                    await session.flush()

                    if notification_type == "grace_12h":
                        user.notified_grace_12h = True
                        user.notified_expired = True

                    elif notification_type == "expired":
                        user.notified_expired = True

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


_wi_notification_cache: TTLCache[tuple[int, str], bool] = TTLCache(
    maxsize=10000,
    ttl=86400 * 7,
)


async def _send_white_internet_notifications(
    bot: Bot,
    current_time: datetime,
) -> None:
    from database.models import WhiteInternetSubscription
    from config.enums import WhiteInternetStatus
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    async with session_scope() as session:
        cutoff = current_time + timedelta(days=3)
        stmt = (
            select(WhiteInternetSubscription, User.telegram_id, User.is_bot_blocked)
            .join(User, WhiteInternetSubscription.user_id == User.id)
            .where(
                WhiteInternetSubscription.status.in_([
                    WhiteInternetStatus.ACTIVE,
                    WhiteInternetStatus.EXHAUSTED,
                ]),
                WhiteInternetSubscription.expires_at.isnot(None),
                WhiteInternetSubscription.expires_at <= cutoff,
                User.is_bot_blocked.is_(False),
                User.is_banned.is_(False),
                User.is_deleted.is_(False),
            )
            .limit(NOTIFICATION_BATCH_SIZE)
        )
        rows = (await session.execute(stmt)).all()

        for sub, telegram_id, is_blocked in rows:
            if not telegram_id or is_blocked:
                continue

            time_left = sub.expires_at - current_time
            notify_type = None
            msg = None

            if time_left.total_seconds() <= 0:
                if (sub.id, "expired") not in _wi_notification_cache:
                    notify_type = "expired"
                    msg = NOTIFY_WI_EXPIRED
            elif time_left <= timedelta(hours=2):
                if (sub.id, "2h") not in _wi_notification_cache:
                    notify_type = "2h"
                    msg = NOTIFY_WI_2H.format(countdown=_format_countdown(time_left))
            elif time_left <= timedelta(days=1):
                if (sub.id, "1d") not in _wi_notification_cache:
                    notify_type = "1d"
                    msg = NOTIFY_WI_1D.format(countdown=_format_countdown(time_left))
            elif time_left <= timedelta(days=3):
                if (sub.id, "3d") not in _wi_notification_cache:
                    notify_type = "3d"
                    msg = NOTIFY_WI_3D.format(date=sub.expires_at.strftime('%d.%m.%Y %H:%M'))

            if not notify_type or not msg:
                continue

            kb = InlineKeyboardBuilder()
            kb.button(text=BTN_EXTEND_WHITE_INTERNET, callback_data="white_internet")

            try:
                await global_send_limiter.acquire()
                await bot.send_message(
                    telegram_id,
                    msg,
                    reply_markup=kb.as_markup(),
                    parse_mode="HTML",
                )
                _wi_notification_cache[(sub.id, notify_type)] = True
            except TelegramForbiddenError:
                _wi_notification_cache[(sub.id, notify_type)] = True
            except Exception as e:
                logger.warning(
                    "Failed to send WI notification to %s: %s",
                    telegram_id,
                    e,
                )

