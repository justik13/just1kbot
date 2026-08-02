"""Two-step confirmation and shortage recovery for balance purchases."""

import uuid

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import (
    get_back_button,
    get_balance_purchase_confirm_keyboard,
    get_balance_shortage_keyboard,
    get_payment_success_keyboard,
)
from bot.states import BalanceStates
from config.settings import get_settings
from database.models import User
from services.account_purchase import (
    AccountPurchaseError,
    get_account_purchase_intent,
    prepare_account_purchase,
    settle_account_purchase,
)
from utils.callbacks import parse_callback_id, parse_callback_parts
from utils.tariff_names import get_tariff_display_name
from utils.telegram import render_hub
from utils.datetime_helpers import now_utc

from .balance_routes import _create_and_render_topup


router = Router()


PURCHASE_ERRORS = {
    "quote_not_found": "Котировка не найдена. Выберите тариф ещё раз.",
    "quote_expired": "Цена устарела. Выберите тариф ещё раз.",
    "quote_not_active": "Эта покупка больше не активна.",
    "tariff_unavailable": "Тариф больше недоступен.",
    "tariff_price_changed": "Цена тарифа изменилась. Проверьте новую цену.",
    "quote_price_mismatch": "Цена изменилась. Выберите тариф ещё раз.",
    "subscription_state_changed": "Состояние подписки изменилось. Начните операцию заново.",
    "insufficient_balance": "На балансе недостаточно средств.",
    "financial_hold": "Покупки временно заблокированы из-за финансового спора.",
    "account_debt": "Покупки недоступны до погашения задолженности.",
    "too_many_devices": "Сначала удалите лишние устройства.",
}


def _uuid_from_callback(data: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(data.split(":", 1)[1])
    except (ValueError, IndexError, AttributeError):
        return None


def _source(operation_type: str) -> str:
    return "renew" if operation_type == "renew" else "showcase"


async def _render_purchase_review(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    quote_public_id: uuid.UUID,
) -> None:
    try:
        intent = await get_account_purchase_intent(
            session,
            user_id=user.id,
            quote_public_id=quote_public_id,
        )
    except AccountPurchaseError as exc:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            PURCHASE_ERRORS.get(exc.code, "Не удалось открыть покупку."),
            get_back_button("menu_subscription"),
        )
        return
    quote = intent.quote
    source = _source(quote.operation_type)
    back = f"select_tariff:{intent.tariff.id}:{source}"
    price = int(quote.confirmed_payment_required_rub)
    before = int(intent.balance.available)
    after = max(0, before - price)
    tariff_name = get_tariff_display_name(intent.version.device_limit)
    operation = "Продление" if quote.operation_type == "renew" else "Покупка"
    text = (
        f"✅ <b>Подтверждение: {operation.lower()}</b>\n\n"
        f"Тариф: <b>{tariff_name}</b>\n"
        f"Срок: <b>{intent.version.duration_hours // 24} дней</b>\n"
        f"Лимит устройств: <b>{intent.version.device_limit}</b>\n"
        f"Цена: <b>{price} ₽</b>\n\n"
        f"Баланс до покупки: <b>{before} ₽</b>\n"
        f"Баланс после покупки: <b>{after} ₽</b>"
    )
    if intent.shortage > 0:
        minimum = get_settings().BALANCE_MIN_TOPUP_RUB
        exact = max(int(intent.shortage), minimum)
        remainder = exact - int(intent.shortage)
        if remainder:
            text += (
                f"\n\nНе хватает {int(intent.shortage)} ₽. "
                f"Минимальное пополнение — {minimum} ₽; "
                f"после покупки останется {remainder} ₽."
            )
        else:
            text += f"\n\nНе хватает: <b>{int(intent.shortage)} ₽</b>."
        keyboard = get_balance_shortage_keyboard(
            str(quote.public_id), exact, back
        )
    else:
        keyboard = get_balance_purchase_confirm_keyboard(
            str(quote.public_id), back
        )
    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        keyboard,
    )


