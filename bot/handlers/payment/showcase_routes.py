import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import (
    get_back_button,
    get_balance_change_start_keyboard,
    get_balance_purchase_start_keyboard,
    get_change_tariff_keyboard,
    get_renew_keyboard,
    get_same_tariff_keyboard,
    get_tariff_duration_keyboard,
)
from database.repositories.profiles_repo import get_user_profiles_count
from database.repositories.tariffs_repo import (
    get_active_tariffs,
    get_tariff_by_id,
)
from services.account_purchase import AccountPurchaseError, prepare_account_purchase
from services.account_tariff_change import get_account_tariff_change_intent
from services.maintenance_service import MaintenanceService
from services.tariff_change_quote import create_tariff_change_quote
from utils.callbacks import parse_callback_id, parse_callback_parts
from utils.datetime_helpers import now_utc
from utils.formatters import format_datetime
from utils.tariff_names import get_tariff_display_name
from utils.telegram import render_hub

from .common import (
    _check_tariff_change_allowed,
    _get_effective_device_limit,
    _is_subscription_active,
    _render_maintenance,
    _show_hub,
    _show_showcase,
)

logger = logging.getLogger(__name__)

router = Router()


def _hours_text(hours: int) -> str:
    days, remainder = divmod(hours, 24)
    return texts.RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L46_1.format(value_0=days) + (texts.DURATION_HOURS_SUFFIX.format(hours=remainder) if remainder else "")

_START_KEYBOARD_BUILDER = InlineKeyboardBuilder()
_START_KEYBOARD_BUILDER.button(
    text=texts.UI_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L50_1, callback_data="back_to_main_menu"
)
_START_KEYBOARD_BUILDER.adjust(1)
_START_KEYBOARD = _START_KEYBOARD_BUILDER.as_markup()


@router.callback_query(F.data.in_(["menu_buy", "menu_subscription"]))
async def hub_menu_payment(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user=None,
) -> None:
    await callback.answer(show_alert=False)
    await state.clear()

    if not db_user:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.PAYMENT_USER_NOT_REGISTERED,
            _START_KEYBOARD,
        )
        return

    if not await MaintenanceService.can_user_perform_action(
        session, callback.from_user.id
    ):
        await _render_maintenance(
            callback, session, back_to="back_to_main_menu"
        )
        return

    is_active = await _is_subscription_active(db_user)

    if is_active:
        await _show_hub(callback, db_user, session)
    else:
        await _show_showcase(callback, session)


