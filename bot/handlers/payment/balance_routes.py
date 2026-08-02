"""Telegram account-balance, top-up, and financial-history screens."""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import (
    get_back_button,
    get_balance_amounts_keyboard,
    get_balance_keyboard,
    get_topup_payment_keyboard,
    get_topup_waiting_keyboard,
)
from bot.states import BalanceStates
from config.settings import get_settings
from database.models import Payment, User
from database.repositories.account_ledger_repo import (
    get_account_balance,
    get_account_history,
)
from database.repositories.tariffs_repo import get_active_tariffs
from services.account_topup import (
    AccountTopupError,
    create_balance_topup,
    get_visible_balance_topup,
    hide_balance_topup,
)
from services.payment_service import PaymentService
from utils.callbacks import parse_callback_id
from utils.datetime_helpers import now_utc
from utils.formatters import format_datetime
from utils.telegram import render_hub


router = Router()
logger = logging.getLogger(__name__)


TOPUP_ERRORS = {
    "topup_amount_must_be_whole_rubles": "Введите сумму целыми рублями без копеек.",
    "topup_below_minimum": "Минимальная сумма пополнения — 10 ₽.",
    "topup_above_maximum": "Максимальная сумма одного пополнения — 5000 ₽.",
    "topup_balance_limit_exceeded": "Сумма превышает допустимый лимит баланса с учётом активных ссылок.",
    "too_many_unfinished_topups": "У вас уже есть три незавершённых платежа. Проверьте их статус или дождитесь отмены.",
    "topup_creation_rate_limited": "Достигнут лимит создания ссылок за 24 часа. Попробуйте позже.",
    "topup_blocked": "Новые пополнения временно заблокированы. Обратитесь в поддержку.",
    "topup_user_banned": "Пополнение недоступно для этого аккаунта.",
}

HISTORY_LABELS = {
    "payment_credit": "Пополнение",
    "purchase_debit": "Покупка тарифа",
    "purchase_reversal": "Возврат покупки",
    "refund_debit": "Возврат через ЮKassa",
    "chargeback_debit": "Банковский спор",
    "admin_adjustment": "Корректировка",
}


def topup_presets(tariffs: list, settings=None) -> list[int]:
    cfg = settings or get_settings()
    prices = {
        int(tariff.price_rub)
        for tariff in tariffs
        if tariff.is_active
        and int(tariff.price_rub) > 0
        and int(tariff.price_rub) <= cfg.BALANCE_MAX_PRESET_RUB
    }
    return sorted(prices)[: cfg.BALANCE_MAX_PRESET_OPTIONS]


def _history_lines(entries: list) -> str:
    if not entries:
        return "<i>Операций пока нет.</i>"
    lines = []
    for entry in entries:
        label = HISTORY_LABELS.get(entry.entry_type, "Операция")
        sign = "+" if entry.amount > 0 else "−"
        amount = abs(int(entry.amount))
        lines.append(
            f"• {format_datetime(entry.created_at)} · {label}: "
            f"<b>{sign}{amount} ₽</b>"
        )
    return "\n".join(lines)


async def _render_balance(
    bot,
    chat_id: int,
    session: AsyncSession,
    user: User,
    *,
    notice: str | None = None,
) -> None:
    snapshot = await get_account_balance(session, user_id=user.id)
    history = await get_account_history(session, user_id=user.id, limit=5)
    visible = await get_visible_balance_topup(session, user_id=user.id)
    details = [f"Доступно: <b>{int(snapshot.available)} ₽</b>"]
    if snapshot.reserved > 0:
        details.append(f"Зарезервировано: <b>{int(snapshot.reserved)} ₽</b>")
    if snapshot.debt > 0:
        details.append(f"Задолженность: <b>{int(snapshot.debt)} ₽</b>")
    prefix = f"{notice}\n\n" if notice else ""
    text = (
        f"{prefix}💰 <b>Баланс</b>\n\n"
        + "\n".join(details)
        + "\n\n<b>Последние операции</b>\n"
        + _history_lines(history)
    )
    await render_hub(
        bot,
        chat_id,
        text,
        get_balance_keyboard(has_visible_topup=visible is not None),
    )


async def _store_presentation(
    session: AsyncSession,
    payment: Payment,
    *,
    chat_id: int,
    message_id: int,
    auto_show: bool,
) -> None:
    payment.topup_context = {
        **(payment.topup_context or {}),
        "chat_id": chat_id,
        "message_id": message_id,
        "auto_show": auto_show,
    }
    await session.flush()


