import asyncio
import html
import logging

from aiogram import Router, F
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.constants import TELEGRAM_MESSAGE_LIMIT
from bot.keyboards import get_back_button, get_broadcast_confirm_keyboard
from bot.keyboards.admin.broadcast import (
    get_broadcast_close_keyboard,
    get_broadcast_result_keyboard,
)
from bot.states import AdminStates
from database.connection import session_scope
from database.models import BroadcastProgress, User
from services.audit_service import AuditService
from utils.admin import is_admin
from utils.datetime_helpers import now_utc
from utils.rate_limiter import broadcast_send_limiter
from utils.telegram import render_hub, send_hub_document, send_hub_photo, safe

router = Router()
logger = logging.getLogger(__name__)

TELEGRAM_CAPTION_LIMIT = 1024

_broadcast_stop_events: dict[int, asyncio.Event] = {}
_broadcast_in_progress: set[int] = set()
_background_tasks: set[asyncio.Task] = set()


def _handle_task_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error("Background broadcast task failed: %s", e, exc_info=True)


def _start_background_task(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(lambda t: (_background_tasks.discard(t), _handle_task_result(t)))
    return task


def _get_stop_event(admin_id: int) -> asyncio.Event:
    if admin_id not in _broadcast_stop_events:
        _broadcast_stop_events[admin_id] = asyncio.Event()
    return _broadcast_stop_events[admin_id]


def _cleanup_stop_event(admin_id: int) -> None:
    _broadcast_stop_events.pop(admin_id, None)


from bot.keyboards.admin.broadcast import (
    get_broadcast_audience_keyboard,
    get_broadcast_close_keyboard,
    get_broadcast_launch_keyboard,
    get_broadcast_result_keyboard,
)

@router.callback_query(F.data == "admin_broadcast")
async def start_broadcast(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return
    await callback.answer(show_alert=False)
    await state.clear()

    from utils.formatters import format_admin_breadcrumbs
    header = format_admin_breadcrumbs("📢 Рассылка", "Шаг 1: Выбор аудитории")

    try:
        await callback.message.edit_text(
            f"{header}<b>Выберите аудиторию для рассылки:</b>\n\nКому отправить сообщение?",
            reply_markup=get_broadcast_audience_keyboard(),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"start_broadcast edit_text failed: {e}")


@router.callback_query(F.data.startswith("broadcast_aud:"))
async def select_broadcast_audience(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    audience = callback.data.split(":", 1)[1]
    if audience == "test":
        audience = f"test_{callback.from_user.id}"
    await callback.answer(show_alert=False)
    await state.update_data(target_audience=audience)
    await state.set_state(AdminStates.entering_broadcast_message)

    try:
        await callback.message.edit_text(
            texts.BROADCAST_PROMPT,
            reply_markup=get_back_button("admin_broadcast"),
        )
    except TelegramBadRequest as e:
        logger.debug(f"select_broadcast_audience edit_text failed: {e}")


@router.message(AdminStates.entering_broadcast_message)
async def process_broadcast_message(
    message: Message,
    state: FSMContext,
    session: AsyncSession = None,
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if message.text and message.text.startswith("/"):
        await state.clear()
        return

    broadcast_text = message.text or message.caption
    if not broadcast_text:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_TEXT_OR_MEDIA,
            get_back_button("admin_menu"),
        )
        return

    media_id = None
    content_type = message.content_type
    if message.photo:
        media_id = message.photo[-1].file_id
    elif message.document:
        media_id = message.document.file_id

    if not media_id and len(broadcast_text) > TELEGRAM_MESSAGE_LIMIT:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.UI_BOT_HANDLERS_ADMIN_BROADCAST_L116_1.format(
                value_0=TELEGRAM_MESSAGE_LIMIT
            ),
            get_back_button("admin_menu"),
        )
        return
    if media_id and len(broadcast_text) > TELEGRAM_CAPTION_LIMIT:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.UI_BOT_HANDLERS_ADMIN_BROADCAST_L125_1.format(
                value_0=TELEGRAM_CAPTION_LIMIT
            ),
            get_back_button("admin_menu"),
        )
        return

    data = await state.get_data()
    target_audience = data.get("target_audience", "all")

    # Send test preview directly to admin first so admin sees actual Telegram rendering
    try:
        await _dispatch_message(
            message.bot,
            message.from_user.id,
            broadcast_text,
            media_id,
            content_type,
        )
    except Exception as e:
        logger.warning(f"Failed to send test preview to admin: {e}")

    # Count recipients
    from datetime import timedelta
    count_stmt = select(func.count(User.id)).where(
        User.is_deleted.is_(False),
        User.is_bot_blocked.is_(False),
        User.is_banned.is_(False),
    )
    current_time = now_utc()
    if target_audience == "active":
        count_stmt = count_stmt.where(User.subscription_end > current_time)
    elif target_audience == "expiring_3d":
        count_stmt = count_stmt.where(
            User.subscription_end > current_time,
            User.subscription_end <= current_time + timedelta(days=3),
        )
    elif target_audience == "expired":
        count_stmt = count_stmt.where(
            User.subscription_end.is_not(None),
            User.subscription_end <= current_time,
        )
    elif target_audience == "never":
        count_stmt = count_stmt.where(User.subscription_end.is_(None))
    elif target_audience == "test" or target_audience.startswith("test_"):
        count_stmt = count_stmt.where(User.telegram_id == message.from_user.id)

    total_count = 1
    if session:
        try:
            result = await session.execute(count_stmt)
        except Exception as e:
            logger.warning(f"Failed to count recipients in session: {e}")

    label_map = {
        "all": "Все пользователи",
        "active": "Активные подписки",
        "expiring_3d": "Подписки истекают < 3 дней",
        "expired": "Истекшие подписки",
        "never": "Без подписок",
        "test": "Тестовая отправка админу",
    }
    aud_label = label_map.get(target_audience, target_audience)

    preview_summary = (
        f"✅ <b>Тестовое сообщение отправлено вам для проверки!</b>\n\n"
        f"👥 <b>Аудитория:</b> {aud_label}\n"
        f"📊 <b>Получателей:</b> {total_count} чел.\n\n"
        f"Ознакомьтесь с предпросмотром выше и подтвердите запуск рассылки."
    )

    try:
        await render_hub(
            message.bot,
            message.chat.id,
            preview_summary,
            get_broadcast_launch_keyboard(total_count),
            parse_mode="HTML",
        )
        await state.update_data(
            broadcast_text=broadcast_text,
            media_id=media_id,
            content_type=content_type,
            target_audience=target_audience,
            total_count=total_count,
        )
        await state.set_state(AdminStates.confirming_broadcast)
    except Exception as e:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_VALIDATION.format(error=safe(str(e))),
            get_back_button("admin_menu"),
        )


