import asyncio
import html
import logging
import re
import time
from typing import Optional, List

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, InputFile
from cachetools import TTLCache

from bot.constants import HUB_CACHE_MAX_SIZE, HUB_CACHE_TTL
from database.connection import session_scope
from database.repositories import hub_repo
from utils.text_limits import split_text_by_lines

logger = logging.getLogger(__name__)

_hub_cache = TTLCache(maxsize=HUB_CACHE_MAX_SIZE, ttl=HUB_CACHE_TTL)

_last_cleanup_time: float = 0.0
_CLEANUP_INTERVAL = 3600.0

_hub_render_locks: dict[int, tuple[asyncio.Lock, float]] = {}
_RENDER_LOCK_TTL = 3600.0
_last_render_lock_cleanup: float = 0.0


def _get_hub_render_lock(chat_id: int) -> asyncio.Lock:
    global _last_render_lock_cleanup

    now = time.monotonic()

    if now - _last_render_lock_cleanup > _CLEANUP_INTERVAL:
        _cleanup_render_locks(now)
        _last_render_lock_cleanup = now

    if chat_id not in _hub_render_locks:
        _hub_render_locks[chat_id] = (asyncio.Lock(), now)
    else:
        lock, _ = _hub_render_locks[chat_id]
        _hub_render_locks[chat_id] = (lock, now)

    return _hub_render_locks[chat_id][0]


def _cleanup_render_locks(now: float) -> None:
    old = [
        cid
        for cid, (lock, last_used) in _hub_render_locks.items()
        if now - last_used > _RENDER_LOCK_TTL and not lock.locked()
    ]

    for cid in old:
        del _hub_render_locks[cid]

    if old:
        logger.debug(
            "Hub render locks cleanup: removed %s, %s remaining",
            len(old),
            len(_hub_render_locks),
        )


async def _safe_delete_batch(
    bot,
    chat_id: int,
    msg_ids: List[int],
) -> tuple[list[int], list[int]]:
    deleted_ids: list[int] = []
    failed_ids: list[int] = []

    for msg_id in msg_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            deleted_ids.append(msg_id)
        except TelegramBadRequest as e:
            err_str = str(e).lower()
            if (
                "message to delete not found" in err_str
                or "message identifier is not valid" in err_str
                or "chat not found" in err_str
                or "message can't be deleted" in err_str
            ):
                deleted_ids.append(msg_id)
            else:
                failed_ids.append(msg_id)
                logger.warning(
                    "TelegramBadRequest on delete_message %s in %s: %s",
                    msg_id,
                    chat_id,
                    e,
                )
        except Exception as e:
            failed_ids.append(msg_id)
            logger.error(
                "Unexpected error deleting message %s in %s: %s",
                msg_id,
                chat_id,
                e,
            )

    return deleted_ids, failed_ids


def safe(value: Optional[str]) -> str:
    if value is None:
        return "—"
    return html.escape(str(value))


def _maybe_cleanup_cache() -> None:
    global _last_cleanup_time

    now = time.monotonic()
    if now - _last_cleanup_time < _CLEANUP_INTERVAL:
        return

    _last_cleanup_time = now

    if len(_hub_cache) >= HUB_CACHE_MAX_SIZE * 0.8:
        expired_keys = []

        for key in list(_hub_cache.keys()):
            try:
                _ = _hub_cache[key]
            except KeyError:
                expired_keys.append(key)

        for key in expired_keys:
            try:
                del _hub_cache[key]
            except KeyError:
                pass

        logger.info(
            "Hub cache cleanup: %s expired entries removed",
            len(expired_keys),
        )


async def _load_hub_ids_from_db(chat_id: int) -> List[int]:
    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        return list(cached["ids"])

    try:
        async with session_scope() as session:
            ids = await hub_repo.get_hub_message_ids(session, chat_id)
            _hub_cache[chat_id] = {"ids": list(ids)}
            return list(ids)
    except Exception as e:
        logger.warning("Failed to load hub ids from DB for chat %s: %s", chat_id, e)
        _hub_cache[chat_id] = {"ids": []}
        return []


