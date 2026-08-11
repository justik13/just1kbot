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
    "open": texts.RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L35_1,
    "manual_review": texts.RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L36_1,
    "won_by_merchant": texts.RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L37_1,
    "lost_by_merchant": texts.RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L38_1,
}


def _error_text(code: str) -> str:
    return {
        "provider_payment_id_required": texts.RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L44_1,
        "provider_case_id_required": texts.RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L45_1,
        "dispute_amount_invalid": texts.RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L46_1,
        "disputed_at_timezone_required": texts.RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L47_1,
        "payment_not_found": texts.RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L48_1,
        "dispute_requires_balance_topup": (
            texts.RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L50_1
        ),
        "payment_not_settled": texts.RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L52_1,
        "payment_not_credited": texts.RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L53_1,
        "provider_case_id_conflict": texts.RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L54_1,
        "payment_has_active_dispute": texts.RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L55_1,
        "refund_in_progress": texts.RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L56_1,
        "dispute_exceeds_payment_exposure": (
            texts.RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L58_1
        ),
        "dispute_not_found": texts.RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L60_1,
        "dispute_already_resolved": texts.RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L61_1,
    }.get(code, texts.RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L62_1)


def _list_keyboard() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.UI_BOT_HANDLERS_ADMIN_DISPUTES_L67_1, callback_data="admin_dispute_new")
    builder.button(text=texts.UI_BOT_HANDLERS_ADMIN_DISPUTES_L68_1, callback_data="admin_disputes")
    builder.button(text=texts.UI_BOT_HANDLERS_ADMIN_DISPUTES_L69_1, callback_data="admin_menu")
    builder.adjust(1)
    return builder


def _card_keyboard(dispute: PaymentDispute) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    if dispute.status in {"open", "manual_review"}:
        builder.button(
            text=texts.UI_BOT_HANDLERS_ADMIN_DISPUTES_L78_1,
            callback_data=f"admin_dispute_resolve:won_by_merchant:{dispute.id}",
        )
        builder.button(
            text=texts.UI_BOT_HANDLERS_ADMIN_DISPUTES_L82_1,
            callback_data=f"admin_dispute_resolve:lost_by_merchant:{dispute.id}",
        )
        if dispute.status == "open":
            builder.button(
                text=texts.UI_BOT_HANDLERS_ADMIN_DISPUTES_L87_1,
                callback_data=f"admin_dispute_review:{dispute.id}",
            )
    builder.button(text=texts.UI_BOT_HANDLERS_ADMIN_DISPUTES_L90_1, callback_data="admin_disputes")
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
        texts.RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L109_1.format(value_0=dispute.id, value_1=STATUS_LABELS.get(dispute.status, safe(dispute.status)), value_2=safe(dispute.provider_case_id), value_3=safe(payment.external_id if payment else texts.PLACEHOLDER_DASH), value_4=int(dispute.amount), value_5=dispute.disputed_at.date().isoformat(), value_6=reservation.id if reservation else texts.PLACEHOLDER_DASH, value_7=safe(reservation.status) if reservation else texts.DISPUTE_RESERVATION_MISSING, value_8=dispute.chargeback_entry_id or texts.PLACEHOLDER_DASH, value_9=safe(dispute.note or texts.PLACEHOLDER_DASH))
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
        "⚖️ <b>Управление платежными спорами (Disputes)</b>\n",
        "ℹ️ <i>Диспуты возникают при обращении клиентов в банк или платёжный провайдер (чарджбэк). Здесь вы можете просмотреть детали спора и урегулировать вопрос.</i>\n",
    ]
    builder = _list_keyboard()
    for dispute in rows:
        lines.append(
            texts.RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L147_1.format(value_0=dispute.id, value_1=STATUS_LABELS.get(dispute.status, dispute.status), value_2=int(dispute.amount), value_3=safe(dispute.provider_case_id))
        )
        builder.button(
            text=(
                texts.UI_BOT_HANDLERS_ADMIN_DISPUTES_L152_1.format(value_0=dispute.id, value_1=STATUS_LABELS.get(dispute.status, dispute.status))
            ),
            callback_data=f"admin_dispute_card:{dispute.id}",
        )
    if not rows:
        lines.append(texts.RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L157_1)
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
    await callback.message.answer(
        texts.UI_BOT_HANDLERS_ADMIN_DISPUTES_L175_1,
        parse_mode="HTML",
    )
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
        await message.answer(texts.UI_BOT_HANDLERS_ADMIN_DISPUTES_L198_1)
        return
    provider_payment_id, case_id, amount, date_text, status, note = parts
    if status not in STATUS_LABELS:
        await message.answer(texts.UI_BOT_HANDLERS_ADMIN_DISPUTES_L202_1)
        return
    try:
        disputed_at = datetime.strptime(date_text, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        await message.answer(texts.UI_BOT_HANDLERS_ADMIN_DISPUTES_L209_1)
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
        await callback.answer(texts.UI_BOT_HANDLERS_ADMIN_DISPUTES_L258_1, show_alert=True)
        return
    rendered = await _render_card(session, dispute_id)
    if rendered is None:
        await callback.answer(texts.UI_BOT_HANDLERS_ADMIN_DISPUTES_L262_1, show_alert=True)
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
        await callback.answer(texts.UI_BOT_HANDLERS_ADMIN_DISPUTES_L286_1, show_alert=True)
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
    await callback.answer(texts.UI_BOT_HANDLERS_ADMIN_DISPUTES_L297_1, show_alert=True)


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
        await callback.answer(texts.UI_BOT_HANDLERS_ADMIN_DISPUTES_L313_1, show_alert=True)
        return
    try:
        dispute_id = int(parts[2])
    except ValueError:
        await callback.answer(texts.UI_BOT_HANDLERS_ADMIN_DISPUTES_L318_1, show_alert=True)
        return
    dispute = await session.get(PaymentDispute, dispute_id)
    if dispute is None or dispute.status not in {"open", "manual_review"}:
        await callback.answer(texts.UI_BOT_HANDLERS_ADMIN_DISPUTES_L322_1, show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_BOT_HANDLERS_ADMIN_DISPUTES_L326_1,
        callback_data=f"admin_dispute_apply:{parts[1]}:{dispute.id}",
    )
    builder.button(
        text=texts.UI_BOT_HANDLERS_ADMIN_DISPUTES_L330_1,
        callback_data=f"admin_dispute_card:{dispute.id}",
    )
    builder.adjust(1)
    effect = (
        texts.RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L333_1
        if parts[1] == "won_by_merchant"
        else texts.RUNTIME_BOT_HANDLERS_ADMIN_DISPUTES_L335_1
    )
    await callback.message.edit_text(
        texts.UI_BOT_HANDLERS_ADMIN_DISPUTES_L340_1.format(value_0=dispute.id, value_1=STATUS_LABELS[parts[1]], value_2=effect),
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
        await callback.answer(texts.UI_BOT_HANDLERS_ADMIN_DISPUTES_L360_1, show_alert=True)
        return
    try:
        dispute = await resolve_payment_dispute(
            session,
            dispute_id=int(parts[2]),
            outcome=parts[1],
            admin_id=callback.from_user.id,
        )
    except (TypeError, ValueError):
        await callback.answer(texts.UI_BOT_HANDLERS_ADMIN_DISPUTES_L370_1, show_alert=True)
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
    await callback.answer(texts.UI_BOT_HANDLERS_ADMIN_DISPUTES_L381_1, show_alert=True)
