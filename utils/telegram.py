import asyncio
import html
import logging
import re
import time

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup, InputFile, LinkPreviewOptions
from cachetools import TTLCache
from sqlalchemy.ext.asyncio import AsyncSession

from config.constants import HUB_CACHE_MAX_SIZE, HUB_CACHE_TTL
from database.connection import session_scope
from database.repositories import hub_repo
from utils.text_limits import split_text_by_lines

logger = logging.getLogger(__name__)

# Premium Message Effect IDs (Telegram Bot API 7.4+)
EFFECT_CONFETTI = "5046509860389126442"  # 🎉 Confetti on topup, purchase, renewal, tariff change
EFFECT_LIKE = "5107584321108051014"      # 👍 Like on system confirmations (e.g. device renamed)
EFFECT_FIRE = "5104841245755180586"      # 🔥 Fire on referral reward
EFFECT_LIGHTNING = "5104841245755180585"  # ⚡ Lightning on VPN device creation

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
    msg_ids: list[int],
) -> tuple[list[int], list[int]]:
    deleted_ids: list[int] = []
    failed_ids: list[int] = []

    for msg_id in msg_ids:
        try:
            await _retry_flood(
                lambda mid=msg_id: bot.delete_message(chat_id=chat_id, message_id=mid)
            )
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


def safe(value: str | None) -> str:
    if value is None:
        return "—"
    return html.escape(str(value))


async def _retry_flood(op):
    """Retry an operation on Telegram flood-control (always safe to retry)."""
    try:
        return await op()
    except TelegramRetryAfter as e:
        await asyncio.sleep(float(e.retry_after) + 0.5)
        return await op()


async def _send_with_resilience(op, *, chat_id: int, context: str = "send"):
    """
    Execute a Telegram send operation with duplicate-safe retry semantics.

    - TelegramRetryAfter: the request was REJECTED (not delivered) and the
      server told us exactly when to retry -> wait and retry once more.
    - TelegramNetworkError / ambiguous timeout: delivery state is UNKNOWN
      (the request may have been processed before the connection broke).
      Automatic resending would risk duplicate messages, so we log a
      `hub_orphan_suspected` marker and re-raise without resending.
    """
    try:
        return await op()
    except TelegramRetryAfter as e:
        await asyncio.sleep(float(e.retry_after) + 0.5)
        return await op()
    except TelegramNetworkError as exc:
        logger.warning(
            "hub_orphan_suspected chat=%s context=%s: network error, not resending: %s",
            chat_id, context, exc,
        )
        raise
    except asyncio.TimeoutError:
        logger.warning(
            "hub_orphan_suspected chat=%s context=%s: ambiguous timeout, not resending",
            chat_id, context,
        )
        raise


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


async def _load_hub_ids_from_db(chat_id: int, session: AsyncSession | None = None) -> list[int]:
    """Load tracked hub ids for a chat.

    Fail-closed: a DB read failure PROPAGATES. Returning [] here would make
    render_hub treat an infrastructure outage as "no hub exists", causing
    duplicate hubs and deletion of live user messages based on fake state.
    """
    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        return list(cached["ids"])

    if session is not None:
        ids = await hub_repo.get_hub_message_ids(session, chat_id)
        effect_id = await hub_repo.get_latest_effect_message_id(session, chat_id)
        _hub_cache[chat_id] = {"ids": list(ids), "effect_msg_id": effect_id}
        return list(ids)

    async with session_scope() as sess:
        ids = await hub_repo.get_hub_message_ids(sess, chat_id)
        effect_id = await hub_repo.get_latest_effect_message_id(sess, chat_id)
    _hub_cache[chat_id] = {"ids": list(ids), "effect_msg_id": effect_id}
    return list(ids)


async def _store_hub_id_in_db(
    chat_id: int, message_id: int, *, is_effect: bool = False
) -> None:
    """
    Persist a delivered hub message id. Raises on DB failure so callers can
    abort the current render and clean up already-sent messages — otherwise a
    delivered-but-untracked message becomes an uncleanable orphan.
    """
    async with session_scope() as sess:
        await hub_repo.add_hub_message_id(
            sess, chat_id, message_id, is_effect=is_effect
        )
    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        if message_id not in cached["ids"]:
            cached["ids"].append(message_id)
    else:
        _hub_cache[chat_id] = {
            "ids": [message_id],
            "effect_msg_id": message_id if is_effect else None,
        }


