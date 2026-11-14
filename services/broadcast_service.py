"""Durable Telegram broadcast service for Mini App admin controls."""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards.notifications import get_dismiss_notification_keyboard
from config.enums import AdminAuditAction
from database.connection import session_scope
from database.models import BroadcastProgress, User
from database.repositories.users_repo import (
    get_effective_active_condition,
    get_effective_expired_condition,
    get_effective_expiring_3d_condition,
    get_effective_inactive_condition,
    get_effective_never_condition,
    get_effective_wi_active_condition,
)
from services.audit_service import AuditService
from utils.datetime_helpers import now_utc
from utils.rate_limiter import broadcast_send_limiter
from utils.telegram import safe_send_message

logger = logging.getLogger(__name__)

BROADCAST_BATCH_SIZE = 25
MAX_BROADCAST_TEXT_LEN = 4096
MAX_BROADCAST_CAPTION_LEN = 1024

AUDIENCE_LABELS: dict[str, str] = {
    "all": "Все пользователи",
    "active": "Активная подписка",
    "active_standard": "Активная Стандартная",
    "active_wi": "Активный Белый Интернет",
    "expiring_3d": "Истекает ≤ 3 дней",
    "expired": "Подписка истекла",
    "no_sub": "Без активной подписки",
    "never": "Никогда не покупали",
    "test": "Тест (только мне)",
}

_active_broadcast_tasks: dict[int, asyncio.Task] = {}


def _is_parse_error(exc: Exception) -> bool:
    err = str(exc).lower()
    return "can't parse" in err or "parse entities" in err or "parse error" in err


async def resolve_audience_label(session: AsyncSession, target_audience: str) -> str:
    """Return human-readable label for a broadcast audience code."""
    if target_audience in AUDIENCE_LABELS:
        return AUDIENCE_LABELS[target_audience]
    return target_audience[:50]


async def apply_audience_filter(
    session: AsyncSession,
    stmt: Any,
    target_audience: str,
    *,
    admin_id: int | None = None,
    include_bot_blocked: bool = False,
    now: datetime | None = None,
) -> Any:
    """Apply canonical non-deleted and audience filters to a User query."""
    if now is None:
        now = now_utc()

    stmt = stmt.where(User.is_deleted.is_(False))
    if not include_bot_blocked:
        stmt = stmt.where(User.is_bot_blocked.is_(False))

    if target_audience == "all":
        return stmt
    if target_audience == "active":
        return stmt.where(get_effective_active_condition(now))
    if target_audience == "active_standard":
        return stmt.where(User.subscription_end > now)
    if target_audience == "active_wi":
        return stmt.where(get_effective_wi_active_condition(now))
    if target_audience == "expiring_3d":
        return stmt.where(get_effective_expiring_3d_condition(now))
    if target_audience == "expired":
        return stmt.where(get_effective_expired_condition(now))
    if target_audience == "no_sub":
        return stmt.where(get_effective_inactive_condition(now))
    if target_audience == "never":
        return stmt.where(get_effective_never_condition())
    if target_audience == "test":
        if admin_id is None:
            return stmt.where(User.id < 0)
        return stmt.where(User.telegram_id == admin_id)

    raise ValueError(f"Неизвестная аудитория рассылки: {target_audience}")


async def count_broadcast_audience(
    session: AsyncSession,
    target_audience: str,
    *,
    admin_id: int | None = None,
    include_bot_blocked: bool = False,
) -> int:
    stmt = select(func.count(User.id))
    stmt = await apply_audience_filter(
        session,
        stmt,
        target_audience,
        admin_id=admin_id,
        include_bot_blocked=include_bot_blocked,
    )
    return int((await session.scalar(stmt)) or 0)


async def get_audience_targets(
    session: AsyncSession,
    target_audience: str,
    *,
    admin_id: int | None = None,
    include_bot_blocked: bool = False,
    last_user_id: int = 0,
    limit: int | None = None,
) -> list[tuple[int, int, bool]]:
    """Return list of (user.id, user.telegram_id, user.is_bot_blocked) ordered by User.id ASC."""
    stmt = select(User.id, User.telegram_id, User.is_bot_blocked).where(User.id > last_user_id)
    stmt = await apply_audience_filter(
        session,
        stmt,
        target_audience,
        admin_id=admin_id,
        include_bot_blocked=include_bot_blocked,
    )
    stmt = stmt.order_by(User.id.asc())
    if limit is not None:
        stmt = stmt.limit(limit)
    rows = (await session.execute(stmt)).all()
    return [(int(r[0]), int(r[1]), bool(r[2])) for r in rows]


