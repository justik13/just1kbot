"""Manual Telegram admin workflow for balance-topup disputes."""

from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from database.dispute_models import PaymentDispute
from database.models import AccountBalanceReservation, Payment
from services.payment_disputes import (
    PaymentDisputeError,
    mark_payment_dispute_manual_review,
    open_payment_dispute,
    resolve_payment_dispute,
)
from utils.admin import is_admin
from utils.telegram import safe

router = Router()


class DisputeEntry(StatesGroup):
    details = State()


STATUS_LABELS = {
    "open": texts.ADMIN_DISPUTE_CARD,
    "manual_review": texts.ADMIN_DISPUTE_ROW_ITEM,
    "won_by_merchant": texts.ADMIN_DISPUTE_LIST_EMPTY,
    "lost_by_merchant": texts.ADMIN_DISPUTE_LIST_HEADER,
}


def _error_text(code: str) -> str:
    return {
        "provider_payment_id_required": texts.ADMIN_DISPUTE_ACCEPT_SUCCESS,
        "provider_case_id_required": texts.ADMIN_DISPUTE_REJECT_SUCCESS,
        "dispute_amount_invalid": texts.ADMIN_DISPUTE_SET_REVIEW_SUCCESS,
        "disputed_at_timezone_required": texts.ADMIN_DISPUTE_ACTION_FAILED,
        "payment_not_found": texts.ADMIN_DISPUTE_PROMPT_CASE_ID,
        "dispute_requires_balance_topup": (
            texts.ADMIN_DISPUTE_PROMPT_PAYMENT_ID
        ),
        "payment_not_settled": texts.ADMIN_DISPUTE_PROMPT_AMOUNT,
        "payment_not_credited": texts.ADMIN_DISPUTE_PROMPT_REASON,
        "provider_case_id_conflict": texts.ADMIN_DISPUTE_INVALID_AMOUNT,
        "payment_has_active_dispute": texts.ADMIN_DISPUTE_CREATE_CONFIRM,
        "refund_in_progress": texts.ADMIN_DISPUTE_CREATE_SUCCESS,
        "dispute_exceeds_payment_exposure": (
            texts.ADMIN_DISPUTE_NOT_FOUND_OR_RESOLVED
        ),
        "dispute_not_found": texts.ADMIN_DISPUTE_RESOLVED_ALREADY,
        "dispute_already_resolved": texts.ADMIN_DISPUTE_GENERAL_ERROR,
    }.get(code, texts.ADMIN_DISPUTE_BTN_REGISTER)


def _list_keyboard() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.ADMIN_DISPUTE_CONFIRM_CASE, callback_data="admin_dispute_new")
    builder.button(text=texts.ADMIN_DISPUTE_CONFIRM_STATUS, callback_data="admin_disputes")
    builder.button(text=texts.ADMIN_DISPUTE_CONFIRM_ACTION, callback_data="admin_menu")
    builder.adjust(1)
    return builder


def _card_keyboard(dispute: PaymentDispute) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    if dispute.status in {"open", "manual_review"}:
        builder.button(
            text=texts.ADMIN_DISPUTE_CONFIRM_PROMPT,
            callback_data=f"admin_dispute_resolve:won_by_merchant:{dispute.id}",
        )
        builder.button(
            text=texts.ADMIN_DISPUTE_EXECUTION_NOTICE,
            callback_data=f"admin_dispute_resolve:lost_by_merchant:{dispute.id}",
        )
        if dispute.status == "open":
            builder.button(
                text=texts.ADMIN_DISPUTE_ACTION_SUCCESS,
                callback_data=f"admin_dispute_review:{dispute.id}",
            )
    builder.button(text=texts.ADMIN_BTN_BACK_TO_DISPUTES, callback_data="admin_disputes")
    builder.adjust(1)
    return builder


