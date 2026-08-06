"""Two-step confirmation and shortage recovery for balance purchases."""
import logging
import uuid

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts

from bot.keyboards import (
    get_back_button,
    get_balance_purchase_confirm_keyboard,
    get_balance_shortage_keyboard,
    get_payment_success_keyboard,
)
from bot.states import BalanceStates
from config.settings import get_settings
from database.models import TariffQuote, User
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
logger = logging.getLogger(__name__)


PURCHASE_ERRORS = {
    "quote_not_found": texts.RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L38_1,
    "quote_expired": texts.RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L39_1,
    "quote_not_active": texts.RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L40_1,
    "tariff_unavailable": texts.RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L41_1,
    "tariff_price_changed": texts.RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L42_1,
    "quote_price_mismatch": texts.RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L43_1,
    "subscription_state_changed": texts.RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L44_1,
    "insufficient_balance": texts.RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L45_1,
    "financial_hold": texts.RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L46_1,
    "account_debt": texts.RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L47_1,
    "too_many_devices": texts.RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L48_1,
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
            PURCHASE_ERRORS.get(exc.code, texts.RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L79_1),
            get_back_button("menu_subscription"),
        )
        return
    quote = intent.quote
    source = _source(quote.operation_type)
    back = f"select_tariff:{intent.tariff.id}:{source}"
    price = int(quote.amount_due_rub)
    before = int(intent.balance.available)
    after = max(0, before - price)
    tariff_name = get_tariff_display_name(intent.version.device_limit)
    operation = texts.RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L90_1 if quote.operation_type == "renew" else texts.WORD_PURCHASE
    text = (
        texts.RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L92_1.format(value_0=operation.lower(), value_1=tariff_name, value_2=intent.version.duration_hours // 24, value_3=intent.version.device_limit, value_4=price, value_5=before, value_6=after)
    )
    if intent.shortage > 0:
        minimum = get_settings().BALANCE_MIN_TOPUP_RUB
        exact = max(int(intent.shortage), minimum)
        remainder = exact - int(intent.shortage)
        if remainder:
            text += (
                texts.RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L106_1.format(value_0=int(intent.shortage), value_1=minimum, value_2=remainder)
            )
        else:
            text += texts.RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L111_1.format(value_0=int(intent.shortage))
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
        await callback.answer(texts.UI_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L135_1, show_alert=True)
        return
    await _render_purchase_review(callback, session, db_user, quote_id)


@router.callback_query(F.data.startswith("balance_purchase_confirm:"))
async def confirm_purchase(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(texts.UI_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L147_1, show_alert=False)
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
        logger.warning(
            "AccountPurchaseError in confirm_purchase: code=%s, user_id=%s, quote_public_id=%s",
            exc.code,
            db_user.id,
            quote_id,
        )
        if exc.code == "insufficient_balance":
            await _render_purchase_review(callback, session, db_user, quote_id)
            return

        failed_quote = await session.scalar(
            select(TariffQuote).where(
                TariffQuote.public_id == quote_id,
                TariffQuote.user_id == db_user.id,
                TariffQuote.status == "active",
            )
        )
        if failed_quote:
            failed_quote.status = "cancelled"
            failed_quote.diagnostic_reason = f"settlement_failed:{exc.code}"
            await session.flush()

        await render_hub(
            callback.bot,
            callback.message.chat.id,
            PURCHASE_ERRORS.get(
                exc.code, texts.RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L167_1
            ),
            get_back_button("menu_subscription"),
        )
        return

    charged = abs(int(result.debit.amount)) if result.debit else 0

    intent = await get_account_purchase_intent(
        session, user_id=db_user.id, quote_public_id=quote_id
    )
    operation = (
        texts.RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L176_1 if result.quote.operation_type == "renew" else texts.PURCHASE_COMPLETED
    )
    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.UI_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L180_1.format(
            value_0=operation,
            value_1=get_tariff_display_name(intent.version.device_limit),
            value_2=intent.version.duration_hours // 24,
            value_3=charged,
            value_4=int(result.balance_after.real_available),
            value_5=int(result.balance_after.bonus_available),
        ),
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


@router.callback_query(F.data.startswith("bal_short_exact:"))
async def topup_exact_shortage(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(
        texts.UI_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L205_1, show_alert=False
    )
    quote_id = _uuid_from_callback(callback.data)
    if db_user is None or quote_id is None:
        return
    try:
        intent, context = await _shortage_context(
            session, db_user, quote_id
        )
    except AccountPurchaseError:
        await callback.answer(
            texts.UI_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L214_1, show_alert=True
        )
        return
    if intent.shortage <= 0:
        await _render_purchase_review(
            callback, session, db_user, quote_id
        )
        return
    amount = max(int(intent.shortage), get_settings().BALANCE_MIN_TOPUP_RUB)
    await _create_and_render_topup(
        callback,
        session,
        db_user,
        amount,
        context=context,
    )


@router.callback_query(F.data.startswith("bal_short_custom:"))
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
        await callback.answer(texts.UI_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L251_1, show_alert=True)
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
        texts.UI_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L263_1.format(value_0=minimum),
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
                texts.UI_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L300_1,
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
            PURCHASE_ERRORS.get(exc.code, texts.RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L316_1),
            get_back_button("menu_subscription"),
        )
        return
    await _render_purchase_review(
        callback, session, db_user, intent.quote.public_id
    )