@router.callback_query(F.data == "payment_showcase")
async def show_tariff_showcase_callback(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    await callback.answer(show_alert=False)

    if not await MaintenanceService.can_user_perform_action(
        session, callback.from_user.id
    ):
        await _render_maintenance(
            callback, session, back_to="back_to_main_menu"
        )
        return

    await _show_showcase(callback, session)


@router.callback_query(F.data.startswith("select_tariff:"))
async def select_tariff(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user=None,
) -> None:
    parts = parse_callback_parts(callback.data, 2)

    if parts is None:
        await callback.answer(texts.UI_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L119_1, show_alert=True)
        return

    tariff_id = parse_callback_id(callback.data, 1)

    if tariff_id is None:
        await callback.answer(texts.UI_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L125_1, show_alert=True)
        return

    source = parts[2] if len(parts) > 2 else "showcase"

    back_to = {
        "change": "payment_change_tariff",
        "renew": "payment_quick_renew",
    }.get(source, "payment_showcase")

    if not db_user:
        await callback.answer(show_alert=False)

        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.PAYMENT_USER_NOT_REGISTERED,
            _START_KEYBOARD,
        )
        return

    if not await MaintenanceService.can_user_perform_action(
        session, callback.from_user.id
    ):
        await callback.answer(show_alert=False)
        await _render_maintenance(callback, session, back_to=back_to)
        return

    tariff = await get_tariff_by_id(session, tariff_id)

    if not tariff:
        await callback.answer(
            texts.ERROR_TARIFF_UNAVAILABLE, show_alert=True
        )
        return

    if source == "change" and db_user.current_tariff_id == tariff.id:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.UI_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L165_1,
            get_same_tariff_keyboard(),
        )
        await callback.answer(show_alert=False)
        return

    if not tariff.is_active:
        await callback.answer(
            texts.ERROR_TARIFF_UNAVAILABLE, show_alert=True
        )
        return

    device_limit = getattr(tariff, "device_limit", 2)

    if source == "change":
        quote_result = await create_tariff_change_quote(
            session,
            user_id=db_user.id,
            target_tariff_id=tariff.id,
            as_of=now_utc(),
        )
        if quote_result.failure_code:
            logger.warning(
                "Tariff change quote creation failed: user_id=%s, target_tariff_id=%s, failure_code=%s, snapshot_failure_code=%s",
                db_user.id,
                tariff.id,
                quote_result.failure_code,
                getattr(quote_result, "snapshot_failure_code", None),
            )
            errors = {
                "target_device_limit_too_small": (
                    texts.PAYMENT_DOWNGRADE_BLOCKED_PROFILES.format(
                        profiles_count=await get_user_profiles_count(
                            session, db_user.id
                        ),
                        new_limit=device_limit,
                    )
                ),
                "same_tariff_requires_renew": (
                    texts.RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L197_1
                ),
                "financial_hold": (
                    texts.RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L201_1
                ),
                "account_debt": (
                    texts.RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L204_1
                ),
                "subscription_balance_untracked": (
                    texts.RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L207_1
                ),
                "mixed_source_tariffs": (
                    texts.RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L207_1
                ),
                "target_tariff_not_found": (
                    texts.ERROR_TARIFF_UNAVAILABLE
                ),
                "target_tariff_inactive": (
                    texts.ERROR_TARIFF_UNAVAILABLE
                ),
                "user_ineligible": (
                    texts.RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L207_1
                ),
                "subscription_inactive": texts.PAYMENT_SUBSCRIPTION_INACTIVE,
                "current_tariff_unknown": texts.PAYMENT_CURRENT_TARIFF_UNKNOWN,
                "active_checkout_exists": texts.PAYMENT_ACTIVE_CHECKOUT_EXISTS,
                # NOTE: active_change_quote_exists is auto-resolved (old quote
                # is cancelled), kept as defensive fallback for race conditions.
                "active_change_quote_exists": texts.PAYMENT_ACTIVE_CHANGE_QUOTE_EXISTS,
            }
            back_button_target = (
                "payment_showcase"
                if quote_result.failure_code in {"subscription_inactive", "current_tariff_unknown"}
                else "payment_change_tariff"
            )
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                errors.get(
                    quote_result.failure_code,
                    texts.RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L216_1,
                ),
                get_same_tariff_keyboard()
                if quote_result.failure_code == "same_tariff_requires_renew"
                else get_back_button(back_button_target),
            )
            await callback.answer(show_alert=False)
            return
        intent = await get_account_tariff_change_intent(
            session,
            user_id=db_user.id,
            quote_public_id=quote_result.quote.public_id,
        )
        due = int(intent.quote.amount_due_rub)
        before = int(intent.balance.available)
        after = max(0, before - due)
        shortage = (
            texts.RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L233_1.format(value_0=int(intent.shortage))
            if intent.shortage > 0
            else ""
        )
        resulting_hours = (
            intent.quote.resulting_paid_hours
            + intent.quote.resulting_bonus_hours
        )
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.UI_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L245_1.format(value_0=get_tariff_display_name(device_limit), value_1=device_limit, value_2=_hours_text(resulting_hours), value_3=due, value_4=before, value_5=after, value_6=shortage),
            get_balance_change_start_keyboard(
                str(intent.quote.public_id), "payment_change_tariff"
            ),
        )
        await callback.answer(show_alert=False)
        return

    error_text = await _check_tariff_change_allowed(
        session, db_user, tariff
    )

    if error_text:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            error_text,
            get_back_button(back_to),
        )
        await callback.answer(show_alert=False)
        return

    tariff_name = get_tariff_display_name(device_limit)

    try:
        intent = await prepare_account_purchase(
            session, user_id=db_user.id, tariff_id=tariff.id
        )
    except AccountPurchaseError as exc:
        errors = {
            "financial_hold": texts.RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L274_1,
            "account_debt": texts.RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L275_1,
            "tariff_change_required": texts.RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L276_1,
            "active_tariff_change_quote_exists": texts.RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L277_1,
        }
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            errors.get(exc.code, texts.RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L282_1),
            get_back_button(back_to),
        )
        await callback.answer(show_alert=False)
        return

    price = int(intent.quote.amount_due_rub)
    balance_before = int(intent.balance.available)
    balance_after = max(0, balance_before - price)
    shortage_line = (
        texts.RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L292_1.format(value_0=int(intent.shortage))
        if intent.shortage > 0
        else ""
    )
    text = (
        texts.RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L297_1.format(value_0=tariff_name, value_1=tariff.duration_days, value_2=device_limit, value_3=price, value_4=balance_before, value_5=balance_after, value_6=shortage_line)
    )

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        get_balance_purchase_start_keyboard(
            str(intent.quote.public_id), back_to
        ),
    )

    await callback.answer(show_alert=False)