async def _remove_hub_ids_from_db(chat_id: int, message_ids: list[int]) -> None:
    if not message_ids:
        return

    try:
        async with session_scope() as sess:
            await hub_repo.remove_hub_message_ids(sess, chat_id, message_ids)
    except Exception as e:
        # Telegram-side deletion may have succeeded while the durable cleanup
        # did not. Invalidate the cache so the next load re-reads DB truth —
        # otherwise the cache would hide stale rows from retry until TTL expiry.
        _hub_cache.pop(chat_id, None)
        logger.warning(
            "Failed to remove hub ids %s in DB for chat %s: %s",
            message_ids, chat_id, e,
        )
        raise

    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        old_set = set(message_ids)
        cached["ids"] = [mid for mid in cached["ids"] if mid not in old_set]
        if cached.get("effect_msg_id") in old_set:
            cached["effect_msg_id"] = None


async def get_hub_ids(chat_id: int, session: AsyncSession | None = None) -> list[int]:
    return await _load_hub_ids_from_db(chat_id, session=session)


async def _delete_hub_messages(bot, chat_id: int, msg_ids: list[int]) -> list[int]:
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


async def delete_hub_ids(bot, chat_id: int, msg_ids: list[int]) -> list[int]:
    if not msg_ids:
        return []

    lock = _get_hub_render_lock(chat_id)
    async with lock:
        return await _delete_hub_messages(bot, chat_id, msg_ids)


def _is_message_effect_error(exc: Exception) -> bool:
    """Return True if TelegramBadRequest was caused by an unsupported or invalid message_effect_id."""
    err = str(exc).lower()
    return any(
        marker in err
        for marker in (
            "message_effect_id_invalid",
            "effect_id_invalid",
            "effect_chat_invalid",
            "message_effect_invalid",
            "message effect invalid",
            "can't be sent with effect",
        )
    )