async def _store_hub_id_in_db(chat_id: int, message_id: int) -> None:
    try:
        async with session_scope() as session:
            await hub_repo.add_hub_message_id(session, chat_id, message_id)
    except Exception as e:
        logger.warning("Failed to store hub id in DB for chat %s: %s", chat_id, e)

    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        if message_id not in cached["ids"]:
            cached["ids"].append(message_id)
    else:
        _hub_cache[chat_id] = {"ids": [message_id]}


async def _remove_hub_ids_from_db(chat_id: int, message_ids: List[int]) -> None:
    if not message_ids:
        return

    try:
        async with session_scope() as session:
            await hub_repo.remove_hub_message_ids(session, chat_id, message_ids)
    except Exception as e:
        logger.warning("Failed to remove hub ids from DB for chat %s: %s", chat_id, e)

    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        old_set = set(message_ids)
        cached["ids"] = [mid for mid in cached["ids"] if mid not in old_set]


async def get_hub_ids(chat_id: int) -> List[int]:
    return await _load_hub_ids_from_db(chat_id)


async def _delete_hub_messages(bot, chat_id: int, msg_ids: List[int]) -> List[int]:
    if not msg_ids:
        return []

    deleted_ids, failed_ids = await _safe_delete_batch(bot, chat_id, msg_ids)

    if deleted_ids:
        await _remove_hub_ids_from_db(chat_id, deleted_ids)

    if failed_ids:
        logger.warning(
            "Failed to delete %s hub messages in chat %s. "
            "They will be retried on next hub render.",
            len(failed_ids),
            chat_id,
        )

    return failed_ids


async def delete_hub_ids(bot, chat_id: int, msg_ids: List[int]) -> List[int]:
    if not msg_ids:
        return []

    lock = _get_hub_render_lock(chat_id)
    async with lock:
        return await _delete_hub_messages(bot, chat_id, msg_ids)


async def render_hub(
    bot,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup,
    parse_mode: str = "HTML",
    force_new: bool = False,
    trigger_message_id: Optional[int] = None,
) -> int:
    """Render a single navigable hub, editing the target text message first."""
    _maybe_cleanup_cache()

    lock = _get_hub_render_lock(chat_id)
    async with lock:
        old_ids = await _load_hub_ids_from_db(chat_id)
        text_parts = split_text_by_lines(text, limit=4096) or ["—"]

        target_edit_id = None
        if not force_new and len(text_parts) == 1:
            if trigger_message_id:
                target_edit_id = trigger_message_id
            elif old_ids:
                target_edit_id = old_ids[-1]

        edited = False
        if target_edit_id:
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=target_edit_id,
                    text=text_parts[0],
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                )
                edited = True
            except TelegramBadRequest as exc:
                error = str(exc).lower()
                if "message is not modified" in error:
                    edited = True
                else:
                    logger.debug(
                        "Hub edit unavailable for chat %s message %s: %s",
                        chat_id,
                        target_edit_id,
                        exc,
                    )
            except Exception as exc:
                logger.warning(
                    "Unexpected hub edit failure for chat %s message %s: %s",
                    chat_id,
                    target_edit_id,
                    type(exc).__name__,
                )

        if edited and target_edit_id:
            stale_ids = [mid for mid in old_ids if mid != target_edit_id]
            if stale_ids:
                await _delete_hub_messages(bot, chat_id, stale_ids)
            _hub_cache[chat_id] = {"ids": [target_edit_id]}
            await _store_hub_id_in_db(chat_id, target_edit_id)
            return target_edit_id

        if trigger_message_id and trigger_message_id not in old_ids:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=trigger_message_id)
            except Exception:
                pass

        sent_ids: list[int] = []
        for index, part in enumerate(text_parts):
            is_last = index == len(text_parts) - 1
            markup = reply_markup if is_last else None
            try:
                message = await bot.send_message(
                    chat_id=chat_id,
                    text=part,
                    reply_markup=markup,
                    parse_mode=parse_mode,
                )
            except TelegramBadRequest as exc:
                error = str(exc).lower()
                if "parse" not in error and "entities" not in error:
                    raise
                logger.warning(
                    "HTML parse failed in render_hub for chat %s; using plain text",
                    chat_id,
                )
                plain = html.unescape(re.sub(r"<[^>]+>", "", part))
                message = await bot.send_message(
                    chat_id=chat_id,
                    text=plain,
                    reply_markup=markup,
                )
            sent_ids.append(message.message_id)

        if old_ids:
            await _delete_hub_messages(bot, chat_id, old_ids)
        for message_id in sent_ids:
            await _store_hub_id_in_db(chat_id, message_id)
        return sent_ids[-1]