async def _render_card(
    session: AsyncSession,
    dispute_id: int,
) -> tuple[str, object] | None:
    dispute = await session.get(PaymentDispute, dispute_id)
    if dispute is None:
        return None
    payment = await session.get(Payment, dispute.payment_id)
    reservation = (
        await session.get(AccountBalanceReservation, dispute.reservation_id)
        if dispute.reservation_id
        else None
    )
    text = (
        texts.ADMIN_DISPUTES.format(value_0=dispute.id, value_1=STATUS_LABELS.get(dispute.status, safe(dispute.status)), value_2=safe(dispute.provider_case_id), value_3=safe(payment.external_id if payment else texts.PLACEHOLDER_DASH), value_4=int(dispute.amount), value_5=dispute.disputed_at.date().isoformat(), value_6=reservation.id if reservation else texts.PLACEHOLDER_DASH, value_7=safe(reservation.status) if reservation else texts.DISPUTE_RESERVATION_MISSING, value_8=dispute.chargeback_entry_id or texts.PLACEHOLDER_DASH, value_9=safe(dispute.note or texts.PLACEHOLDER_DASH))
    )
    return text, _card_keyboard(dispute).as_markup()


@router.callback_query(F.data == "admin_disputes")
async def show_disputes(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    rows = list(
        (
            await session.scalars(
                select(PaymentDispute)
                .order_by(PaymentDispute.created_at.desc(), PaymentDispute.id.desc())
                .limit(20)
            )
        ).all()
    )
    lines = [
        texts.ADMIN_DISPUTES_HEADER + "\n",
    ]
    builder = _list_keyboard()
    for dispute in rows:
        lines.append(
            texts.ADMIN_DISPUTE_STATUS_WON_LABEL.format(value_0=dispute.id, value_1=STATUS_LABELS.get(dispute.status, dispute.status), value_2=int(dispute.amount), value_3=safe(dispute.provider_case_id))
        )
        builder.button(
            text=(
                texts.ADMIN_DISPUTE_BTN_LIST.format(value_0=dispute.id, value_1=STATUS_LABELS.get(dispute.status, dispute.status))
            ),
            callback_data=f"admin_dispute_card:{dispute.id}",
        )
    if not rows:
        lines.append(texts.ADMIN_DISPUTE_STATUS_LOST_LABEL)
    builder.adjust(1)
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_dispute_new")
async def start_dispute_entry(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.set_state(DisputeEntry.details)
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_CANCEL, callback_data="admin_dispute_cancel")
    await callback.message.answer(
        texts.ADMIN_DISPUTE_BTN_ACCEPT,
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_dispute_cancel")
async def cancel_dispute_entry(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(texts.ADMIN_DISPUTES_INPUT_CANCELLED)
    await callback.answer()


@router.message(DisputeEntry.details)
async def receive_dispute_entry(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(message.from_user.id):
        await state.clear()
        await message.answer(texts.ERROR_ACCESS_DENIED)
        return
    parts = [part.strip() for part in (message.text or "").split("|", 5)]
    if len(parts) != 6:
        await message.answer(texts.ADMIN_DISPUTE_BTN_REJECT)
        return
    provider_payment_id, case_id, amount, date_text, status, note = parts
    if status not in STATUS_LABELS:
        await message.answer(texts.ADMIN_DISPUTE_BTN_REVIEW)
        return
    try:
        disputed_at = datetime.strptime(date_text, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        await message.answer(texts.ADMIN_DISPUTE_BTN_BACK_TO_LIST)
        return
    try:
        result = await open_payment_dispute(
            session,
            provider_payment_id=provider_payment_id,
            provider_case_id=case_id,
            amount=amount,
            disputed_at=disputed_at,
            note=note,
            admin_id=message.from_user.id,
        )
        dispute = result.dispute
        if status == "manual_review":
            dispute = await mark_payment_dispute_manual_review(
                session,
                dispute_id=dispute.id,
                admin_id=message.from_user.id,
                note=note or "manual review",
            )
        elif status in {"won_by_merchant", "lost_by_merchant"}:
            dispute = await resolve_payment_dispute(
                session,
                dispute_id=dispute.id,
                outcome=status,
                admin_id=message.from_user.id,
                note=note,
            )
    except PaymentDisputeError as exc:
        await message.answer(_error_text(exc.code))
        return
    await state.clear()
    rendered = await _render_card(session, dispute.id)
    prefix = (
        texts.ADMIN_DISPUTE_CREATED_PREFIX
        if result.created
        else texts.ADMIN_DISPUTE_EXISTING_PREFIX
    )
    await message.answer(
        prefix + rendered[0],
        reply_markup=rendered[1],
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin_dispute_card:"))
async def show_dispute_card(callback: CallbackQuery, session: AsyncSession):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    try:
        dispute_id = int(callback.data.rsplit(":", 1)[1])
    except (TypeError, ValueError):
        await callback.answer(texts.ADMIN_DISPUTE_STATUS_WON_BADGE, show_alert=True)
        return
    rendered = await _render_card(session, dispute_id)
    if rendered is None:
        await callback.answer(texts.ADMIN_DISPUTE_STATUS_LOST_BADGE, show_alert=True)
        return
    await callback.message.edit_text(
        rendered[0],
        reply_markup=rendered[1],
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_dispute_review:"))
async def mark_dispute_review(callback: CallbackQuery, session: AsyncSession):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    try:
        dispute_id = int(callback.data.rsplit(":", 1)[1])
        dispute = await mark_payment_dispute_manual_review(
            session,
            dispute_id=dispute_id,
            admin_id=callback.from_user.id,
            note="marked for manual review in Telegram admin",
        )
    except (TypeError, ValueError):
        await callback.answer(texts.ADMIN_DISPUTE_STATUS_REVIEW_BADGE, show_alert=True)
        return
    except PaymentDisputeError as exc:
        await callback.answer(_error_text(exc.code), show_alert=True)
        return
    rendered = await _render_card(session, dispute.id)
    await callback.message.edit_text(
        rendered[0],
        reply_markup=rendered[1],
        parse_mode="HTML",
    )
    await callback.answer(texts.ADMIN_DISPUTE_STATUS_OPEN_BADGE, show_alert=True)


@router.callback_query(F.data.startswith("admin_dispute_resolve:"))
async def confirm_dispute_resolution(
    callback: CallbackQuery,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 3 or parts[1] not in {
        "won_by_merchant",
        "lost_by_merchant",
    }:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return
    try:
        dispute_id = int(parts[2])
    except ValueError:
        await callback.answer(texts.ADMIN_DISPUTE_STATUS_CANCELLED_BADGE, show_alert=True)
        return
    dispute = await session.get(PaymentDispute, dispute_id)
    if dispute is None or dispute.status not in {"open", "manual_review"}:
        await callback.answer(texts.ADMIN_DISPUTE_CONFIRM_TITLE, show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_CONFIRM,
        callback_data=f"admin_dispute_apply:{parts[1]}:{dispute.id}",
    )
    builder.button(
        text=texts.BTN_CANCEL,
        callback_data=f"admin_dispute_card:{dispute.id}",
    )
    builder.adjust(1)
    effect = (
        texts.ADMIN_DISPUTE_STATUS_REVIEW_LABEL
        if parts[1] == "won_by_merchant"
        else texts.ADMIN_DISPUTE_STATUS_UNDER_REVIEW_LABEL
    )
    await callback.message.edit_text(
        texts.ADMIN_DISPUTE_CONFIRM_NOTE.format(value_0=dispute.id, value_1=STATUS_LABELS[parts[1]], value_2=effect),
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_dispute_apply:"))
async def apply_dispute_resolution(
    callback: CallbackQuery,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return
    try:
        dispute = await resolve_payment_dispute(
            session,
            dispute_id=int(parts[2]),
            outcome=parts[1],
            admin_id=callback.from_user.id,
        )
    except (TypeError, ValueError):
        await callback.answer(texts.ADMIN_DISPUTE_CONFIRM_AMOUNT, show_alert=True)
        return
    except PaymentDisputeError as exc:
        await callback.answer(_error_text(exc.code), show_alert=True)
        return
    rendered = await _render_card(session, dispute.id)
    await callback.message.edit_text(
        rendered[0],
        reply_markup=rendered[1],
        parse_mode="HTML",
    )
    await callback.answer(texts.ADMIN_DISPUTE_CONFIRM_PAYMENT, show_alert=True)