async def _send_with_html(bot, uid, text, media_id, content_type, kb):
    if content_type == "photo" and media_id:
        await bot.send_photo(uid, media_id, caption=text, parse_mode="HTML", reply_markup=kb)
    elif content_type == "document" and media_id:
        await bot.send_document(uid, media_id, caption=text, parse_mode="HTML", reply_markup=kb)
    elif content_type == "video" and media_id:
        await bot.send_video(uid, media_id, caption=text, parse_mode="HTML", reply_markup=kb)
    elif content_type == "voice" and media_id:
        await bot.send_voice(uid, media_id, caption=text, parse_mode="HTML", reply_markup=kb)
    elif content_type == "audio" and media_id:
        await bot.send_audio(uid, media_id, caption=text, parse_mode="HTML", reply_markup=kb)
    elif content_type == "video_note" and media_id:
        await bot.send_video_note(uid, media_id, reply_markup=kb)
    elif content_type == "animation" and media_id:
        await bot.send_animation(uid, media_id, caption=text, parse_mode="HTML", reply_markup=kb)
    elif content_type == "sticker" and media_id:
        await bot.send_sticker(uid, media_id, reply_markup=kb)
    else:
        await bot.send_message(uid, text, parse_mode="HTML", reply_markup=kb)


async def _send_plain(bot, uid, text, media_id, content_type, kb):
    if content_type == "photo" and media_id:
        await bot.send_photo(uid, media_id, caption=text, reply_markup=kb)
    elif content_type == "document" and media_id:
        await bot.send_document(uid, media_id, caption=text, reply_markup=kb)
    elif content_type == "video" and media_id:
        await bot.send_video(uid, media_id, caption=text, reply_markup=kb)
    elif content_type == "voice" and media_id:
        await bot.send_voice(uid, media_id, caption=text, reply_markup=kb)
    elif content_type == "audio" and media_id:
        await bot.send_audio(uid, media_id, caption=text, reply_markup=kb)
    elif content_type == "video_note" and media_id:
        await bot.send_video_note(uid, media_id, reply_markup=kb)
    elif content_type == "animation" and media_id:
        await bot.send_animation(uid, media_id, caption=text, reply_markup=kb)
    elif content_type == "sticker" and media_id:
        await bot.send_sticker(uid, media_id, reply_markup=kb)
    else:
        await bot.send_message(uid, text, reply_markup=kb)


