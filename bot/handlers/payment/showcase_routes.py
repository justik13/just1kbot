import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import (
    get_back_button,
    get_balance_purchase_start_keyboard,
    get_change_tariff_keyboard,
    get_downgrade_blocked_keyboard,
    get_renew_keyboard,
    get_same_tariff_keyboard,
    get_tariff_change_options_keyboard,
    get_tariff_duration_keyboard,
)
from database.repositories.profiles_repo import (
    get_user_effective_device_count,
)
from database.repositories.tariffs_repo import (
    get_active_tariffs,
    get_tariff_by_id,
)
from services.account_purchase import AccountPurchaseError, prepare_account_purchase
from services.maintenance_service import MaintenanceService
from services.tariff_change_quote import (
    calculate_tariff_change_options,
)
from utils.callbacks import parse_callback_id, parse_callback_parts
from utils.datetime_helpers import now_utc
from bot.formatters import (
    format_plural,
    format_subscription_date,
    get_tariff_display_name,
    get_tariff_group_name,
)
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

# Compatibility alias for legacy tests
get_user_profiles_count = get_user_effective_device_count

router = Router()


def _hours_text(hours: int) -> str:
    days, remainder = divmod(hours, 24)
    return texts.TIME_DAYS_FORMAT.format(days=days) + (texts.DURATION_HOURS_SUFFIX.format(hours=remainder) if remainder else "")

_START_KEYBOARD_BUILDER = InlineKeyboardBuilder()
_START_KEYBOARD_BUILDER.button(
    text=texts.BTN_PAYMENT_START_ONBOARDING, callback_data="back_to_main_menu"
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
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    tariff_id = parse_callback_id(callback.data, 1)

    if tariff_id is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
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

    if not tariff.is_active:
        await callback.answer(
            texts.ERROR_TARIFF_UNAVAILABLE, show_alert=True
        )
        return

    current_limit = await _get_effective_device_limit(session, db_user)
    if source == "change" and (
        db_user.current_tariff_id == tariff.id
        or getattr(tariff, "device_limit", None) == current_limit
    ):
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.PAYMENT_SHOWCASE,
            get_same_tariff_keyboard(),
        )
        await callback.answer(show_alert=False)
        return

    device_limit = getattr(tariff, "device_limit", 2)

    if source == "change":
        effective_devices = await get_user_effective_device_count(
            session, db_user.id, getattr(db_user, "active_sub_devices", None)
        )
        if effective_devices > tariff.device_limit:
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                texts.PAYMENT_DOWNGRADE_BLOCKED_PROFILES.format(
                    profiles_count=effective_devices,
                    new_limit=tariff.device_limit,
                ),
                get_downgrade_blocked_keyboard(),
            )
            await callback.answer(show_alert=False)
            return

        if db_user.subscription_end is None or db_user.subscription_end <= now_utc():
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                texts.PAYMENT_SUBSCRIPTION_INACTIVE,
                get_back_button("payment_showcase"),
            )
            await callback.answer(show_alert=False)
            return

        options = await calculate_tariff_change_options(
            session,
            user=db_user,
            target_tariff=tariff,
            as_of=now_utc(),
        )
        text = texts.PAYMENT_TARIFF_CHANGE_OPTIONS_HEADER.format(
            source_tariff_name=options.source_tariff_name,
            remaining_days=options.remaining_days,
            remaining_value=options.remaining_value,
            target_tariff_name=options.target_tariff_name,
            target_device_limit=options.target_device_limit,
        )
        keyboard = get_tariff_change_options_keyboard(
            target_tariff_id=tariff.id,
            option1_available=options.option1_available,
            option1_days=options.option1_days,
            option2_1m_surcharge=options.option2_1m_surcharge,
            option2_1m_tariff_id=options.option2_1m_tariff_id,
            option2_3m_surcharge=options.option2_3m_surcharge,
            option2_3m_tariff_id=options.option2_3m_tariff_id,
            back_callback="payment_change_tariff",
        )
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            text,
            keyboard,
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
            "financial_hold": texts.PAYMENT_DISPUTE_BLOCKED_NOTICE,
            "account_debt": texts.PAYMENT_DEBT_BLOCKED_NOTICE,
            "tariff_change_required": texts.PAYMENT_SHOWCASE_USE_CHANGE_SECTION,
            "active_tariff_change_quote_exists": texts.PAYMENT_CHANGE_TARIFF_IN_PROGRESS_NOTICE,
        }
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            errors.get(exc.code, texts.PAYMENT_SHOWCASE_PREPARE_FAILED),
            get_back_button(back_to),
        )
        await callback.answer(show_alert=False)
        return

    price = int(intent.quote.amount_due_rub)
    balance_before = int(intent.balance.available)
    balance_after = max(0, balance_before - price)
    shortage_line = (
        texts.PAYMENT_SHORTAGE_WARNING.format(amount_rub=int(intent.shortage))
        if intent.shortage > 0
        else ""
    )
    text = (
        texts.PAYMENT_SHOWCASE_ORDER_CARD.format(tariff_label=tariff_name, days=tariff.duration_days, device_limit=device_limit, price=price, balance_before=balance_before, balance_after=balance_after, shortage_line=shortage_line)
    )

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        get_balance_purchase_start_keyboard(
            str(intent.quote.public_id), back_to, is_shortage=intent.shortage > 0
        ),
    )

    await callback.answer(show_alert=False)


