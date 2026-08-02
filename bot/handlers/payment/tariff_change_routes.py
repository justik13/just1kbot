"""Two-step tariff-change confirmation and balance-shortage recovery."""

import uuid

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import (
    get_back_button,
    get_balance_change_confirm_keyboard,
    get_balance_change_shortage_keyboard,
    get_payment_success_keyboard,
)
from bot.states import BalanceStates
from config.settings import get_settings
from database.models import User
from services.account_tariff_change import (
    AccountTariffChangeError,
    get_account_tariff_change_intent,
    settle_account_tariff_change,
)
from utils.datetime_helpers import now_utc
from utils.tariff_names import get_tariff_display_name
from utils.telegram import render_hub

from .balance_routes import _create_and_render_topup


router = Router()


CHANGE_ERRORS = {
    "quote_not_found": "Котировка не найдена. Выберите тариф ещё раз.",
    "quote_expired": "Расчёт устарел. Выберите тариф ещё раз.",
    "quote_not_active": "Эта смена тарифа больше не активна.",
    "tariff_unavailable": "Выбранный тариф больше недоступен.",
    "tariff_price_changed": "Цена тарифа изменилась. Проверьте новый расчёт.",
    "quote_source_history_changed": "Подписка изменилась. Создайте новый расчёт.",
    "quote_economics_changed": "Экономика подписки изменилась. Создайте новый расчёт.",
    "subscription_state_changed": "Состояние подписки изменилось. Начните заново.",
    "subscription_balance_untracked": "Не удалось надёжно рассчитать остаток подписки.",
    "insufficient_balance": "На балансе недостаточно средств.",
    "financial_hold": "Смена тарифа заблокирована из-за финансового спора.",
    "account_debt": "Смена тарифа недоступна до погашения задолженности.",
    "too_many_devices": "Сначала удалите лишние устройства.",
}


def _uuid_from_callback(data: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(data.split(":", 1)[1])
    except (ValueError, IndexError, AttributeError):
        return None


def _hours_text(hours: int) -> str:
    days, remainder = divmod(hours, 24)
    return f"{days} дн." + (f" {remainder} ч." if remainder else "")


async def render_tariff_change_review(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    quote_public_id,
) -> None:
    try:
        intent = await get_account_tariff_change_intent(
            session,
            user_id=user.id,
            quote_public_id=quote_public_id,
        )
    except AccountTariffChangeError as exc:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            CHANGE_ERRORS.get(exc.code, "Не удалось открыть смену тарифа."),
            get_back_button("payment_change_tariff"),
        )
        return
    quote = intent.quote
    due = int(quote.confirmed_payment_required_rub)
    before = int(intent.balance.available)
    after = max(0, before - due)
    back = f"select_tariff:{intent.target_tariff.id}:change"
    text = (
        "✅ <b>Подтверждение смены тарифа</b>\n\n"
        f"Новый тариф: <b>{get_tariff_display_name(intent.target_version.device_limit)}</b>\n"
        f"Лимит устройств: <b>{intent.target_version.device_limit}</b>\n"
        f"Срок после конвертации: <b>{_hours_text(quote.resulting_paid_hours + quote.resulting_bonus_hours)}</b>\n"
        f"Доплата: <b>{due} ₽</b>\n\n"
        f"Баланс до операции: <b>{before} ₽</b>\n"
        f"Баланс после операции: <b>{after} ₽</b>"
    )
    if intent.shortage > 0:
        minimum = get_settings().BALANCE_MIN_TOPUP_RUB
        exact = max(int(intent.shortage), minimum)
        remainder = exact - int(intent.shortage)
        if remainder:
            text += (
                f"\n\nНе хватает {int(intent.shortage)} ₽. "
                f"Минимальное пополнение — {minimum} ₽; "
                f"после смены останется {remainder} ₽."
            )
        else:
            text += f"\n\nНе хватает: <b>{int(intent.shortage)} ₽</b>."
        keyboard = get_balance_change_shortage_keyboard(
            str(quote.public_id), exact, back
        )
    else:
        keyboard = get_balance_change_confirm_keyboard(
            str(quote.public_id), back
        )
    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        keyboard,
    )