async def render_hub(
    bot,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "HTML",
    force_new: bool = False,
    trigger_message_id: int | None = None,
    disable_web_page_preview: bool = True,
    message_effect_id: str | None = None,
) -> int:
    """Render a single navigable hub, editing the target text message first."""
    _maybe_cleanup_cache()

    link_preview_opts = LinkPreviewOptions(is_disabled=True) if disable_web_page_preview else None

    lock = _get_hub_render_lock(chat_id)
    async with lock:
        old_ids = await _load_hub_ids_from_db(chat_id)
        text_parts = split_text_by_lines(text, limit=4096) or ["—"]
        cached_effect_id = _hub_cache.get(chat_id, {}).get("effect_msg_id")

        target_edit_id = None
        if not force_new and not message_effect_id and len(text_parts) == 1:
            if trigger_message_id and trigger_message_id in old_ids and trigger_message_id != cached_effect_id:
                target_edit_id = trigger_message_id
            elif old_ids and old_ids[-1] != cached_effect_id:
                target_edit_id = old_ids[-1]
            elif trigger_message_id and trigger_message_id != cached_effect_id:
                target_edit_id = trigger_message_id

        if trigger_message_id and trigger_message_id != target_edit_id and trigger_message_id not in old_ids:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=trigger_message_id)
            except Exception:
                pass

        if target_edit_id is not None:
            try:
                await _retry_flood(
                    lambda: bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=target_edit_id,
                        text=text_parts[0],
                        reply_markup=reply_markup,
                        parse_mode=parse_mode,
                        link_preview_options=link_preview_opts,
                    )
                )
                stale_ids = [mid for mid in old_ids if mid != target_edit_id]
                stale_failed: list[int] = []
                if stale_ids:
                    try:
                        stale_failed = await _delete_hub_messages(bot, chat_id, stale_ids)
                    except Exception:
                        # Durable cleanup of stale ids failed; an ADOPTED trigger
                        # was never persisted -> remove it before escaping.
                        if target_edit_id not in old_ids:
                            try:
                                await asyncio.shield(
                                    bot.delete_message(chat_id=chat_id, message_id=target_edit_id)
                                )
                            except Exception:
                                pass
                        raise
                try:
                    await _store_hub_id_in_db(chat_id, target_edit_id)
                except Exception:
                    if target_edit_id not in old_ids:
                        # Adopted an untracked trigger message as the hub; its id
                        # was never durably stored -> clean it up before escaping.
                        try:
                            await asyncio.shield(
                                bot.delete_message(chat_id=chat_id, message_id=target_edit_id)
                            )
                        except Exception:
                            pass
                    raise
                # Cache mirrors DB truth INCLUDING ids whose deletion failed,
                # otherwise the next render would not retry them until TTL expiry.
                _hub_cache[chat_id] = {
                    "ids": [target_edit_id] + stale_failed,
                    "effect_msg_id": None,
                }
                return target_edit_id
            except TelegramBadRequest as e:
                err_str = str(e).lower()
                if "message is not modified" in err_str:
                    stale_ids = [mid for mid in old_ids if mid != target_edit_id]
                    stale_failed_nm: list[int] = []
                    if stale_ids:
                        try:
                            stale_failed_nm = await _delete_hub_messages(bot, chat_id, stale_ids)
                        except Exception:
                            if target_edit_id not in old_ids:
                                try:
                                    await asyncio.shield(
                                        bot.delete_message(chat_id=chat_id, message_id=target_edit_id)
                                    )
                                except Exception:
                                    pass
                            raise
                    try:
                        await _store_hub_id_in_db(chat_id, target_edit_id)
                    except Exception:
                        if target_edit_id not in old_ids:
                            try:
                                await asyncio.shield(
                                    bot.delete_message(chat_id=chat_id, message_id=target_edit_id)
                                )
                            except Exception:
                                pass
                        raise
                    _hub_cache[chat_id] = {
                        "ids": [target_edit_id] + stale_failed_nm,
                        "effect_msg_id": None,
                    }
                    return target_edit_id

                logger.debug(
                    "edit_message_text failed for %s in chat %s: %s; falling back to send",
                    target_edit_id,
                    chat_id,
                    e,
                )

        if trigger_message_id and trigger_message_id not in old_ids:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=trigger_message_id)
            except Exception:
                pass

        sent_ids: list[int] = []
        try:
            for index, part in enumerate(text_parts):
                is_last = index == len(text_parts) - 1
                markup = reply_markup if is_last else None
                effect = message_effect_id if index == 0 else None
                try:
                    message = await _send_with_resilience(
                        lambda p=part, m=markup, eff=effect: bot.send_message(
                            chat_id=chat_id,
                            text=p,
                            reply_markup=m,
                            parse_mode=parse_mode,
                            link_preview_options=link_preview_opts,
                            message_effect_id=eff,
                        ),
                        chat_id=chat_id,
                        context="render_hub",
                    )
                except TelegramBadRequest as exc:
                    error = str(exc).lower()
                    if effect and _is_message_effect_error(exc):
                        try:
                            message = await _send_with_resilience(
                                lambda p=part, m=markup: bot.send_message(
                                    chat_id=chat_id,
                                    text=p,
                                    reply_markup=m,
                                    parse_mode=parse_mode,
                                    link_preview_options=link_preview_opts,
                                ),
                                chat_id=chat_id,
                                context="render_hub_no_effect",
                            )
                        except TelegramBadRequest as inner_exc:
                            inner_error = str(inner_exc).lower()
                            if "parse" in inner_error or "entities" in inner_error:
                                logger.warning(
                                    "HTML parse failed in render_hub retry for chat %s; using plain text",
                                    chat_id,
                                )
                                plain = html.unescape(re.sub(r"<[^>]+>", "", part))
                                message = await _send_with_resilience(
                                    lambda p=plain, m=markup: bot.send_message(
                                        chat_id=chat_id,
                                        text=p,
                                        reply_markup=m,
                                        link_preview_options=link_preview_opts,
                                        parse_mode=None,
                                    ),
                                    chat_id=chat_id,
                                    context="render_hub_plain_retry",
                                )
                            else:
                                raise
                    elif "parse" in error or "entities" in error:
                        logger.warning(
                            "HTML parse failed in render_hub for chat %s; using plain text",
                            chat_id,
                        )
                        plain = html.unescape(re.sub(r"<[^>]+>", "", part))
                        try:
                            message = await _send_with_resilience(
                                lambda p=plain, m=markup, eff=effect: bot.send_message(
                                    chat_id=chat_id,
                                    text=p,
                                    reply_markup=m,
                                    link_preview_options=link_preview_opts,
                                    parse_mode=None,
                                    message_effect_id=eff,
                                ),
                                chat_id=chat_id,
                                context="render_hub_plain",
                            )
                        except TelegramBadRequest as effect_exc:
                            if effect and _is_message_effect_error(effect_exc):
                                message = await _send_with_resilience(
                                    lambda p=plain, m=markup: bot.send_message(
                                        chat_id=chat_id,
                                        text=p,
                                        reply_markup=m,
                                        link_preview_options=link_preview_opts,
                                        parse_mode=None,
                                    ),
                                    chat_id=chat_id,
                                    context="render_hub_plain_no_effect",
                                )
                            else:
                                raise
                    else:
                        raise
                sent_ids.append(message.message_id)
                # Append BEFORE storing: if the store raises, the id is already
                # in sent_ids so the BaseException cleanup deletes the delivered
                # message instead of orphaning it. The first part carries the
                # durable effect marker (if any).
                await _store_hub_id_in_db(
                    chat_id,
                    message.message_id,
                    is_effect=bool(effect),
                )
        except BaseException:
            if sent_ids:
                try:
                    await asyncio.shield(_delete_hub_messages(bot, chat_id, sent_ids))
                except Exception:
                    pass
            raise

        old_failed: list[int] = []
        if old_ids:
            old_failed = await _delete_hub_messages(bot, chat_id, old_ids)
        _hub_cache[chat_id] = {
            "ids": sent_ids + old_failed,
            "effect_msg_id": sent_ids[0] if message_effect_id and sent_ids else None,
        }
        return sent_ids[-1]



