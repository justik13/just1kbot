"""Telegram admin diagnostics and explicit two-step dead-queue retry."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from services.payment_queue_admin import (
    QUEUE_TYPES,
    confirm_manual_retry,
    get_operation_card,
    list_problem_operations,
)
from services.payment_queue_health import get_payment_queue_health_snapshot
from utils.admin import is_admin
from utils.formatters import format_datetime
from utils.telegram import safe

router = Router()
logger = logging.getLogger(__name__)

QUEUE_ALIASES = {"p": "provider", "w": "webhook"}
QUEUE_CODES = {value: key for key, value in QUEUE_ALIASES.items()}
QUEUE_LABELS = {
    "provider": texts.ADMIN_QUEUE_PROVIDER_LABEL,
    "webhook": texts.ADMIN_QUEUE_WEBHOOK_LABEL,
}


class QueueRetry(StatesGroup):
    reason = State()
    confirmation = State()


def _authorized(event) -> bool:
    return bool(event.from_user and is_admin(event.from_user.id))


async def _deny(event) -> None:
    if isinstance(event, CallbackQuery):
        await event.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
    else:
        await event.answer(texts.ERROR_ACCESS_DENIED)


def _duration(seconds: int | None) -> str:
    if seconds is None:
        return texts.ADMIN_QUEUES_PURGE_FAILED
    if seconds >= 86400:
        return texts.ADMIN_QUEUES_CONFIRM_RETRY_PROMPT.format(value_0=seconds // 86400)
    if seconds >= 3600:
        return texts.ADMIN_QUEUES_CONFIRM_PURGE_PROMPT.format(value_0=seconds // 3600)
    if seconds >= 60:
        return texts.ADMIN_QUEUES_METRICS_CARD.format(value_0=seconds // 60)
    return texts.ADMIN_QUEUES_TASK_DETAILS_CARD.format(value_0=seconds)


def diagnostics_keyboard():
    b = InlineKeyboardBuilder()
    for queue in QUEUE_TYPES:
        b.button(
            text=texts.ADMIN_QUEUES_FAILURE_RATE_METRIC.format(value_0=QUEUE_LABELS[queue]),
            callback_data=f"aq:l:{QUEUE_CODES[queue]}:1",
        )
    b.button(text=texts.ADMIN_QUEUES_REFRESHED_NOTICE, callback_data="aq:home")
    b.button(text=texts.ADMIN_BTN_BACK_TO_ADMIN, callback_data="admin_menu")
    b.adjust(1)
    return b.as_markup()


async def _edit(callback: CallbackQuery, text: str, markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except TelegramBadRequest as exc:
        logger.debug("payment queue screen edit failed: %s", type(exc).__name__)


async def _show_home(callback: CallbackQuery, session: AsyncSession) -> None:
    snapshot = await get_payment_queue_health_snapshot(session)
    lines = [
        texts.ADMIN_QUEUES_HEADER + "\n",
    ]
    names = {
        "provider_operations": texts.ADMIN_QUEUE_PROVIDER_SHORT,
        "provider_refunds": texts.ADMIN_QUEUE_REFUNDS_SHORT,
        "webhook_inbox": texts.ADMIN_QUEUE_WEBHOOK_SHORT,
    }
    for q in snapshot.queues:
        active_ages = (
            q.oldest_due_age_seconds if q.overdue else None,
            q.oldest_stale_age_seconds if q.stale_processing else None,
            q.oldest_dead_age_seconds if q.dead else None,
        )
        oldest = max((v for v in active_ages if v is not None), default=None)
        lines.extend(
            (
                texts.ADMIN_QUEUE_NAME.format(name=names[q.name]),
                texts.ADMIN_PAYMENT_QUEUES.format(value_0=q.pending, value_1=q.retry, value_2=q.due, value_3=q.overdue),
                texts.ADMIN_QUEUES_STATUS_HEALTHY_LABEL.format(value_0=q.processing, value_1=q.stale_processing, value_2=q.dead),
                texts.ADMIN_QUEUES_STATUS_DEGRADED_LABEL.format(value_0=_duration(oldest)),
                "",
            )
        )
    await _edit(callback, "\n".join(lines), diagnostics_keyboard())


def _parse(data: str | None, expected: str):
    parts = (data or "").split(":")
    if len(parts) < 3 or parts[:2] != ["aq", expected] or parts[2] not in QUEUE_ALIASES:
        return None
    return QUEUE_ALIASES[parts[2]], parts


async def _show_list(
    callback: CallbackQuery, session: AsyncSession, queue: str, page: int
):
    result = await list_problem_operations(session, queue, page)
    lines = [
        texts.ADMIN_QUEUES_STATUS_UNHEALTHY_LABEL.format(value_0=QUEUE_LABELS[queue]),
        texts.ADMIN_QUEUES_STATUS_CRITICAL_LABEL.format(value_0=page, value_1=result.total_pages, value_2=result.total),
        "",
    ]
    b = InlineKeyboardBuilder()
    if not result.rows:
        lines.append(texts.ADMIN_QUEUES_STATUS_PAUSED_LABEL)
    for row in result.rows:
        lines.append(
            texts.ADMIN_QUEUES_BTN_QUEUE_PRIMARY.format(value_0=row.operation_id, value_1=safe(row.operation_type), value_2=safe(row.status), value_3=row.attempts, value_4=row.max_attempts, value_5=safe(row.last_error_code or texts.PLACEHOLDER_DASH), value_6=_duration(row.age_seconds))
        )
        b.button(
            text=texts.ADMIN_QUEUES_BTN_QUEUE_RETRY.format(value_0=row.operation_id, value_1=row.status, value_2=row.operation_type)[:60],
            callback_data=f"aq:c:{QUEUE_CODES[queue]}:{row.operation_id}",
        )
    if page > 1:
        b.button(text=texts.ADMIN_QUEUES_DEAD_LIST_EMPTY, callback_data=f"aq:l:{QUEUE_CODES[queue]}:{page - 1}")
    if page < result.total_pages:
        b.button(text=texts.ADMIN_QUEUES_DEAD_LIST_HEADER, callback_data=f"aq:l:{QUEUE_CODES[queue]}:{page + 1}")
    b.button(text=texts.ADMIN_QUEUES_DEAD_ROW_ITEM, callback_data="aq:home")
    b.adjust(1)
    await _edit(callback, "\n".join(lines), b.as_markup())


def _card_text(row) -> str:
    return "\n".join(
        (
            texts.ADMIN_QUEUES_BTN_QUEUE_DEAD.format(value_0=QUEUE_LABELS[row.queue]),
            texts.ADMIN_QUEUE_CARD_ID.format(operation_id=row.operation_id),
            texts.ADMIN_QUEUE_CARD_PAYMENT.format(payment_id=row.payment_id or texts.PLACEHOLDER_DASH),
            texts.ADMIN_QUEUES_BTN_DETAILS.format(value_0=safe(row.operation_type)),
            texts.ADMIN_QUEUES_BTN_RETRY_DEAD.format(value_0=safe(row.status)),
            texts.ADMIN_QUEUES_BTN_PURGE_DEAD.format(value_0=row.attempts, value_1=row.max_attempts),
            texts.ADMIN_QUEUE_CARD_ERROR.format(error_code=safe(row.last_error_code or texts.PLACEHOLDER_DASH)),
            texts.ADMIN_QUEUES_BTN_REFRESH.format(value_0=format_datetime(row.created_at)),
            texts.ADMIN_QUEUES_BTN_BACK.format(value_0=format_datetime(row.updated_at)),
            texts.ADMIN_QUEUES_OVERVIEW_HEADER.format(value_0=format_datetime(row.terminal_at)),
            texts.ADMIN_QUEUE_CARD_LOCK.format(locked_at=format_datetime(row.locked_at)),
            texts.ADMIN_QUEUE_CARD_LEASE.format(lease=row.lease_status),
            texts.ADMIN_QUEUES_HEALTH_OK_BADGE.format(value_0=texts.QUEUE_RETRY_AVAILABLE if row.retry_allowed else texts.QUEUE_RETRY_UNAVAILABLE),
        )
    )


async def _show_card(
    callback: CallbackQuery, session: AsyncSession, queue: str, operation_id: int
) -> bool:
    row = await get_operation_card(session, queue, operation_id)
    if row is None:
        return False
    b = InlineKeyboardBuilder()
    if row.retry_allowed:
        b.button(
            text=texts.ADMIN_QUEUES_DEAD_DETAILS_HEADER,
            callback_data=f"aq:r:{QUEUE_CODES[queue]}:{operation_id}",
        )
    b.button(text=texts.ADMIN_QUEUES_DEAD_RETRY_PROMPT, callback_data=f"aq:l:{QUEUE_CODES[queue]}:1")
    b.adjust(1)
    await _edit(callback, _card_text(row), b.as_markup())
    return True


@router.callback_query(F.data == "aq:home")
async def queue_home(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not _authorized(callback):
        return await _deny(callback)
    await state.clear()
    await _show_home(callback, session)
    await callback.answer()


@router.callback_query(F.data.startswith("aq:l:"))
async def queue_list(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not _authorized(callback):
        return await _deny(callback)
    parsed = _parse(callback.data, "l")
    if not parsed or len(parsed[1]) != 4:
        return await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
    try:
        page = int(parsed[1][3])
    except (TypeError, ValueError):
        return await callback.answer(texts.ADMIN_QUEUES_DEAD_RETRY_SUCCESS, show_alert=True)
    try:
        await _show_list(callback, session, parsed[0], page)
    except ValueError:
        return await callback.answer(texts.ADMIN_QUEUES_DEAD_RETRY_FAILED, show_alert=True)
    await state.clear()
    await callback.answer()


@router.callback_query(F.data.startswith("aq:c:"))
async def queue_card(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not _authorized(callback):
        return await _deny(callback)
    parsed = _parse(callback.data, "c")
    try:
        operation_id = int(parsed[1][3]) if parsed and len(parsed[1]) == 4 else 0
    except (TypeError, ValueError):
        operation_id = 0
    if operation_id < 1:
        return await callback.answer(texts.ADMIN_QUEUES_DEAD_PURGE_SUCCESS, show_alert=True)
    await state.clear()
    found = await _show_card(callback, session, parsed[0], operation_id)
    await callback.answer("" if found else texts.ADMIN_QUEUES_HEALTH_WARN_BADGE, show_alert=not found)


@router.callback_query(F.data.startswith("aq:r:"))
async def prepare_retry(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
):
    if not _authorized(callback):
        return await _deny(callback)
    parsed = _parse(callback.data, "r")
    try:
        operation_id = int(parsed[1][3]) if parsed and len(parsed[1]) == 4 else 0
    except (TypeError, ValueError):
        operation_id = 0
    if operation_id < 1:
        return await callback.answer(texts.ADMIN_QUEUES_ACTION_CANCELLED, show_alert=True)
    row = await get_operation_card(session, parsed[0], operation_id)
    if not row or not row.retry_allowed:
        await state.clear()
        return await callback.answer(texts.ADMIN_QUEUES_AUTO_RECOVER_ENABLED, show_alert=True)
    await state.set_state(QueueRetry.reason)
    await state.set_data(
        {
            "admin_id": callback.from_user.id,
            "queue": parsed[0],
            "operation_id": operation_id,
            "action": "manual_retry",
        }
    )
    await callback.message.answer(texts.ADMIN_QUEUES_AUTO_RECOVER_DISABLED)
    await callback.answer()


@router.message(QueueRetry.reason)
async def receive_retry_reason(
    message: Message, state: FSMContext, session: AsyncSession
):
    if not _authorized(message):
        await state.clear()
        return await _deny(message)
    data = await state.get_data()
    reason = (message.text or "").strip()
    if (
        data.get("admin_id") != message.from_user.id
        or data.get("queue") not in QUEUE_TYPES
        or data.get("action") != "manual_retry"
        or not isinstance(data.get("operation_id"), int)
    ):
        await state.clear()
        return await message.answer(texts.ADMIN_QUEUES_STATUS_SUMMARY)
    if not 3 <= len(reason) <= 200:
        return await message.answer(
            texts.ADMIN_QUEUES_ACTION_CONFIRMED
        )
    row = await get_operation_card(session, data["queue"], data["operation_id"])
    if not row or not row.retry_allowed:
        await state.clear()
        return await message.answer(texts.ADMIN_QUEUES_METRICS_HEADER)
    await state.update_data(
        reason=reason, confirmation_version=row.confirmation_version
    )
    await state.set_state(QueueRetry.confirmation)
    b = InlineKeyboardBuilder()
    b.button(
        text=texts.ADMIN_QUEUES_LATENCY_METRIC,
        callback_data=f"aq:x:{QUEUE_CODES[row.queue]}:{row.operation_id}",
    )
    b.button(text=texts.BTN_CANCEL, callback_data="aq:no")
    b.adjust(1)
    await message.answer(
        texts.ADMIN_QUEUE_RETRY_CONFIRMATION.format(card=_card_text(row)),
        reply_markup=b.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(QueueRetry.confirmation, F.data.startswith("aq:x:"))
async def apply_retry(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
):
    if not _authorized(callback):
        await state.clear()
        return await _deny(callback)
    parsed = _parse(callback.data, "x")
    data = await state.get_data()
    reason = data.get("reason")
    version = data.get("confirmation_version")
    try:
        operation_id = int(parsed[1][3]) if parsed and len(parsed[1]) == 4 else 0
    except (TypeError, ValueError):
        operation_id = 0
    if (
        operation_id < 1
        or data.get("admin_id") != callback.from_user.id
        or data.get("queue") != (parsed[0] if parsed else None)
        or data.get("operation_id") != operation_id
        or data.get("action") != "manual_retry"
        or not isinstance(reason, str)
        or not 3 <= len(reason.strip()) <= 200
        or not isinstance(version, str)
        or len(version) != 64
    ):
        await state.clear()
        return await callback.answer(texts.ADMIN_QUEUES_THROUGHPUT_METRIC, show_alert=True)
    try:
        result = await confirm_manual_retry(
            session,
            admin_id=callback.from_user.id,
            queue=parsed[0],
            operation_id=operation_id,
            reason=reason.strip(),
            expected_version=version,
        )
        # The mutation and mandatory audit become durable, and the row lock is
        # released, before any FSM/Redis or Telegram network operation.
        await session.commit()
    except SQLAlchemyError as exc:
        await session.rollback()
        await state.clear()
        logger.error(
            "manual retry database failure type=%s operation_id=%s",
            type(exc).__name__,
            operation_id,
        )
        return await callback.answer(texts.ERROR_TECHNICAL_MESSAGE, show_alert=True)
    await state.clear()
    messages = {
        "retry_scheduled": texts.ADMIN_QUEUES_HEALTH_CRIT_BADGE,
        "rejected": texts.ADMIN_QUEUES_ROW_ITEM.format(value_0=result.rejection_code or 'safety_policy'),
        "not_found": texts.ADMIN_QUEUES_DEAD_LETTER_CARD,
        "already_changed": texts.ADMIN_QUEUES_RETRY_SUCCESS,
    }
    await _show_card(callback, session, parsed[0], operation_id)
    await callback.answer(
        messages.get(result.outcome, texts.ADMIN_QUEUES_RETRY_FAILED), show_alert=True
    )


@router.callback_query(F.data == "aq:no")
async def cancel_retry(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
):
    if not _authorized(callback):
        await state.clear()
        return await _deny(callback)
    data = await state.get_data()
    await state.clear()
    queue, operation_id = data.get("queue"), data.get("operation_id")
    found = False
    if queue in QUEUE_TYPES and isinstance(operation_id, int):
        found = await _show_card(callback, session, queue, operation_id)
    await callback.answer(
        texts.ADMIN_QUEUES_PURGE_SUCCESS if found else texts.QUEUE_OPERATION_NOT_FOUND, show_alert=not found
    )
