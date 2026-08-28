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
    get_same_tariff_keyboard,
)
from bot.states import BalanceStates
from config.settings import get_settings
from database.models import TariffQuote, User
from services.account_purchase import (
    AccountPurchaseError,
    cancel_account_purchase_quote,
    get_account_purchase_intent,
    prepare_account_purchase,
    settle_account_purchase,
)
from services.maintenance_service import MaintenanceService
from utils.callbacks import parse_callback_id, parse_callback_parts
from utils.datetime_helpers import now_utc
from bot.formatters import get_tariff_display_name
from utils.telegram import EFFECT_CONFETTI, render_hub

from .balance_routes import _create_and_render_topup
from .common import _render_maintenance

router = Router()
logger = logging.getLogger(__name__)


PURCHASE_ERRORS = {
    "quote_not_found": texts.PAYMENT_QUOTE_NOT_FOUND_NOTICE,
    "quote_expired": texts.PAYMENT_PURCHASE_PRICE_STALE,
    "quote_not_active": texts.PAYMENT_OPERATION_NOT_ACTIVE_NOTICE,
    "tariff_unavailable": texts.PAYMENT_TARIFF_UNAVAILABLE_NOTICE,
    "tariff_price_changed": texts.PAYMENT_PURCHASE_PRICE_CHANGED_NOTICE,
    "quote_price_mismatch": texts.PAYMENT_PURCHASE_PRICE_EXPIRED_RETRY,
    "subscription_state_changed": texts.PAYMENT_PURCHASE_STATE_CHANGED_RETRY,
    "insufficient_balance": texts.PAYMENT_INSUFFICIENT_FUNDS_ALERT,
    "financial_hold": texts.PAYMENT_DISPUTE_BLOCKED_NOTICE,
    "account_debt": texts.PAYMENT_DEBT_BLOCKED_NOTICE,
    "too_many_devices": texts.PAYMENT_DEVICES_BLOCKED_NOTICE,
    "purchase_user_missing": texts.ERROR_USER_NOT_FOUND,
    "purchase_user_banned": texts.PAYMENT_DISPUTE_BLOCKED_NOTICE,
    "purchase_user_ineligible": texts.PAYMENT_PURCHASE_STATE_CHANGED_RETRY,
    "current_tariff_unknown": texts.PAYMENT_TARIFF_UNAVAILABLE_NOTICE,
    "tariff_change_required": texts.PAYMENT_PURCHASE_STATE_CHANGED_RETRY,
    "active_tariff_change_quote_exists": texts.PAYMENT_OPERATION_NOT_ACTIVE_NOTICE,
    "consumed_quote_incomplete": texts.PAYMENT_PURCHASE_PRICE_STALE,
    "quote_operation_mismatch": texts.PAYMENT_OPERATION_NOT_ACTIVE_NOTICE,
    "active_quote_has_existing_debit": texts.PAYMENT_OPERATION_NOT_ACTIVE_NOTICE,
    "active_quote_has_existing_entitlement": texts.PAYMENT_OPERATION_NOT_ACTIVE_NOTICE,
    "tariff_duration_not_whole_days": texts.PAYMENT_TARIFF_UNAVAILABLE_NOTICE,
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
            PURCHASE_ERRORS.get(exc.code, texts.PAYMENT_PURCHASE_OPEN_FAILED),
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
    operation = texts.PAYMENT_PURCHASE_OPERATION_RENEW_TITLE if quote.operation_type == "renew" else texts.WORD_PURCHASE
    text = texts.PAYMENT_PURCHASE_CONFIRMATION_CARD.format(
        operation_label=operation.lower(),
        tariff_name=tariff_name,
        duration_days=intent.version.duration_hours // 24,
        device_limit=intent.version.device_limit,
        price=price,
        balance_before=before,
        balance_after=after,
    )
    if intent.shortage > 0:
        minimum = get_settings().BALANCE_MIN_TOPUP_RUB
        exact = max(int(intent.shortage), minimum)
        remainder = exact - int(intent.shortage)
        if remainder:
            text += (
                texts.PAYMENT_PURCHASE.format(shortage=int(intent.shortage), minimum=minimum, remainder=remainder)
            )
        else:
            text += texts.PAYMENT_SHORTAGE_LINE.format(amount_rub=int(intent.shortage))
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
        await callback.answer(texts.PAYMENT_PURCHASE_INVALID_OPERATION, show_alert=True)
        return
    if not await MaintenanceService.can_user_perform_action(
        session, callback.from_user.id
    ):
        await _render_maintenance(callback, session, back_to="payment_showcase")
        return
    await _render_purchase_review(callback, session, db_user, quote_id)


@router.callback_query(F.data.startswith("balance_purchase_cancel:"))
async def cancel_purchase(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    quote_id = _uuid_from_callback(callback.data)
    if db_user is None or quote_id is None:
        await callback.answer(texts.PAYMENT_PURCHASE_INVALID_OPERATION, show_alert=True)
        return

    try:
        quote = await cancel_account_purchase_quote(
            session,
            user_id=db_user.id,
            quote_public_id=quote_id,
        )
        # Cancellation is a durable business transition. Commit before
        # rendering the next screen so a Telegram/rendering failure cannot
        # roll the quote back to active and recreate active_checkout_exists.
        await session.commit()
    except AccountPurchaseError as exc:
        logger.warning(
            "Balance purchase cancellation failed: code=%s, user_id=%s, quote_public_id=%s",
            exc.code,
            db_user.id,
            quote_id,
        )
        await callback.answer(
            PURCHASE_ERRORS.get(
                exc.code,
                texts.PAYMENT_PURCHASE_OPEN_FAILED,
            ),
            show_alert=True,
        )
        return
    except Exception:
        logger.exception(
            "Unexpected balance purchase cancellation failure: user_id=%s, quote_public_id=%s",
            db_user.id,
            quote_id,
        )
        await callback.answer(
            texts.PAYMENT_PURCHASE_OPEN_FAILED,
            show_alert=True,
        )
        return

    # The business transition is committed before navigation. A separate
    # callback answer removes Telegram's loading state immediately.
    await callback.answer(show_alert=False)

    if quote.operation_type == "renew":
        from .showcase_routes import show_quick_renew

        await show_quick_renew(callback, session, db_user)
    else:
        from database.models import TariffVersion
        from .showcase_routes import select_tariff_type, show_tariff_showcase_callback

        target_version_id = getattr(quote, "target_tariff_version_id", None)
        version = await session.get(TariffVersion, target_version_id) if target_version_id else None
        if version and getattr(version, "device_limit", None):
            callback.data = f"select_tariff_type:{version.device_limit}:showcase"
            await select_tariff_type(callback, session, db_user)
        else:
            await show_tariff_showcase_callback(callback, session)


@router.callback_query(F.data.startswith("balance_purchase_confirm:"))
async def confirm_purchase(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(texts.PAYMENT_PURCHASE_PROCESSING_NOTICE, show_alert=False)
    await state.clear()
    quote_id = _uuid_from_callback(callback.data)
    if db_user is None or quote_id is None:
        return
    if not await MaintenanceService.can_user_perform_action(
        session, callback.from_user.id
    ):
        await _render_maintenance(callback, session, back_to="payment_showcase")
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
                exc.code, texts.PAYMENT_CANCELLED_NO_DEBIT_NOTICE
            ),
            get_back_button("menu_subscription"),
        )
        return

    charged = abs(int(result.debit.amount)) if result.debit else 0

    intent = await get_account_purchase_intent(
        session, user_id=db_user.id, quote_public_id=quote_id
    )
    operation = (
        texts.PAYMENT_PURCHASE_RENEW_COMPLETED if result.quote.operation_type == "renew" else texts.PURCHASE_COMPLETED
    )
    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.PAYMENT_PURCHASE_SUCCESS_CARD.format(
            operation_title=operation,
            tariff_name=get_tariff_display_name(intent.version.device_limit),
            duration_days=intent.version.duration_hours // 24,
            charged=charged,
            real_balance=int(result.balance_after.real_available),
            bonus_balance=int(result.balance_after.bonus_available),
        ),
        get_payment_success_keyboard(),
        message_effect_id=EFFECT_CONFETTI,
        force_new=True,
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
        "auto_fulfill_action": "purchase",
    }