async def send_hub_photo(
    bot,
    chat_id: int,
    photo: InputFile,
    caption: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "HTML",
) -> int:
    _maybe_cleanup_cache()

    lock = _get_hub_render_lock(chat_id)
    async with lock:
        old_ids = await _load_hub_ids_from_db(chat_id)

        if caption:
            caption = caption[:1024]

        try:
            msg = await _send_with_resilience(
                lambda: bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=caption,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                ),
                chat_id=chat_id,
                context="send_hub_photo",
            )
        except TelegramBadRequest as exc:
            error = str(exc).lower()
            if "parse" in error or "entities" in error:
                logger.warning(
                    "HTML parse failed in send_hub_photo for chat %s; using plain text",
                    chat_id,
                )
                plain = html.unescape(re.sub(r"<[^>]+>", "", caption or ""))[:1024]
                msg = await _send_with_resilience(
                    lambda: bot.send_photo(
                        chat_id=chat_id,
                        photo=photo,
                        caption=plain,
                        reply_markup=reply_markup,
                        parse_mode=None,
                    ),
                    chat_id=chat_id,
                    context="send_hub_photo_plain",
                )
            else:
                raise

        try:
            await _store_hub_id_in_db(chat_id, msg.message_id)
        except Exception:
            # Store BEFORE deleting the old hub: on failure we remove only the
            # NEW message and leave the OLD hub fully intact (no user-visible
            # gap). Deleting old first would wipe the chat if persistence fails.
            try:
                await asyncio.shield(
                    bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
                )
            except Exception:
                pass
            raise

        if old_ids:
            await _delete_hub_messages(bot, chat_id, old_ids)

        return msg.message_id


async def send_hub_document(
    bot,
    chat_id: int,
    document: InputFile,
    caption: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "HTML",
) -> int:
    _maybe_cleanup_cache()

    lock = _get_hub_render_lock(chat_id)
    async with lock:
        old_ids = await _load_hub_ids_from_db(chat_id)

        if caption:
            caption = caption[:1024]

        try:
            msg = await _send_with_resilience(
                lambda: bot.send_document(
                    chat_id=chat_id,
                    document=document,
                    caption=caption,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                ),
                chat_id=chat_id,
                context="send_hub_document",
            )
        except TelegramBadRequest as exc:
            error = str(exc).lower()
            if "parse" in error or "entities" in error:
                logger.warning(
                    "HTML parse failed in send_hub_document for chat %s; using plain text",
                    chat_id,
                )
                plain = html.unescape(re.sub(r"<[^>]+>", "", caption or ""))[:1024]
                msg = await _send_with_resilience(
                    lambda: bot.send_document(
                        chat_id=chat_id,
                        document=document,
                        caption=plain,
                        reply_markup=reply_markup,
                        parse_mode=None,
                    ),
                    chat_id=chat_id,
                    context="send_hub_document_plain",
                )
            else:
                raise

        try:
            await _store_hub_id_in_db(chat_id, msg.message_id)
        except Exception:
            # Store BEFORE deleting the old hub (see send_hub_photo): on
            # persistence failure we remove only the NEW document and keep the
            # old hub intact, so the user is never left without a hub screen.
            try:
                await asyncio.shield(
                    bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
                )
            except Exception:
                pass
            raise

        if old_ids:
            await _delete_hub_messages(bot, chat_id, old_ids)

        return msg.message_id


