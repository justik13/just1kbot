"""Two-step tariff-change confirmation and balance-shortage recovery."""
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
    get_balance_change_confirm_keyboard,
    get_balance_change_shortage_keyboard,
    get_payment_success_keyboard,
)
from bot.states import BalanceStates
from config.settings import get_settings
from database.models import TariffQuote, User
from services.account_tariff_change import (
    AccountTariffChangeError,
    get_account_tariff_change_intent,
    settle_account_tariff_change,
)
from services.maintenance_service import MaintenanceService
from utils.datetime_helpers import now_utc
from utils.tariff_names import get_tariff_display_name
from utils.telegram import EFFECT_CONFETTI, render_hub

from .balance_routes import _create_and_render_topup
from .common import _render_maintenance

router = Router()
logger = logging.getLogger(__name__)


CHANGE_ERRORS = {
    "quote_not_found": texts.PAYMENT_QUOTE_NOT_FOUND_NOTICE,
    "quote_expired": texts.PAYMENT_CHANGE_TARIFF_CALC_EXPIRED,
    "quote_not_active": texts.PAYMENT_OPERATION_NOT_ACTIVE_NOTICE,
    "tariff_unavailable": texts.PAYMENT_TARIFF_UNAVAILABLE_NOTICE,
    "tariff_price_changed": texts.PAYMENT_CHANGE_TARIFF_PRICE_CHANGED,
    "quote_source_history_changed": texts.PAYMENT_CHANGE_TARIFF_SUB_CHANGED,
    "quote_economics_changed": texts.PAYMENT_CHANGE_TARIFF_ECONOMY_CHANGED,
    "quote_economics_invalid": texts.PAYMENT_CHANGE_TARIFF_ECONOMY_CHANGED,
    "subscription_state_changed": texts.PAYMENT_CHANGE_TARIFF_STATE_CHANGED,
    "subscription_balance_untracked": texts.PAYMENT_CHANGE_TARIFF_CALC_FAILED,
    "insufficient_balance": texts.PAYMENT_INSUFFICIENT_FUNDS_ALERT,
    "financial_hold": texts.PAYMENT_DISPUTE_BLOCKED_NOTICE,
    "account_debt": texts.PAYMENT_DEBT_BLOCKED_NOTICE,
    "too_many_devices": texts.PAYMENT_DEVICES_BLOCKED_NOTICE,
    "change_user_ineligible": texts.PAYMENT_CHANGE_TARIFF_CALC_FAILED,
    "quote_operation_mismatch": texts.PAYMENT_OPERATION_NOT_ACTIVE_NOTICE,
    "quote_amount_invalid": texts.PAYMENT_CHANGE_TARIFF_PRICE_CHANGED,
    "consumed_quote_incomplete": texts.PAYMENT_CHANGE_TARIFF_CALC_EXPIRED,
    "quote_tariff_version_invalid": texts.PAYMENT_TARIFF_UNAVAILABLE_NOTICE,
    "quote_currency_invalid": texts.PAYMENT_CHANGE_TARIFF_PRICE_CHANGED,
    "active_quote_has_existing_debit": texts.PAYMENT_OPERATION_NOT_ACTIVE_NOTICE,
    "paid_value_ledger_conflict": texts.PAYMENT_CHANGE_TARIFF_CALC_FAILED,
    "active_quote_has_existing_entitlement": texts.PAYMENT_OPERATION_NOT_ACTIVE_NOTICE,
    "change_cooldown_active": texts.PAYMENT_DOWNGRADE_COOLDOWN_ALERT,
}