@router.callback_query(F.data.in_(["payment_quick_renew", "payment_renew"]))
async def show_quick_renew(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user=None,
) -> None:
    await callback.answer(show_alert=False)

    if not db_user:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.PAYMENT_USER_NOT_REGISTERED,
            _START_KEYBOARD,
        )
        return

    if not await MaintenanceService.can_user_perform_action(
        session, callback.from_user.id
    ):
        await _render_maintenance(
            callback, session, back_to="menu_subscription"
        )
        return

    tariffs = await get_active_tariffs(session)
    current_limit = await _get_effective_device_limit(session, db_user)

    renew_tariffs = [
        t
        for t in tariffs
        if getattr(t, "device_limit", 2) == current_limit
    ]

    if not renew_tariffs:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.PAYMENT_NO_TARIFFS,
            get_back_button("menu_subscription"),
        )
        return

    tariff_name = get_tariff_display_name(current_limit)

    text = texts.PAYMENT_QUICK_RENEW_HEADER.format(
        tariff_name=tariff_name,
        valid_until=format_datetime(db_user.subscription_end),
    )

    keyboard = get_renew_keyboard(renew_tariffs)

    await render_hub(
        callback.bot, callback.message.chat.id, text, keyboard
    )


@router.callback_query(F.data == "payment_change_tariff")
async def show_change_tariff(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user=None,
) -> None:
    await callback.answer(show_alert=False)

    if not db_user:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.PAYMENT_USER_NOT_REGISTERED,
            _START_KEYBOARD,
        )
        return

    if not await MaintenanceService.can_user_perform_action(
        session, callback.from_user.id
    ):
        await _render_maintenance(
            callback, session, back_to="menu_subscription"
        )
        return

    tariffs = [
        tariff
        for tariff in await get_active_tariffs(session)
        if tariff.id != getattr(db_user, "current_tariff_id", None)
    ]

    if not tariffs:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.PAYMENT_NO_TARIFFS,
            get_back_button("menu_subscription"),
        )
        return

    is_active = await _is_subscription_active(db_user)
    if not is_active:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.PAYMENT_CHANGE_TARIFF_UNAVAILABLE_NO_SUB,
            get_back_button("menu_subscription"),
        )
        return

    current_limit = await _get_effective_device_limit(session, db_user)
    tariff_name = get_tariff_display_name(current_limit)

    text = texts.PAYMENT_CHANGE_TARIFF_HEADER.format(
        tariff_name=tariff_name,
        valid_until=format_datetime(db_user.subscription_end),
    )

    current_tariff = await get_tariff_by_id(session, db_user.current_tariff_id) if getattr(db_user, "current_tariff_id", None) else None
    current_duration_days = getattr(current_tariff, "duration_days", 30) if current_tariff else 30

    keyboard = get_change_tariff_keyboard(
        tariffs,
        current_limit,
        is_subscription_active=is_active,
        current_tariff_id=db_user.current_tariff_id,
        current_duration_days=current_duration_days,
    )

    await render_hub(
        callback.bot, callback.message.chat.id, text, keyboard
    )


@router.callback_query(F.data.startswith("select_tariff_type:"))
async def select_tariff_type(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user=None,
) -> None:
    await callback.answer(show_alert=False)

    parts = parse_callback_parts(callback.data, 2)

    if parts is None:
        await callback.answer(texts.UI_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L458_1, show_alert=True)
        return

    device_limit = parse_callback_id(callback.data, 1)

    if device_limit is None:
        await callback.answer(texts.UI_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L464_1, show_alert=True)
        return

    source = parts[2] if len(parts) > 2 else "showcase"

    back_to = {
        "change": "payment_change_tariff",
        "renew": "menu_subscription",
    }.get(source, "payment_showcase")

    if not await MaintenanceService.can_user_perform_action(
        session, callback.from_user.id
    ):
        await _render_maintenance(callback, session, back_to=back_to)
        return

    if db_user:
        profiles_count = await get_user_profiles_count(
            session, db_user.id
        )

        if profiles_count > device_limit:
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                texts.PAYMENT_DOWNGRADE_BLOCKED_PROFILES.format(
                    profiles_count=profiles_count,
                    new_limit=device_limit,
                ),
                get_back_button(back_to),
            )
            return

    tariffs = await get_active_tariffs(session)

    type_tariffs = [
        t
        for t in tariffs
        if getattr(t, "device_limit", 2) == device_limit
        and not (
            source == "change"
            and t.id == getattr(db_user, "current_tariff_id", None)
        )
    ]

    if not type_tariffs:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.PAYMENT_NO_TARIFFS,
            get_back_button(back_to),
        )
        return

    description = texts.PAYMENT_TARIFF_DESCRIPTION.get(device_limit, "")
    text = description + texts.PAYMENT_DURATION_HEADER

    keyboard = get_tariff_duration_keyboard(type_tariffs, source=source)

    await render_hub(
        callback.bot, callback.message.chat.id, text, keyboard
    )