async def _append_hub_document_unlocked(
    bot,
    chat_id: int,
    document: InputFile,
    caption: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "HTML",
) -> int:
    if caption:
        caption = caption[:1024]

    try:
        msg = await _send_with_resilience(
            lambda: bot.send_document(
                chat_id=chat_id,
                document=document,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            ),
            chat_id=chat_id,
            context="append_hub_document",
        )
    except TelegramBadRequest as exc:
        error = str(exc).lower()
        if "parse" in error or "entities" in error:
            logger.warning(
                "HTML parse failed in _append_hub_document_unlocked for chat %s; using plain text",
                chat_id,
            )
            plain = html.unescape(re.sub(r"<[^>]+>", "", caption or ""))[:1024]
            msg = await _send_with_resilience(
                lambda: bot.send_document(
                    chat_id=chat_id,
                    document=document,
                    caption=plain,
                    reply_markup=reply_markup,
                    parse_mode=None,
                ),
                chat_id=chat_id,
                context="append_hub_document_plain",
            )
        else:
            raise

    try:
        await _store_hub_id_in_db(chat_id, msg.message_id)
    except Exception:
        try:
            await asyncio.shield(
                bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
            )
        except Exception:
            pass
        raise

    return msg.message_id


async def append_hub_document(
    bot,
    chat_id: int,
    document: InputFile,
    caption: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "HTML",
) -> int:
    _maybe_cleanup_cache()
    lock = _get_hub_render_lock(chat_id)
    async with lock:
        return await _append_hub_document_unlocked(
            bot,
            chat_id=chat_id,
            document=document,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )


async def _append_hub_message_unlocked(
    bot,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "HTML",
) -> int:
    text_parts = split_text_by_lines(text, limit=4096)

    if not text_parts:
        text_parts = ["—"]

    sent_ids = []
    try:
        for i, part in enumerate(text_parts):
            is_last = (i == len(text_parts) - 1)
            kb = reply_markup if is_last else None

            try:
                msg = await _send_with_resilience(
                    lambda p=part, k=kb: bot.send_message(
                        chat_id=chat_id,
                        text=p,
                        reply_markup=k,
                        parse_mode=parse_mode,
                    ),
                    chat_id=chat_id,
                    context="append_hub_message",
                )
            except TelegramBadRequest as e:
                err_str = str(e).lower()
                if "parse" in err_str or "entities" in err_str:
                    logger.warning(
                        "HTML parse failed in append_hub_message for chat %s, fallback to plain text: %s",
                        chat_id,
                        e,
                    )
                    plain = html.unescape(re.sub(r"<[^>]+>", "", part))
                    msg = await _send_with_resilience(
                        lambda p=plain, k=kb: bot.send_message(
                            chat_id=chat_id,
                            text=p,
                            reply_markup=k,
                            parse_mode=None,
                        ),
                        chat_id=chat_id,
                        context="append_hub_message_plain",
                    )
                else:
                    raise

            sent_ids.append(msg.message_id)
            # Same ordering contract as render_hub: id must be in sent_ids
            # before the store attempt, so failure cleanup covers it.
            await _store_hub_id_in_db(chat_id, msg.message_id)
    except BaseException:
        if sent_ids:
            try:
                await asyncio.shield(_delete_hub_messages(bot, chat_id, sent_ids))
            except Exception:
                pass
        raise

    return sent_ids[-1]


async def append_hub_message(
    bot,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "HTML",
) -> int:
    _maybe_cleanup_cache()
    lock = _get_hub_render_lock(chat_id)
    async with lock:
        return await _append_hub_message_unlocked(
            bot,
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
