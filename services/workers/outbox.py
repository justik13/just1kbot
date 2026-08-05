import asyncio
import logging

from aiogram import Bot
from sqlalchemy import select

from bot import texts
from database.connection import session_scope
from database.models import User
from database.outbox_models import OutboxNotification
from services.workers.notifications import global_send_limiter

logger = logging.getLogger("OutboxWorker")


async def outbox_worker_loop(
    bot: Bot,
    shutdown_event: asyncio.Event,
):
    logger.info("Outbox worker started")
    while not shutdown_event.is_set():
        try:
            async with session_scope() as session:
                pending = (
                    await session.execute(
                        select(OutboxNotification)
                        .where(OutboxNotification.status == "pending")
                        .order_by(OutboxNotification.created_at.asc())
                        .limit(50)
                        .with_for_update(skip_locked=True)
                    )
                ).scalars().all()

                for notification in pending:
                    user = await session.get(User, notification.user_id)
                    if not user or user.is_bot_blocked or user.is_deleted:
                        notification.status = "cancelled"
                        continue

                    try:
                        await global_send_limiter.acquire()

                        if notification.event_type == "topup_success":
                            text_to_send = texts.NOTIFY_TOPUP_SUCCESS.format(
                                **notification.payload
                            )
                        elif notification.event_type == "chargeback_debt":
                            text_to_send = texts.NOTIFY_CHARGEBACK_DEBT
                        elif notification.event_type == "admin_sub_reduce":
                            text_to_send = texts.NOTIFY_ADMIN_SUB_REDUCE
                        else:
                            text_to_send = notification.payload.get(
                                "text",
                                texts.NOTIFY_GENERIC,
                            )

                        await bot.send_message(
                            user.telegram_id,
                            text_to_send,
                            parse_mode="HTML",
                        )
                        notification.status = "sent"
                    except Exception as exc:
                        logger.warning(
                            "Failed to send outbox notification %s to %s: %s",
                            notification.id,
                            user.telegram_id,
                            exc,
                        )
                        notification.attempts += 1
                        if notification.attempts >= 5:
                            notification.status = "failed"

            try:
                await asyncio.wait_for(
                    shutdown_event.wait(),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                pass
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error(
                "Outbox worker error: %s",
                exc,
                exc_info=True,
            )
            await asyncio.sleep(5.0)

    logger.info("Outbox worker stopped")