def serialize_broadcast(bp: BroadcastProgress) -> dict[str, Any]:
    processed = (bp.success_count or 0) + (bp.fail_count or 0)
    total = bp.total_count or 0
    progress_pct = round(min(100.0, (processed / total) * 100), 1) if total > 0 else 100.0
    return {
        "id": bp.id,
        "admin_id": bp.admin_id,
        "target_audience": bp.target_audience,
        "label": bp.label,
        "content_type": bp.content_type,
        "has_media": bool(bp.media_id),
        "text_preview": (bp.broadcast_text or "")[:180],
        "status": bp.status,
        "total_count": total,
        "success_count": bp.success_count or 0,
        "fail_count": bp.fail_count or 0,
        "processed_count": processed,
        "progress_pct": progress_pct,
        "created_at": bp.created_at.isoformat() if bp.created_at else None,
        "updated_at": bp.updated_at.isoformat() if bp.updated_at else None,
    }


async def _send_photo_with_resilience(
    bot: Bot,
    chat_id: int,
    photo: str,
    caption: str,
    reply_markup: Any,
) -> None:
    """Send photo broadcast message handling FloodWait and HTML parse fallback."""
    async def _send(p_mode: str | None) -> None:
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=caption[:MAX_BROADCAST_CAPTION_LEN],
                parse_mode=p_mode,
                reply_markup=reply_markup,
            )
        except TelegramRetryAfter as exc:
            await asyncio.sleep(float(exc.retry_after) + 0.5)
            await bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=caption[:MAX_BROADCAST_CAPTION_LEN],
                parse_mode=p_mode,
                reply_markup=reply_markup,
            )

    try:
        await _send("HTML")
    except TelegramBadRequest as exc:
        if _is_parse_error(exc):
            await _send(None)
        else:
            raise


async def _deliver_to_user(
    bot: Bot,
    chat_id: int,
    text: str,
    media_id: str | None,
    content_type: str,
) -> bool:
    """Deliver broadcast item to one user. Returns True on success, raises TelegramForbiddenError if blocked."""
    kb = get_dismiss_notification_keyboard()
    if media_id and content_type == "photo":
        if len(text) <= MAX_BROADCAST_CAPTION_LEN:
            await _send_photo_with_resilience(bot, chat_id, media_id, text, kb)
            return True
        await _send_photo_with_resilience(bot, chat_id, media_id, "", None)
    msg_id = await safe_send_message(
        bot,
        chat_id,
        text,
        reply_markup=kb,
        parse_mode="HTML",
        raise_forbidden=True,
    )
    return msg_id is not None


async def _run_broadcast_worker(bot: Bot, broadcast_id: int) -> None:
    logger.info("Broadcast worker started for id=%s", broadcast_id)
    try:
        while True:
            async with session_scope() as session:
                bp = await session.get(BroadcastProgress, broadcast_id)
                if bp is None or bp.status != "in_progress":
                    return
                last_id = int(bp.last_processed_id or 0)
                target_audience = bp.target_audience
                admin_id = int(bp.admin_id)
                text = bp.broadcast_text
                media_id = bp.media_id
                content_type = bp.content_type
                targets = await get_audience_targets(
                    session,
                    target_audience,
                    admin_id=admin_id,
                    include_bot_blocked=False,
                    last_user_id=last_id,
                    limit=BROADCAST_BATCH_SIZE,
                )

            if not targets:
                async with session_scope() as session:
                    bp = await session.get(BroadcastProgress, broadcast_id)
                    if bp is not None and bp.status == "in_progress":
                        bp.status = "completed"
                        bp.updated_at = now_utc()
                        await AuditService.log_action(
                            session,
                            admin_id=bp.admin_id,
                            action=AdminAuditAction.BROADCAST_COMPLETED,
                            target_type="broadcast",
                            target_id=bp.id,
                            details={
                                "audience": bp.target_audience,
                                "total": bp.total_count,
                                "success": bp.success_count,
                                "fail": bp.fail_count,
                            },
                        )
                return

            batch_success = 0
            batch_fail = 0
            blocked_user_ids: list[int] = []
            max_uid = last_id

            for uid, tg_id, _is_blocked in targets:
                max_uid = uid
                try:
                    await broadcast_send_limiter.acquire()
                    await _deliver_to_user(bot, tg_id, text, media_id, content_type)
                    batch_success += 1
                except TelegramForbiddenError:
                    batch_fail += 1
                    blocked_user_ids.append(uid)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    batch_fail += 1
                    logger.warning(
                        "Broadcast %s delivery failed for user %s (%s): %s",
                        broadcast_id,
                        uid,
                        tg_id,
                        exc,
                    )

            async with session_scope() as session:
                if blocked_user_ids:
                    await session.execute(
                        update(User)
                        .where(User.id.in_(blocked_user_ids))
                        .values(is_bot_blocked=True)
                    )
                bp = await session.get(BroadcastProgress, broadcast_id)
                if bp is None:
                    return
                bp.last_processed_id = max_uid
                bp.success_count = (bp.success_count or 0) + batch_success
                bp.fail_count = (bp.fail_count or 0) + batch_fail
                bp.updated_at = now_utc()
                if bp.status != "in_progress":
                    return

    except asyncio.CancelledError:
        logger.info("Broadcast worker cancelled for id=%s", broadcast_id)
        raise
    except Exception as exc:
        logger.error("Broadcast worker crashed for id=%s: %s", broadcast_id, exc, exc_info=True)
        try:
            async with session_scope() as session:
                bp = await session.get(BroadcastProgress, broadcast_id)
                if bp is not None and bp.status == "in_progress":
                    bp.status = "failed"
                    bp.updated_at = now_utc()
        except Exception as inner_exc:
            logger.error("Failed to mark broadcast %s as failed: %s", broadcast_id, inner_exc)
    finally:
        current = asyncio.current_task()
        if _active_broadcast_tasks.get(broadcast_id) is current:
            _active_broadcast_tasks.pop(broadcast_id, None)


