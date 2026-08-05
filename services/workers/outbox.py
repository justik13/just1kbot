import asyncio
import logging
from aiogram import Bot
from sqlalchemy import select
from bot import texts
from database.connection import session_scope
from database.outbox_models import OutboxNotification
from database.models import User
from services.workers.notifications import global_send_limiter

logger = logging.getLogger("OutboxWorker")

async def outbox_worker_loop(bot: Bot, shutdown_event: asyncio.Event):
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
                    if not user or user.is_blocked_bot_sync() or user.is_deleted:
                        notification.status = "cancelled"
                        continue

                    try:
                        await global_send_limiter.acquire()

                        text_to_send = ""
                        if notification.event_type == "topup_success":
                            text_to_send = getattr(texts, "NOTIFY_TOPUP_SUCCESS", "Баланс успешно пополнен на {amount} руб.").format(**notification.payload)
                        elif notification.event_type == "chargeback_debt":
                            text_to_send = getattr(texts, "NOTIFY_CHARGEBACK_DEBT", "По вашему аккаунту зафиксирован чарджбэк. VPN заблокирован.")
                        elif notification.event_type == "admin_sub_reduce":
                            text_to_send = getattr(texts, "NOTIFY_ADMIN_SUB_REDUCE", "Администратор изменил срок вашей подписки.")
                        else:
                            text_to_send = notification.payload.get("text", "Новое уведомление")

                        await bot.send_message(user.telegram_id, text_to_send, parse_mode="HTML")
                        notification.status = "sent"
                    except Exception as e:
                        logger.warning(f"Failed to send outbox notification {notification.id} to {user.telegram_id}: {e}")
                        notification.attempts += 1
                        if notification.attempts >= 5:
                            notification.status = "failed"

            # Wait before next iteration
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Outbox worker error: {e}", exc_info=True)
            await asyncio.sleep(5.0)

    logger.info("Outbox worker stopped")