@router.callback_query(F.data.startswith("balance_purchase_review:"))
async def review_purchase(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(show_alert=False)
    quote_id = _uuid_from_callback(callback.data)
    if db_user is None or quote_id is None:
        await callback.answer("Некорректная покупка", show_alert=True)
        return
    await _render_purchase_review(callback, session, db_user, quote_id)


@router.callback_query(F.data.startswith("balance_purchase_confirm:"))
async def confirm_purchase(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer("Проводим покупку…", show_alert=False)
    await state.clear()
    quote_id = _uuid_from_callback(callback.data)
    if db_user is None or quote_id is None:
        return
    try:
        result = await settle_account_purchase(
            session,
            user_id=db_user.id,
            quote_public_id=quote_id,
        )
    except AccountPurchaseError as exc:
        if exc.code == "insufficient_balance":
            await _render_purchase_review(callback, session, db_user, quote_id)
            return
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            PURCHASE_ERRORS.get(
                exc.code, "Покупка не выполнена. Деньги не списаны."
            ),
            get_back_button("menu_subscription"),
        )
        return
    intent = await get_account_purchase_intent(
        session, user_id=db_user.id, quote_public_id=quote_id
    )
    operation = (
        "Продление выполнено" if result.quote.operation_type == "renew" else "Тариф куплен"
    )
    await render_hub(
        callback.bot,
        callback.message.chat.id,
        f"🎉 <b>{operation}</b>\n\n"
        f"Тариф: <b>{get_tariff_display_name(intent.version.device_limit)}</b>\n"
        f"Срок: {intent.version.duration_hours // 24} дней\n"
        f"Списано: <b>{abs(int(result.debit.amount))} ₽</b>\n"
        f"Баланс: <b>{int(result.balance_after.available)} ₽</b>",
        get_payment_success_keyboard(),
    )
    result.quote.purchase_notified_at = result.quote.purchase_notified_at or now_utc()


async def _shortage_context(session, user, quote_id):
    intent = await get_account_purchase_intent(
        session, user_id=user.id, quote_public_id=quote_id
    )
    source = _source(intent.quote.operation_type)
    return intent, {
        "operation": intent.quote.operation_type,
        "quote_public_id": str(intent.quote.public_id),
        "tariff_id": intent.tariff.id,
        "source": source,
    }


@router.callback_query(F.data.startswith("balance_shortage_exact:"))
async def topup_exact_shortage(
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
    except AccountPurchaseError:
        await callback.answer("Котировка устарела", show_alert=True)
        return
    if intent.shortage <= 0:
        await _render_purchase_review(callback, session, db_user, quote_id)
        return
    amount = max(
        int(intent.shortage), get_settings().BALANCE_MIN_TOPUP_RUB
    )
    await _create_and_render_topup(
        callback.message,
        session,
        db_user,
        amount,
        context=context,
    )


@router.callback_query(F.data.startswith("balance_shortage_custom:"))
async def topup_custom_shortage(
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
    except AccountPurchaseError:
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
        get_back_button(
            f"balance_purchase_review:{intent.quote.public_id}"
        ),
    )


@router.callback_query(F.data.startswith("balance_resume_purchase:"))
async def resume_purchase_after_topup(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(show_alert=False)
    parts = parse_callback_parts(callback.data, 2)
    tariff_id = parse_callback_id(callback.data, 1)
    source = parts[2] if parts and len(parts) > 2 else None
    if (
        db_user is None
        or tariff_id is None
        or source not in {"showcase", "renew", "change"}
    ):
        return
    if source == "change":
        from services.tariff_change_quote import create_tariff_change_quote
        from .tariff_change_routes import render_tariff_change_review

        quote_result = await create_tariff_change_quote(
            session,
            user_id=db_user.id,
            target_tariff_id=tariff_id,
            as_of=now_utc(),
        )
        if quote_result.failure_code:
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                "Операция устарела. Выберите тариф заново.",
                get_back_button("payment_change_tariff"),
            )
            return
        await render_tariff_change_review(
            callback,
            session,
            db_user,
            quote_result.quote.public_id,
        )
        return
    try:
        intent = await prepare_account_purchase(
            session, user_id=db_user.id, tariff_id=tariff_id
        )
    except AccountPurchaseError as exc:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            PURCHASE_ERRORS.get(exc.code, "Операция устарела. Выберите тариф заново."),
            get_back_button("menu_subscription"),
        )
        return
    await _render_purchase_review(
        callback, session, db_user, intent.quote.public_id
    )