async def render_quick_renew(
    bot,
    chat_id: int,
    session: AsyncSession,
    db_user=None,
) -> None:
    if not db_user:
        await render_hub(
            bot,
            chat_id,
            texts.PAYMENT_USER_NOT_REGISTERED,
            _START_KEYBOARD,
        )
        return

    user_tg_id = getattr(db_user, "telegram_id", None) or chat_id
    if not await MaintenanceService.can_user_perform_action(
        session, user_tg_id
    ):
        message = await MaintenanceService.get_message(session) or texts.MAINTENANCE_DEFAULT_MESSAGE
        await render_hub(
            bot,
            chat_id,
            message,
            get_back_button("menu_subscription"),
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
            bot,
            chat_id,
            texts.PAYMENT_NO_TARIFFS,
            get_back_button("menu_subscription"),
        )
        return

    tariff_name = get_tariff_display_name(current_limit)

    text = texts.PAYMENT_QUICK_RENEW_HEADER.format(
        tariff_name=tariff_name,
        valid_until=format_subscription_date(db_user.subscription_end),
    )

    keyboard = get_renew_keyboard(renew_tariffs)

    await render_hub(
        bot, chat_id, text, keyboard
    )


@router.callback_query(F.data.in_(["payment_quick_renew", "payment_renew"]))
async def show_quick_renew(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user=None,
) -> None:
    await callback.answer(show_alert=False)
    await render_quick_renew(
        callback.bot,
        callback.message.chat.id,
        session,
        db_user,
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
        valid_until=format_subscription_date(db_user.subscription_end),
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


async def render_tariff_duration_selection(
    bot,
    chat_id: int,
    session: AsyncSession,
    db_user,
    device_limit: int,
    source: str = "showcase",
) -> None:
    back_to = {
        "change": "payment_change_tariff",
        "renew": "menu_subscription",
    }.get(source, "payment_showcase")

    user_tg_id = getattr(db_user, "telegram_id", None) or chat_id
    if not await MaintenanceService.can_user_perform_action(
        session, user_tg_id
    ):
        message = await MaintenanceService.get_message(session) or texts.MAINTENANCE_DEFAULT_MESSAGE
        await render_hub(bot, chat_id, message, get_back_button(back_to))
        return

    if db_user:
        effective_devices = await get_user_profiles_count(
            session, db_user.id
        )

        if effective_devices > device_limit:
            await render_hub(
                bot,
                chat_id,
                texts.PAYMENT_DOWNGRADE_BLOCKED_PROFILES.format(
                    profiles_count=format_plural(effective_devices, texts.NOUN_DEVICES),
                    new_limit=format_plural(device_limit, texts.NOUN_DEVICES),
                ),
                get_downgrade_blocked_keyboard(back_to=back_to),
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
            bot,
            chat_id,
            texts.PAYMENT_NO_TARIFFS,
            get_back_button(back_to),
        )
        return

    description = f"<b>{get_tariff_group_name(device_limit)}</b>\n\n"
    text = description + texts.PAYMENT_DURATION_HEADER

    keyboard = get_tariff_duration_keyboard(type_tariffs, source=source)

    await render_hub(
        bot, chat_id, text, keyboard
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
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    device_limit = parse_callback_id(callback.data, 1)

    if device_limit is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    source = parts[2] if len(parts) > 2 else "showcase"
    await render_tariff_duration_selection(
        callback.bot,
        callback.message.chat.id,
        session,
        db_user,
        device_limit,
        source=source,
    )
