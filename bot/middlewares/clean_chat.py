import asyncio
import logging

from aiogram import BaseMiddleware
from aiogram.types import Message

logger = logging.getLogger(__name__)

_delete_queue: asyncio.Queue | None = None
_direct_delete_tasks: set[asyncio.Task] = set()
_delete_worker_task: asyncio.Task | None = None
_DELETE_RATE = 10
_DELETE_BATCH_SIZE = 5
_QUEUE_STOP_DRAIN_TIMEOUT = 5.0


async def _delete_message(bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.debug(
            f"Failed to delete message {message_id} in {chat_id}: {e}"
        )


async def _delete_worker():
    while True:
        try:
            batch = []
            for _ in range(_DELETE_BATCH_SIZE):
                try:
                    item = await asyncio.wait_for(
                        _delete_queue.get(), timeout=0.1
                    )
                    batch.append(item)
                except asyncio.TimeoutError:
                    break
            for bot, chat_id, message_id in batch:
                await _delete_message(bot, chat_id, message_id)
                _delete_queue.task_done()
            await asyncio.sleep(1.0 / _DELETE_RATE * _DELETE_BATCH_SIZE)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"CleanChat worker error: {e}")
            await asyncio.sleep(1.0)


def _ensure_worker_started():
    global _delete_queue, _delete_worker_task
    if _delete_queue is None:
        _delete_queue = asyncio.Queue(maxsize=5000)
    if _delete_worker_task is None or _delete_worker_task.done():
        _delete_worker_task = asyncio.create_task(_delete_worker())


async def stop_clean_chat_worker():
    global _delete_worker_task, _delete_queue
    if _delete_queue is not None:
        try:
            await asyncio.wait_for(
                _delete_queue.join(), timeout=_QUEUE_STOP_DRAIN_TIMEOUT
            )
        except asyncio.TimeoutError:
            logger.warning(
                "CleanChat worker stopped before all queued deletions were drained"
            )
    if _delete_worker_task and not _delete_worker_task.done():
        _delete_worker_task.cancel()
        try:
            await _delete_worker_task
        except asyncio.CancelledError:
            pass
    _delete_worker_task = None
    _delete_queue = None
    logger.info("CleanChat worker stopped")


class CleanChatMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            if any(
                [
                    event.pinned_message,
                    event.new_chat_members,
                    event.left_chat_member,
                    event.new_chat_title,
                    event.new_chat_photo,
                    event.delete_chat_photo,
                    event.group_chat_created,
                    event.supergroup_chat_created,
                    event.channel_chat_created,
                    event.migrate_to_chat_id,
                    event.migrate_from_chat_id,
                ]
            ):
                return await handler(event, data)

            state = data.get("raw_state") or data.get("state")
            if state:
                try:
                    if await state.get_state() is not None:
                        return await handler(event, data)
                except Exception:
                    pass

            _ensure_worker_started()
            try:
                _delete_queue.put_nowait(
                    (event.bot, event.chat.id, event.message_id)
                )
            except asyncio.QueueFull:
                logger.warning(
                    f"CleanChat queue full, deleting message "
                    f"{event.message_id} in {event.chat.id} without queue"
                )
                task = asyncio.create_task(
                    _delete_message(event.bot, event.chat.id, event.message_id)
                )
                _direct_delete_tasks.add(task)
                task.add_done_callback(_direct_delete_tasks.discard)
            except Exception as e:
                logger.debug(f"Failed to enqueue message deletion: {e}")

        return await handler(event, data)