def _spawn_broadcast_task(bot: Bot, broadcast_id: int) -> None:
    existing = _active_broadcast_tasks.get(broadcast_id)
    if existing and not existing.done():
        return
    task = asyncio.create_task(
        _run_broadcast_worker(bot, broadcast_id),
        name=f"broadcast:{broadcast_id}",
    )
    _active_broadcast_tasks[broadcast_id] = task


async def create_and_start_broadcast(
    bot: Bot | None,
    *,
    admin_id: int,
    target_audience: str,
    text: str,
    media_url: str | None = None,
) -> dict[str, Any]:
    clean_text = (text or "").strip()
    if not clean_text:
        raise ValueError("Текст рассылки не может быть пустым")
    if len(clean_text) > MAX_BROADCAST_TEXT_LEN:
        raise ValueError(f"Текст рассылки превышает {MAX_BROADCAST_TEXT_LEN} символов")

    clean_media = (media_url or "").strip() or None
    content_type = "photo" if clean_media else "text"

    async with session_scope() as session:
        total_count = await count_broadcast_audience(
            session,
            target_audience,
            admin_id=admin_id,
            include_bot_blocked=False,
        )
        if total_count <= 0:
            raise ValueError("Нет доступных получателей для выбранной аудитории")

        label = await resolve_audience_label(session, target_audience)
        bp = BroadcastProgress(
            admin_id=admin_id,
            total_count=total_count,
            success_count=0,
            fail_count=0,
            last_processed_id=0,
            target_audience=target_audience,
            broadcast_text=clean_text,
            media_id=clean_media,
            content_type=content_type,
            label=label[:50],
            status="in_progress",
        )
        session.add(bp)
        await session.flush()

        await AuditService.log_action(
            session,
            admin_id=admin_id,
            action=AdminAuditAction.BROADCAST,
            target_type="broadcast",
            target_id=bp.id,
            details={
                "audience": target_audience,
                "label": label,
                "total": total_count,
                "content_type": content_type,
            },
        )
        payload = serialize_broadcast(bp)
        broadcast_id = bp.id

    if bot is not None:
        _spawn_broadcast_task(bot, broadcast_id)
    return payload


async def cancel_broadcast(
    session: AsyncSession,
    broadcast_id: int,
    admin_id: int,
) -> dict[str, Any] | None:
    bp = await session.get(BroadcastProgress, broadcast_id)
    if bp is None:
        return None
    if bp.status == "in_progress":
        bp.status = "cancelled"
        bp.updated_at = now_utc()
        await session.flush()
        await AuditService.log_action(
            session,
            admin_id=admin_id,
            action=AdminAuditAction.BROADCAST_COMPLETED,
            target_type="broadcast",
            target_id=bp.id,
            details={
                "cancelled": True,
                "success": bp.success_count,
                "fail": bp.fail_count,
            },
        )
    task = _active_broadcast_tasks.pop(broadcast_id, None)
    if task and not task.done():
        task.cancel()
    return serialize_broadcast(bp)


async def list_broadcasts(session: AsyncSession, limit: int = 20) -> list[dict[str, Any]]:
    stmt = (
        select(BroadcastProgress)
        .order_by(desc(BroadcastProgress.id))
        .limit(max(1, min(limit, 50)))
    )
    rows = (await session.scalars(stmt)).all()
    return [serialize_broadcast(row) for row in rows]


async def resume_pending_broadcasts(bot: Bot) -> None:
    """Resume any broadcasts left in 'in_progress' state across bot restarts."""
    try:
        async with session_scope() as session:
            ids = list(
                (
                    await session.scalars(
                        select(BroadcastProgress.id)
                        .where(BroadcastProgress.status == "in_progress")
                        .order_by(BroadcastProgress.created_at.asc())
                    )
                ).all()
            )
        for bid in ids:
            _spawn_broadcast_task(bot, int(bid))
        if ids:
            logger.info("Resumed %d pending broadcast(s) on startup", len(ids))
    except Exception as exc:
        logger.error("Failed to resume pending broadcasts: %s", exc, exc_info=True)


async def stop_broadcast_tasks() -> None:
    tasks = list(_active_broadcast_tasks.values())
    _active_broadcast_tasks.clear()
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