async def _dispatch_message(bot, uid, text, media_id, content_type):
    kb = get_broadcast_close_keyboard()
    try:
        await _send_with_html(bot, uid, text, media_id, content_type, kb)
    except TelegramBadRequest as e:
        if "can't parse entities" in str(e).lower() or "parse" in str(e).lower():
            logger.warning(
                "HTML parse failed for user %s, falling back to plain text",
                uid,
            )
            await _send_plain(bot, uid, text, media_id, content_type, kb)
        else:
            raise


async def _get_next_batch(
    session: AsyncSession,
    audience: str,
    last_id: int,
    limit: int = 50,
):
    stmt = select(User.id, User.telegram_id).where(
        User.id > last_id,
        User.is_deleted.is_(False),
        User.is_bot_blocked.is_(False),
        User.is_banned.is_(False),
    )
    current_time = now_utc()
    if audience == "active":
        stmt = stmt.where(User.subscription_end > current_time)
    elif audience == "expiring_3d":
        from datetime import timedelta
        stmt = stmt.where(
            User.subscription_end > current_time,
            User.subscription_end <= current_time + timedelta(days=3),
        )
    elif audience == "expired":
        stmt = stmt.where(
            User.subscription_end.is_not(None),
            User.subscription_end <= current_time,
        )
    elif audience == "never":
        stmt = stmt.where(User.subscription_end.is_(None))
    elif audience.startswith("test_"):
        try:
            admin_tg_id = int(audience.split("_", 1)[1])
            stmt = stmt.where(User.telegram_id == admin_tg_id)
        except (IndexError, ValueError):
            pass
    stmt = stmt.order_by(User.id).limit(limit)
    result = await session.execute(stmt)
    return [(row[0], row[1]) for row in result.all()]