async def send_hub_photo(
    bot,
    chat_id: int,
    photo: InputFile,
    caption: str,
    reply_markup: InlineKeyboardMarkup = None,
    parse_mode: str = "HTML",
) -> int:
    _maybe_cleanup_cache()

    lock = _get_hub_render_lock(chat_id)
    async with lock:
        old_ids = await _load_hub_ids_from_db(chat_id)

        msg = await bot.send_photo(
            chat_id=chat_id,
            photo=photo,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )

        if old_ids:
            await _delete_hub_messages(bot, chat_id, old_ids)

        await _store_hub_id_in_db(chat_id, msg.message_id)

        return msg.message_id


async def send_hub_document(
    bot,
    chat_id: int,
    document: InputFile,
    caption: str,
    reply_markup: InlineKeyboardMarkup = None,
    parse_mode: str = "HTML",
) -> int:
    _maybe_cleanup_cache()

    lock = _get_hub_render_lock(chat_id)
    async with lock:
        old_ids = await _load_hub_ids_from_db(chat_id)

        msg = await bot.send_document(
            chat_id=chat_id,
            document=document,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )

        if old_ids:
            await _delete_hub_messages(bot, chat_id, old_ids)

        await _store_hub_id_in_db(chat_id, msg.message_id)

        return msg.message_id


async def append_hub_document(
    bot,
    chat_id: int,
    document: InputFile,
    caption: str,
    reply_markup: InlineKeyboardMarkup = None,
    parse_mode: str = "HTML",
) -> int:
    _maybe_cleanup_cache()

    lock = _get_hub_render_lock(chat_id)
    async with lock:
        msg = await bot.send_document(
            chat_id=chat_id,
            document=document,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )

        await _store_hub_id_in_db(chat_id, msg.message_id)

        return msg.message_id


async def append_hub_message(
    bot,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup = None,
    parse_mode: str = "HTML",
) -> int:
    _maybe_cleanup_cache()

    lock = _get_hub_render_lock(chat_id)
    async with lock:
        text_parts = split_text_by_lines(text, limit=4096)

        if not text_parts:
            text_parts = ["—"]

        sent_ids = []
        for i, part in enumerate(text_parts):
            is_last = (i == len(text_parts) - 1)
            kb = reply_markup if is_last else None
            
            try:
                msg = await bot.send_message(
                    chat_id=chat_id,
                    text=part,
                    reply_markup=kb,
                    parse_mode=parse_mode,
                )
            except TelegramBadRequest as e:
                err_str = str(e).lower()
                if "parse" in err_str or "entities" in err_str:
                    logger.warning(
                        "HTML parse failed in append_hub_message for chat %s, fallback to plain text: %s",
                        chat_id,
                        e,
                    )
                    msg = await bot.send_message(
                        chat_id=chat_id,
                        text=part,
                        reply_markup=kb,
                    )
                else:
                    raise
            
            sent_ids.append(msg.message_id)

        for mid in sent_ids:
            await _store_hub_id_in_db(chat_id, mid)

        return sent_ids[-1]