async def _render_topup(
    bot,
    chat_id: int,
    session: AsyncSession,
    user: User,
    payment: Payment,
) -> None:
    balance = await get_account_balance(session, user_id=user.id)
    if payment.payment_url:
        text = (
            "💳 <b>Пополнение баланса</b>\n\n"
            f"Сумма: <b>{int(payment.amount)} ₽</b>\n"
            f"Текущий баланс: <b>{int(balance.available)} ₽</b>\n\n"
            "Ссылка ведёт на защищённую страницу ЮKassa."
        )
        message_id = await render_hub(
            bot,
            chat_id,
            text,
            get_topup_payment_keyboard(payment.payment_url, payment.id),
        )
        payment.payment_url_notified_at = payment.payment_url_notified_at or now_utc()
        await _store_presentation(
            session,
            payment,
            chat_id=chat_id,
            message_id=message_id,
            auto_show=False,
        )
        return
    text = (
        "⏳ <b>Создаём ссылку на пополнение</b>\n\n"
        f"Сумма: <b>{int(payment.amount)} ₽</b>\n"
        f"Текущий баланс: <b>{int(balance.available)} ₽</b>\n\n"
        "Ссылка появится здесь автоматически. Ручная проверка остаётся доступна."
    )
    message_id = await render_hub(
        bot,
        chat_id,
        text,
        get_topup_waiting_keyboard(payment.id),
    )
    await _store_presentation(
        session,
        payment,
        chat_id=chat_id,
        message_id=message_id,
        auto_show=True,
    )


async def _create_and_render_topup(
    target,
    session: AsyncSession,
    user: User,
    amount: int,
    *,
    context: dict | None = None,
) -> None:
    settings = get_settings()
    if not settings.YOOKASSA_SHOP_ID or not settings.YOOKASSA_SECRET_KEY:
        await render_hub(
            target.bot,
            target.chat.id,
            "Сервис пополнения временно недоступен.",
            get_back_button("menu_balance"),
        )
        return
    bot_info = await target.bot.get_me()
    try:
        result = await create_balance_topup(
            session,
            user_id=user.id,
            amount=amount,
            bot_username=bot_info.username,
            context=context,
        )
    except AccountTopupError as exc:
        await render_hub(
            target.bot,
            target.chat.id,
            TOPUP_ERRORS.get(exc.code, "Не удалось создать пополнение."),
            get_back_button("menu_balance"),
        )
        return
    if context:
        result.payment.topup_context = {
            **(result.payment.topup_context or {}),
            **context,
        }
    await _render_topup(
        target.bot, target.chat.id, session, user, result.payment
    )