def _uuid_from_callback(data: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(data.split(":", 1)[1])
    except (ValueError, IndexError, AttributeError):
        return None


def _hours_text(hours: int) -> str:
    days, remainder = divmod(hours, 24)
    return texts.TIME_DAYS_FORMAT.format(
        days=days
    ) + (
        texts.DURATION_HOURS_SUFFIX.format(hours=remainder)
        if remainder
        else ""
    )


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
            CHANGE_ERRORS.get(
                exc.code,
                texts.PAYMENT_CHANGE_TARIFF_OPEN_FAILED,
            ),
            get_back_button("payment_change_tariff"),
        )
        return
    quote = intent.quote
    due = int(quote.amount_due_rub)
    before = int(intent.balance.available)
    after = max(0, before - due)
    back = f"select_tariff:{intent.target_tariff.id}:change"
    text = texts.PAYMENT_TARIFF_CHANGE_CONFIRMATION_CARD.format(
        value_0=get_tariff_display_name(intent.target_version.device_limit),
        value_1=intent.target_version.device_limit,
        value_2=_hours_text(
            quote.resulting_paid_hours + quote.resulting_bonus_hours
        ),
        value_3=due,
        value_4=before,
        value_5=after,
    )
    if intent.shortage > 0:
        minimum = get_settings().BALANCE_MIN_TOPUP_RUB
        exact = max(int(intent.shortage), minimum)
        remainder = exact - int(intent.shortage)
        if remainder:
            text += texts.PAYMENT_TARIFF_CHANGE.format(
                value_0=int(intent.shortage),
                value_1=minimum,
                value_2=remainder,
            )
        else:
            text += texts.PAYMENT_SHORTAGE_LINE.format(
                value_0=int(intent.shortage)
            )
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
        await callback.answer(
            texts.PAYMENT_CHANGE_TARIFF_INVALID_OPERATION,
            show_alert=True,
        )
        return
    if not await MaintenanceService.can_user_perform_action(
        session, callback.from_user.id
    ):
        await _render_maintenance(callback, session, back_to="payment_change_tariff")
        return
    await render_tariff_change_review(callback, session, db_user, quote_id)


@router.callback_query(F.data.startswith("balance_change_confirm:"))
async def confirm_tariff_change(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(
        texts.PAYMENT_CHANGE_TARIFF_PROCESSING_NOTICE,
        show_alert=False,
    )
    await state.clear()
    quote_id = _uuid_from_callback(callback.data)
    if db_user is None or quote_id is None:
        return
    if not await MaintenanceService.can_user_perform_action(
        session, callback.from_user.id
    ):
        await _render_maintenance(callback, session, back_to="payment_change_tariff")
        return
    try:
        result = await settle_account_tariff_change(
            session,
            user_id=db_user.id,
            quote_public_id=quote_id,
        )
    except AccountTariffChangeError as exc:
        logger.warning(
            "AccountTariffChangeError in confirm_tariff_change: code=%s, user_id=%s, quote_public_id=%s",
            exc.code,
            db_user.id,
            quote_id,
        )
        if exc.code == "insufficient_balance":
            await render_tariff_change_review(
                callback, session, db_user, quote_id
            )
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
            CHANGE_ERRORS.get(
                exc.code,
                texts.PAYMENT_CANCELLED_NO_DEBIT_NOTICE,
            ),
            get_back_button("payment_change_tariff"),
        )
        return

    charged = abs(int(result.debit.amount)) if result.debit else 0

    intent = await get_account_tariff_change_intent(
        session,
        user_id=db_user.id,
        quote_public_id=quote_id,
    )
    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.PAYMENT_TARIFF_CHANGE_SUCCESS_CARD.format(
            value_0=get_tariff_display_name(intent.target_version.device_limit),
            value_1=_hours_text(
                result.quote.resulting_paid_hours
                + result.quote.resulting_bonus_hours
            ),
            value_2=charged,
            value_3=int(result.balance_after.real_available),
            value_4=int(result.balance_after.bonus_available),
        ),
        get_payment_success_keyboard(),
        message_effect_id=EFFECT_CONFETTI,
        force_new=True,
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
        "auto_fulfill_action": "tariff_change",
    }


@router.callback_query(F.data.startswith("bal_chg_short_exact:"))
async def topup_exact_change_shortage(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(
        texts.PAYMENT_CREATING_LINK_NOTICE,
        show_alert=False,
    )
    quote_id = _uuid_from_callback(callback.data)
    if db_user is None or quote_id is None:
        return
    try:
        intent, context = await _shortage_context(
            session, db_user, quote_id
        )
    except AccountTariffChangeError:
        await callback.answer(
            texts.PAYMENT_QUOTE_EXPIRED_RETRY_NOTICE,
            show_alert=True,
        )
        return
    if intent.shortage <= 0:
        await render_tariff_change_review(
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


@router.callback_query(F.data.startswith("bal_chg_short_custom:"))
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
    if not await MaintenanceService.can_user_perform_action(
        session, callback.from_user.id
    ):
        await state.clear()
        await _render_maintenance(callback, session, back_to="payment_change_tariff")
        return
    try:
        intent, context = await _shortage_context(
            session, db_user, quote_id
        )
    except AccountTariffChangeError:
        await callback.answer(
            texts.PAYMENT_QUOTE_EXPIRED_RETRY_NOTICE,
            show_alert=True,
        )
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
        texts.PAYMENT_CUSTOM_AMOUNT_PROMPT.format(
            value_0=minimum
        ),
        get_back_button(f"balance_change_review:{intent.quote.public_id}"),
    )