async def _send_broadcast_to_users_with_resume(
    bot,
    progress_id: int,
    admin_id: int,
):
    stop_event = None
    broadcast_text = None
    media_id = None
    content_type = None
    target_audience = None
    last_id = None
    final_progress = None
    should_finalize = False
    blocked_user_ids = []

    try:
        async with session_scope() as session:
            progress = await session.get(
                BroadcastProgress,
                progress_id,
            )
            if not progress:
                return
            if progress.status == "stopping":
                progress.status = "stopped"
                await session.commit()
                final_progress = progress
                return
            if progress.status != "in_progress":
                return

            should_finalize = True
            stop_event = _get_stop_event(admin_id)
            stop_event.clear()

            broadcast_text = progress.broadcast_text
            media_id = progress.media_id
            content_type = progress.content_type
            target_audience = progress.target_audience
            last_id = progress.last_processed_id

        logger.info(
            "Broadcast resume/start: admin=%s, progress_id=%s, starting from id %s",
            admin_id,
            progress_id,
            last_id,
        )

        local_success = 0
        local_fail = 0

        while True:
            async with session_scope() as session:
                progress = await session.get(BroadcastProgress, progress_id)
                if not progress or progress.status != "in_progress":
                    break

            if stop_event and stop_event.is_set():
                break

            async with session_scope() as session:
                batch = await _get_next_batch(
                    session,
                    target_audience,
                    last_id,
                )
                if not batch:
                    break

                for internal_id, uid in batch:
                    if stop_event and stop_event.is_set():
                        break
                    try:
                        await broadcast_send_limiter.acquire()
                        await _dispatch_message(
                            bot,
                            uid,
                            broadcast_text,
                            media_id,
                            content_type,
                        )
                        local_success += 1
                    except TelegramRetryAfter as e:
                        await asyncio.sleep(e.retry_after + 1)
                        try:
                            await broadcast_send_limiter.acquire()
                            await _dispatch_message(
                                bot,
                                uid,
                                broadcast_text,
                                media_id,
                                content_type,
                            )
                            local_success += 1
                        except Exception:
                            local_fail += 1
                    except TelegramForbiddenError:
                        blocked_user_ids.append(uid)
                        local_fail += 1
                    except Exception as e:
                        logger.error(
                            "Broadcast error for user %s: %s",
                            uid,
                            e,
                        )
                        local_fail += 1

                    last_id = internal_id

                progress = await session.get(
                    BroadcastProgress,
                    progress_id,
                )
                if progress:
                    progress.last_processed_id = last_id
                    progress.success_count += local_success
                    progress.fail_count += local_fail
                    await session.commit()

                local_success = 0
                local_fail = 0

        if should_finalize:
            async with session_scope() as session:
                progress = await session.get(
                    BroadcastProgress,
                    progress_id,
                )
                if progress:
                    if (
                        stop_event and stop_event.is_set()
                    ) or progress.status == "stopping":
                        progress.status = "stopped"
                    else:
                        progress.status = "completed"
                    await session.commit()
                    final_progress = progress

    except Exception as e:
        logger.exception(
            "Broadcast unexpected error (progress_id=%s): %s",
            progress_id,
            e,
        )
        try:
            async with session_scope() as session:
                progress = await session.get(
                    BroadcastProgress,
                    progress_id,
                )
                if progress and progress.status == "in_progress":
                    progress.status = "stopped"
                    await session.commit()
                    final_progress = progress
        except Exception as inner_e:
            logger.error(
                "Failed to mark broadcast %s as stopped: %s",
                progress_id,
                inner_e,
            )
        try:
            await bot.send_message(
                admin_id,
                texts.UI_BOT_HANDLERS_ADMIN_BROADCAST_L423_1.format(
                    value_0=html.escape(
                        type(e).__name__ + ": " + str(e)[:200]
                    )
                ),
                parse_mode="HTML",
            )
        except Exception as alert_err:
            logger.warning(
                "Failed to send crash alert to admin %s: %s",
                admin_id,
                alert_err,
            )
        raise

    finally:
        if stop_event:
            stop_event.clear()
        _broadcast_in_progress.discard(admin_id)
        _cleanup_stop_event(admin_id)

        if blocked_user_ids:
            logger.info(
                "Marking %d users as bot_blocked (bulk)",
                len(blocked_user_ids),
            )
            try:
                async with session_scope() as session:
                    await session.execute(
                        update(User)
                        .where(User.telegram_id.in_(blocked_user_ids))
                        .values(is_bot_blocked=True)
                    )
            except Exception as bulk_err:
                logger.warning(
                    "Failed to bulk mark users as bot_blocked: %s",
                    bulk_err,
                    exc_info=True,
                )

        if final_progress and admin_id:
            try:
                await bot.send_message(
                    admin_id,
                    texts.BROADCAST_RESULT.format(
                        success_count=final_progress.success_count,
                        fail_count=final_progress.fail_count,
                        label=final_progress.label,
                        total_count=final_progress.total_count,
                    ),
                    reply_markup=get_broadcast_result_keyboard(),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error(
                    "Failed to send broadcast result to admin %s: %s",
                    admin_id,
                    e,
                )

        try:
            async with session_scope() as session:
                await AuditService.log_action(
                    session,
                    admin_id,
                    "BROADCAST",
                    details=(
                        f"to {final_progress.label if final_progress else '?'}: "
                        f"{final_progress.success_count if final_progress else 0} success, "
                        f"{final_progress.fail_count if final_progress else 0} fail, "
                        f"status={final_progress.status if final_progress else 'unknown'}"
                    ),
                )
        except Exception as e:
            logger.error("Failed to log broadcast audit: %s", e)


async def resume_pending_broadcasts(bot):
    try:
        async with session_scope() as session:
            await session.execute(
                update(BroadcastProgress)
                .where(BroadcastProgress.status == "stopping")
                .values(status="stopped")
            )
            stmt = (
                select(BroadcastProgress)
                .where(BroadcastProgress.status == "in_progress")
                .order_by(
                    BroadcastProgress.created_at.desc(),
                    BroadcastProgress.id.desc(),
                )
            )
            result = await session.execute(stmt)
            pending = result.scalars().all()

            resume_items: list[tuple[int, int]] = []
            stop_ids: list[int] = []
            seen_admins: set[int] = set()

            for p in pending:
                if p.admin_id in _broadcast_in_progress:
                    stop_ids.append(p.id)
                    continue
                if p.admin_id in seen_admins:
                    stop_ids.append(p.id)
                    continue
                seen_admins.add(p.admin_id)
                resume_items.append((p.id, p.admin_id))

            if stop_ids:
                await session.execute(
                    update(BroadcastProgress)
                    .where(BroadcastProgress.id.in_(stop_ids))
                    .values(status="stopped")
                )
                logger.info(
                    "Marked %s old/duplicate broadcast(s) as stopped",
                    len(stop_ids),
                )

        for progress_id, admin_id in resume_items:
            logger.info(
                "Resuming interrupted broadcast ID %s for admin %s",
                progress_id,
                admin_id,
            )
            _broadcast_in_progress.add(admin_id)
            _start_background_task(
                _send_broadcast_to_users_with_resume(
                    bot,
                    progress_id,
                    admin_id,
                )
            )
    except Exception as e:
        logger.exception("Failed to resume broadcasts: %s", e)
        raise


async def _start_broadcast_process(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    audience: str,
):
    admin_id = callback.from_user.id

    if admin_id in _broadcast_in_progress:
        await callback.answer(
            texts.BROADCAST_ALREADY_RUNNING,
            show_alert=True,
        )
        return

    try:
        async with session_scope() as check_session:
            active_count = await check_session.scalar(
                select(func.count(BroadcastProgress.id)).where(
                    BroadcastProgress.admin_id == admin_id,
                    BroadcastProgress.status == "in_progress",
                )
            )
            if active_count and active_count > 0:
                await callback.answer(
                    texts.BROADCAST_ALREADY_RUNNING,
                    show_alert=True,
                )
                return
    except Exception as e:
        logger.warning(
            "Failed to check DB for active broadcasts: %s",
            e,
        )

    data = await state.get_data()
    broadcast_text = data.get("broadcast_text")
    if not broadcast_text:
        await callback.answer(
            texts.ERROR_TEXT_EMPTY,
            show_alert=True,
        )
        await state.clear()
        return

    media_id = data.get("media_id")
    content_type = data.get("content_type")

    count_stmt = select(func.count(User.id)).where(
        User.is_deleted.is_(False),
        User.is_bot_blocked.is_(False),
        User.is_banned.is_(False),
    )
    current_time = now_utc()
    if audience == "active":
        count_stmt = count_stmt.where(
            User.subscription_end > current_time,
        )
    elif audience == "expiring_3d":
        from datetime import timedelta
        count_stmt = count_stmt.where(
            User.subscription_end > current_time,
            User.subscription_end <= current_time + timedelta(days=3),
        )
    elif audience == "expired":
        count_stmt = count_stmt.where(
            User.subscription_end.is_not(None),
            User.subscription_end <= current_time,
        )
    elif audience == "never":
        count_stmt = count_stmt.where(
            User.subscription_end.is_(None),
        )
    elif audience.startswith("test_"):
        try:
            admin_tg_id = int(audience.split("_", 1)[1])
            count_stmt = count_stmt.where(User.telegram_id == admin_tg_id)
        except (IndexError, ValueError):
            pass

    result = await session.execute(count_stmt)
    total_count = result.scalar_one()

    if not total_count:
        await callback.answer(
            texts.BROADCAST_NO_RECIPIENTS,
            show_alert=True,
        )
        await state.clear()
        return

    label_map = {
        "all": texts.RUNTIME_BOT_HANDLERS_ADMIN_BROADCAST_L628_1,
        "active": texts.BROADCAST_ACTIVE_LABEL,
        "expiring_3d": "⏳ Истекают < 3 дней",
        "expired": "🔴 Истекшие подписки",
        "never": "🆕 Без подписок",
    }
    label = label_map.get(audience, "🧪 Тест мне (Админу)" if audience.startswith("test_") else audience)

    async with session_scope() as sess:
        progress = BroadcastProgress(
            admin_id=admin_id,
            total_count=total_count,
            target_audience=audience,
            broadcast_text=broadcast_text,
            media_id=media_id,
            content_type=content_type,
            label=label,
            status="in_progress",
        )
        sess.add(progress)
        await sess.commit()
        await sess.refresh(progress)
        progress_id = progress.id

    _broadcast_in_progress.add(admin_id)
    _start_background_task(
        _send_broadcast_to_users_with_resume(
            callback.bot,
            progress_id,
            admin_id,
        )
    )

    try:
        await callback.message.edit_text(
            texts.BROADCAST_STARTED.format(total_count=total_count),
            reply_markup=get_back_button("admin_menu"),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(
            "edit_text failed in _start_broadcast_process: %s",
            e,
        )

    await state.clear()


@router.callback_query(
    StateFilter(AdminStates.confirming_broadcast),
    F.data == "broadcast_confirm_launch",
)
async def broadcast_confirm_launch(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return
    data = await state.get_data()
    audience = data.get("target_audience", "all")
    await _start_broadcast_process(callback, state, session, audience)


@router.callback_query(
    StateFilter(AdminStates.confirming_broadcast),
    F.data == "broadcast_send_all",
)
async def broadcast_to_all(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return
    await _start_broadcast_process(callback, state, session, "all")


@router.callback_query(
    StateFilter(AdminStates.confirming_broadcast),
    F.data == "broadcast_send_active",
)
async def broadcast_to_active(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return
    await _start_broadcast_process(callback, state, session, "active")


@router.callback_query(
    StateFilter(AdminStates.confirming_broadcast),
    F.data == "broadcast_send_expired",
)
async def broadcast_to_expired(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return
    await _start_broadcast_process(callback, state, session, "expired")


@router.callback_query(
    StateFilter(AdminStates.confirming_broadcast),
    F.data == "broadcast_send_never",
)
async def broadcast_to_never(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return
    await _start_broadcast_process(callback, state, session, "never")


@router.callback_query(
    StateFilter(AdminStates.confirming_broadcast),
    F.data == "broadcast_send_test",
)
async def broadcast_to_test(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return
    audience = f"test_{callback.from_user.id}"
    await _start_broadcast_process(callback, state, session, audience)



@router.callback_query(F.data == "broadcast_stop")
async def stop_broadcast(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return
    admin_id = callback.from_user.id
    if admin_id not in _broadcast_in_progress:
        await callback.answer(
            texts.UI_BOT_HANDLERS_ADMIN_BROADCAST_L709_1,
            show_alert=True,
        )
        return

    try:
        async with session_scope() as session:
            await session.execute(
                update(BroadcastProgress)
                .where(
                    BroadcastProgress.admin_id == admin_id,
                    BroadcastProgress.status == "in_progress",
                )
                .values(status="stopping")
            )
            await session.commit()
    except Exception as e:
        logger.error(
            "Failed to persist broadcast stop request: %s",
            e,
            exc_info=True,
        )
        await callback.answer(
            texts.ERROR_TECHNICAL_ALERT,
            show_alert=True,
        )
        return

    # Set the in-memory event only after the durable DB state is committed.
    # On restart, resume_pending_broadcasts converts the persisted "stopping"
    # state to "stopped" and never resumes the broadcast.
    _get_stop_event(admin_id).set()
    await callback.answer(
        texts.BROADCAST_STOPPING,
        show_alert=True,
    )


@router.callback_query(F.data == "broadcast_dismiss")
async def dismiss_broadcast_result(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return
    await callback.answer(show_alert=False)
    try:
        await callback.message.delete()
    except TelegramBadRequest as e:
        logger.debug(f"dismiss_broadcast_result delete failed: {e}")


@router.callback_query(F.data == "dismiss_broadcast")
async def dismiss_broadcast_message(callback: CallbackQuery):
    await callback.answer(show_alert=False)
    try:
        await callback.message.delete()
    except TelegramBadRequest as e:
        logger.debug(f"dismiss_broadcast_message delete failed: {e}")