@router.callback_query(F.data == "menu_balance")
async def show_balance(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(show_alert=False)
    await state.clear()
    if db_user is None:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return
    await _render_balance(callback.bot, callback.message.chat.id, session, db_user)


@router.callback_query(F.data == "balance_history")
async def show_balance_history(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(show_alert=False)
    if db_user is None:
        return
    entries = await get_account_history(session, user_id=db_user.id, limit=30)
    await render_hub(
        callback.bot,
        callback.message.chat.id,
        "🧾 <b>История операций</b>\n\n" + _history_lines(entries),
        get_back_button("menu_balance"),
    )


@router.callback_query(F.data == "balance_topup")
async def choose_topup_amount(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(show_alert=False)
    await state.clear()
    if db_user is None:
        return
    if db_user.topup_blocked:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            TOPUP_ERRORS["topup_blocked"],
            get_back_button("menu_balance"),
        )
        return
    visible = await get_visible_balance_topup(session, user_id=db_user.id)
    if visible:
        await _render_topup(
            callback.bot,
            callback.message.chat.id,
            session,
            db_user,
            visible,
        )
        return
    tariffs = await get_active_tariffs(session)
    amounts = topup_presets(tariffs)
    balance = await get_account_balance(session, user_id=db_user.id)
    await render_hub(
        callback.bot,
        callback.message.chat.id,
        "➕ <b>Пополнение баланса</b>\n\n"
        f"Текущий баланс: <b>{int(balance.available)} ₽</b>\n"
        "Выберите сумму или укажите другую целую сумму в рублях.",
        get_balance_amounts_keyboard(amounts),
    )


@router.callback_query(F.data.startswith("balance_create:"))
async def create_preset_topup(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer("Создаём ссылку…", show_alert=False)
    amount = parse_callback_id(callback.data, 1)
    if db_user is None or amount is None:
        return
    await _create_and_render_topup(callback.message, session, db_user, amount)


@router.callback_query(F.data == "balance_custom_amount")
async def request_custom_amount(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.answer(show_alert=False)
    await state.set_state(BalanceStates.enter_custom_amount)
    await state.set_data({})
    await render_hub(
        callback.bot,
        callback.message.chat.id,
        "Введите сумму пополнения целым числом от 10 до 5000 ₽.\n"
        "Например: <code>499</code>",
        get_back_button("menu_balance"),
    )


@router.message(BalanceStates.enter_custom_amount)
async def accept_custom_amount(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    raw = (message.text or "").strip()
    if not raw.isascii() or not raw.isdigit():
        await render_hub(
            message.bot,
            message.chat.id,
            "Введите целую сумму без копеек, пробелов и знаков. Например: <code>499</code>",
            get_back_button("menu_balance"),
        )
        return
    if db_user is None:
        await state.clear()
        return
    data = await state.get_data()
    minimum = int(data.get("balance_minimum") or get_settings().BALANCE_MIN_TOPUP_RUB)
    amount = int(raw)
    if amount < minimum:
        await render_hub(
            message.bot,
            message.chat.id,
            f"Для выбранной операции нужно пополнить минимум на <b>{minimum} ₽</b>.",
            get_back_button("menu_balance"),
        )
        return
    await state.clear()
    await _create_and_render_topup(
        message,
        session,
        db_user,
        amount,
        context=data.get("balance_context"),
    )


@router.callback_query(F.data == "balance_resume_topup")
async def resume_topup(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(show_alert=False)
    if db_user is None:
        return
    payment = await get_visible_balance_topup(session, user_id=db_user.id)
    if payment is None:
        await _render_balance(
            callback.bot,
            callback.message.chat.id,
            session,
            db_user,
            notice="Активная ссылка пополнения не найдена.",
        )
        return
    await _render_topup(
        callback.bot,
        callback.message.chat.id,
        session,
        db_user,
        payment,
    )


async def _owned_topup(
    session: AsyncSession, user: User, payment_id: int
) -> Payment | None:
    payment = await session.get(Payment, payment_id)
    if (
        payment is None
        or payment.user_id != user.id
        or payment.payment_kind != "balance_topup"
    ):
        return None
    return payment


@router.callback_query(F.data.startswith("balance_check:"))
async def check_topup(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer("Проверяем…", show_alert=False)
    payment_id = parse_callback_id(callback.data, 1)
    if db_user is None or payment_id is None:
        return
    payment = await _owned_topup(session, db_user, payment_id)
    if payment is None:
        await callback.answer("Пополнение не найдено", show_alert=True)
        return
    await PaymentService.check_yookassa_payment(
        session, payment.id, notify_user=False
    )
    if payment.fulfillment_status == "succeeded":
        await _render_balance(
            callback.bot,
            callback.message.chat.id,
            session,
            db_user,
            notice="✅ Баланс пополнен.",
        )
    elif payment.provider_status == "succeeded":
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            "✅ Оплата подтверждена. Зачисляем деньги на баланс.",
            get_topup_waiting_keyboard(payment.id),
        )
    elif payment.provider_status == "canceled":
        await _render_balance(
            callback.bot,
            callback.message.chat.id,
            session,
            db_user,
            notice="Пополнение отменено платёжным провайдером.",
        )
    else:
        await _render_topup(
            callback.bot,
            callback.message.chat.id,
            session,
            db_user,
            payment,
        )


@router.callback_query(F.data.startswith("balance_cancel:"))
async def cancel_topup_ui(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(show_alert=False)
    await state.clear()
    payment_id = parse_callback_id(callback.data, 1)
    if db_user is None or payment_id is None:
        return
    try:
        await hide_balance_topup(
            session, user_id=db_user.id, payment_id=payment_id
        )
    except AccountTopupError:
        await callback.answer("Пополнение уже завершено", show_alert=True)
        return
    await _render_balance(
        callback.bot,
        callback.message.chat.id,
        session,
        db_user,
        notice=(
            "Пополнение отменено. Ссылка скрыта. Если оплата уже завершена, "
            "деньги всё равно поступят на баланс."
        ),
    )


@router.callback_query(F.data.startswith("balance_later:"))
async def return_later(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(show_alert=False)
    payment_id = parse_callback_id(callback.data, 1)
    if db_user is None or payment_id is None:
        return
    payment = await _owned_topup(session, db_user, payment_id)
    if payment:
        payment.topup_context = {
            **(payment.topup_context or {}),
            "auto_show": False,
        }
    await _render_balance(
        callback.bot,
        callback.message.chat.id,
        session,
        db_user,
        notice="Ссылка сохранена. Вы можете вернуться к пополнению позже.",
    )