@router.callback_query(F.data.startswith("balance_change_review:"))
async def review_tariff_change(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(show_alert=False)
    quote_id = _uuid_from_callback(callback.data)
    if db_user is None or quote_id is None:
        await callback.answer("Некорректная операция", show_alert=True)
        return
    await render_tariff_change_review(callback, session, db_user, quote_id)


@router.callback_query(F.data.startswith("balance_change_confirm:"))
async def confirm_tariff_change(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer("Меняем тариф…", show_alert=False)
    await state.clear()
    quote_id = _uuid_from_callback(callback.data)
    if db_user is None or quote_id is None:
        return
    try:
        result = await settle_account_tariff_change(
            session,
            user_id=db_user.id,
            quote_public_id=quote_id,
        )
    except AccountTariffChangeError as exc:
        if exc.code == "insufficient_balance":
            await render_tariff_change_review(
                callback, session, db_user, quote_id
            )
            return
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            CHANGE_ERRORS.get(
                exc.code, "Смена тарифа не выполнена. Деньги не списаны."
            ),
            get_back_button("payment_change_tariff"),
        )
        return
    intent = await get_account_tariff_change_intent(
        session,
        user_id=db_user.id,
        quote_public_id=quote_id,
    )
    charged = abs(int(result.debit.amount)) if result.debit else 0
    await render_hub(
        callback.bot,
        callback.message.chat.id,
        "🎉 <b>Тариф изменён</b>\n\n"
        f"Новый тариф: <b>{get_tariff_display_name(intent.target_version.device_limit)}</b>\n"
        f"Срок: {_hours_text(result.quote.resulting_paid_hours + result.quote.resulting_bonus_hours)}\n"
        f"Списано: <b>{charged} ₽</b>\n"
        f"Баланс: <b>{int(result.balance_after.available)} ₽</b>",
        get_payment_success_keyboard(),
    )
    result.quote.purchase_notified_at = (
        result.quote.purchase_notified_at or now_utc()
    )


async def _shortage_context(session, user, quote_id):
    intent = await get_account_tariff_change_intent(
        session, user_id=user.id, quote_public_id=quote_id
    )
    return intent, {
        "operation": "change",
        "quote_public_id": str(intent.quote.public_id),
        "tariff_id": intent.target_tariff.id,
        "source": "change",
    }


@router.callback_query(F.data.startswith("balance_change_shortage_exact:"))
async def topup_exact_change_shortage(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer("Создаём ссылку…", show_alert=False)
    quote_id = _uuid_from_callback(callback.data)
    if db_user is None or quote_id is None:
        return
    try:
        intent, context = await _shortage_context(
            session, db_user, quote_id
        )
    except AccountTariffChangeError:
        await callback.answer("Котировка устарела", show_alert=True)
        return
    if intent.shortage <= 0:
        await render_tariff_change_review(
            callback, session, db_user, quote_id
        )
        return
    amount = max(int(intent.shortage), get_settings().BALANCE_MIN_TOPUP_RUB)
    await _create_and_render_topup(
        callback.message,
        session,
        db_user,
        amount,
        context=context,
    )


@router.callback_query(F.data.startswith("balance_change_shortage_custom:"))
async def topup_custom_change_shortage(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(show_alert=False)
    quote_id = _uuid_from_callback(callback.data)
    if db_user is None or quote_id is None:
        return
    try:
        intent, context = await _shortage_context(
            session, db_user, quote_id
        )
    except AccountTariffChangeError:
        await callback.answer("Котировка устарела", show_alert=True)
        return
    minimum = max(
        int(intent.shortage), get_settings().BALANCE_MIN_TOPUP_RUB
    )
    await state.set_state(BalanceStates.enter_custom_amount)
    await state.set_data(
        {"balance_minimum": minimum, "balance_context": context}
    )
    await render_hub(
        callback.bot,
        callback.message.chat.id,
        f"Введите целую сумму от <b>{minimum} ₽</b> до 5000 ₽.",
        get_back_button(f"balance_change_review:{intent.quote.public_id}"),
    )
