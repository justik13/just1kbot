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
    "open": "открыт",
    "manual_review": "ручная проверка",
    "won_by_merchant": "выигран продавцом",
    "lost_by_merchant": "проигран продавцом",
}


def _error_text(code: str) -> str:
    return {
        "provider_payment_id_required": "Нужен YooKassa payment ID",
        "provider_case_id_required": "Нужен ID спора банка/провайдера",
        "dispute_amount_invalid": "Сумма должна быть целым числом рублей",
        "disputed_at_timezone_required": "Некорректная дата спора",
        "payment_not_found": "Платёж с таким YooKassa ID не найден",
        "dispute_requires_balance_topup": (
            "Спор поддерживается только для пополнения баланса"
        ),
        "payment_not_settled": "Платёж ещё не подтверждён",
        "payment_not_credited": "Пополнение ещё не зачислено в ledger",
        "provider_case_id_conflict": "Этот case ID уже связан с другими данными",
        "payment_has_active_dispute": "По платежу уже открыт спор",
        "refund_in_progress": "Сначала завершите активный refund",
        "dispute_exceeds_payment_exposure": (
            "Сумма превышает остаток платёжного риска"
        ),
        "dispute_not_found": "Спор не найден",
        "dispute_already_resolved": "Спор уже завершён другим исходом",
    }.get(code, "Операция со спором отклонена финансовыми инвариантами")


def _list_keyboard() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Зарегистрировать спор", callback_data="admin_dispute_new")
    builder.button(text="🔄 Обновить", callback_data="admin_disputes")
    builder.button(text="← В админку", callback_data="admin_menu")
    builder.adjust(1)
    return builder


def _card_keyboard(dispute: PaymentDispute) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    if dispute.status in {"open", "manual_review"}:
        builder.button(
            text="✅ Продавец выиграл",
            callback_data=f"admin_dispute_resolve:won_by_merchant:{dispute.id}",
        )
        builder.button(
            text="❌ Продавец проиграл",
            callback_data=f"admin_dispute_resolve:lost_by_merchant:{dispute.id}",
        )
        if dispute.status == "open":
            builder.button(
                text="🛑 Ручная проверка",
                callback_data=f"admin_dispute_review:{dispute.id}",
            )
    builder.button(text="← К спорам", callback_data="admin_disputes")
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
        f"⚠️ <b>Спор #{dispute.id}</b>\n"
        f"Статус: <b>{STATUS_LABELS.get(dispute.status, safe(dispute.status))}</b>\n"
        f"Case ID: <code>{safe(dispute.provider_case_id)}</code>\n"
        f"YooKassa payment: "
        f"<code>{safe(payment.external_id if payment else '—')}</code>\n"
        f"Сумма: <b>{int(dispute.amount)} RUB</b>\n"
        f"Дата спора: <code>{dispute.disputed_at.date().isoformat()}</code>\n"
        f"Reservation: <code>{reservation.id if reservation else '—'}</code> "
        f"({safe(reservation.status) if reservation else 'нет'})\n"
        f"Chargeback entry: <code>{dispute.chargeback_entry_id or '—'}</code>\n"
        f"Заметка: {safe(dispute.note or '—')}"
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
    lines = ["🛠 Админка › ⚠️ <b>Платёжные споры</b>", ""]
    builder = _list_keyboard()
    for dispute in rows:
        lines.append(
            f"#{dispute.id} · {STATUS_LABELS.get(dispute.status, dispute.status)} · "
            f"{int(dispute.amount)} ₽ · case={safe(dispute.provider_case_id)}"
        )
        builder.button(
            text=(
                f"#{dispute.id} · "
                f"{STATUS_LABELS.get(dispute.status, dispute.status)}"
            ),
            callback_data=f"admin_dispute_card:{dispute.id}",
        )
    if not rows:
        lines.append("Споров пока нет.")
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
        "Отправьте одной строкой:\n"
        "<code>YooKassa_payment_ID | case_ID | сумма | YYYY-MM-DD | "
        "open/manual_review/won_by_merchant/lost_by_merchant | заметка</code>\n\n"
        "Пример:\n"
        "<code>2f... | bank-case-17 | 499 | 2026-08-02 | open | "
        "ожидаем документы</code>",
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
        await message.answer("Нужно ровно 6 полей, разделённых символом |")
        return
    provider_payment_id, case_id, amount, date_text, status, note = parts
    if status not in STATUS_LABELS:
        await message.answer("Некорректный статус спора")
        return
    try:
        disputed_at = datetime.strptime(date_text, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        await message.answer("Дата должна быть в формате YYYY-MM-DD")
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
    prefix = "Создан новый спор.\n\n" if result.created else "Спор уже существовал.\n\n"
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
        await callback.answer("Некорректный ID", show_alert=True)
        return
    rendered = await _render_card(session, dispute_id)
    if rendered is None:
        await callback.answer("Спор не найден", show_alert=True)
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
        await callback.answer("Некорректный ID", show_alert=True)
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
    await callback.answer("Переведено на ручную проверку", show_alert=True)


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
        await callback.answer("Некорректный запрос", show_alert=True)
        return
    try:
        dispute_id = int(parts[2])
    except ValueError:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    dispute = await session.get(PaymentDispute, dispute_id)
    if dispute is None or dispute.status not in {"open", "manual_review"}:
        await callback.answer("Состояние спора уже изменилось", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Подтвердить",
        callback_data=f"admin_dispute_apply:{parts[1]}:{dispute.id}",
    )
    builder.button(
        text="Отмена",
        callback_data=f"admin_dispute_card:{dispute.id}",
    )
    builder.adjust(1)
    effect = (
        "reservation будет освобождена"
        if parts[1] == "won_by_merchant"
        else "будет создан exactly-once chargeback debit; возможен долг"
    )
    await callback.message.edit_text(
        "⚠️ <b>Подтвердите исход спора</b>\n\n"
        f"Спор: <code>#{dispute.id}</code>\n"
        f"Исход: <b>{STATUS_LABELS[parts[1]]}</b>\n"
        f"Эффект: {effect}.",
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
        await callback.answer("Некорректный запрос", show_alert=True)
        return
    try:
        dispute = await resolve_payment_dispute(
            session,
            dispute_id=int(parts[2]),
            outcome=parts[1],
            admin_id=callback.from_user.id,
        )
    except (TypeError, ValueError):
        await callback.answer("Некорректный ID", show_alert=True)
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
    await callback.answer("Исход спора зафиксирован", show_alert=True)