@router.callback_query(F.data.startswith("bal_short_exact:"))
async def topup_exact_shortage(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await callback.answer(
        texts.PAYMENT_CREATING_LINK_NOTICE, show_alert=False
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
            texts.PAYMENT_QUOTE_EXPIRED_RETRY_NOTICE, show_alert=True
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
    if not await MaintenanceService.can_user_perform_action(
        session, callback.from_user.id
    ):
        await state.clear()
        await _render_maintenance(callback, session, back_to="menu_balance")
        return
    try:
        intent, context = await _shortage_context(
            session, db_user, quote_id
        )
    except AccountPurchaseError:
        await callback.answer(texts.PAYMENT_QUOTE_EXPIRED_RETRY_NOTICE, show_alert=True)
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
        texts.PAYMENT_CUSTOM_AMOUNT_PROMPT.format(amount_rub=minimum),
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
    if not await MaintenanceService.can_user_perform_action(
        session, callback.from_user.id
    ):
        back_to = "payment_change_tariff" if source == "change" else "payment_showcase"
        await _render_maintenance(callback, session, back_to=back_to)
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
            if quote_result.failure_code == "same_tariff_requires_renew":
                await render_hub(
                    callback.bot,
                    callback.message.chat.id,
                    texts.PAYMENT_SHOWCASE,
                    get_same_tariff_keyboard(),
                )
                return
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                texts.PAYMENT_PURCHASE_EXPIRED_RETRY,
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
            PURCHASE_ERRORS.get(exc.code, texts.PAYMENT_PURCHASE_EXPIRED_RETRY),
            get_back_button("menu_subscription"),
        )
        return
    await _render_purchase_review(
        callback, session, db_user, intent.quote.public_id
    )
