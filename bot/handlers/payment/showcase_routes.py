from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import (
    get_back_button,
    get_balance_purchase_start_keyboard,
    get_change_tariff_keyboard,
    get_renew_keyboard,
    get_tariff_duration_keyboard,
)
from database.repositories.profiles_repo import get_user_profiles_count
from database.repositories.tariffs_repo import (
    get_active_tariffs,
    get_tariff_by_id,
)
from services.maintenance_service import MaintenanceService
from services.account_purchase import AccountPurchaseError, prepare_account_purchase
from utils.callbacks import parse_callback_id, parse_callback_parts
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

router = Router()

_START_KEYBOARD_BUILDER = InlineKeyboardBuilder()
_START_KEYBOARD_BUILDER.button(
    text="🚀 Начать", callback_data="back_to_main_menu"
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
        await callback.answer("Некорректный запрос", show_alert=True)
        return

    tariff_id = parse_callback_id(callback.data, 1)

    if tariff_id is None:
        await callback.answer("Некорректный запрос", show_alert=True)
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

    if not tariff or not tariff.is_active:
        await callback.answer(
            texts.ERROR_TARIFF_UNAVAILABLE, show_alert=True
        )
        return

    device_limit = getattr(tariff, "device_limit", 2)

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
            "financial_hold": "Покупки временно заблокированы из-за открытого финансового спора.",
            "account_debt": "Покупки недоступны до погашения задолженности.",
            "tariff_change_required": "Для этого варианта используйте раздел «Сменить тариф».",
            "active_tariff_change_quote_exists": "Сначала завершите или отмените смену тарифа.",
            "legacy_checkout_in_progress": "Сначала завершите ранее созданный платёж.",
        }
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            errors.get(exc.code, "Не удалось подготовить покупку. Попробуйте ещё раз."),
            get_back_button(back_to),
        )
        await callback.answer(show_alert=False)
        return

    price = int(intent.quote.confirmed_payment_required_rub)
    balance_before = int(intent.balance.available)
    balance_after = max(0, balance_before - price)
    shortage_line = (
        f"\n⚠️ Не хватает: <b>{int(intent.shortage)} ₽</b>"
        if intent.shortage > 0
        else ""
    )
    text = (
        "💳 <b>Оформление заказа</b>\n\n"
        f"📦 Тариф: <b>{tariff_name}</b>\n"
        f"⏱ Срок: {tariff.duration_days} дней\n"
        f"🔌 Устройства: до {device_limit}\n"
        f"💰 Цена: <b>{price} ₽</b>\n\n"
        f"Баланс: <b>{balance_before} ₽</b>\n"
        f"После покупки: <b>{balance_after} ₽</b>"
        f"{shortage_line}\n\n"
        "Покупка выполняется только после отдельного подтверждения."
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

    tariffs = await get_active_tariffs(session)

    if not tariffs:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.PAYMENT_NO_TARIFFS,
            get_back_button("menu_subscription"),
        )
        return

    current_limit = await _get_effective_device_limit(session, db_user)
    tariff_name = get_tariff_display_name(current_limit)
    is_active = await _is_subscription_active(db_user)

    text = texts.PAYMENT_CHANGE_TARIFF_HEADER.format(
        tariff_name=tariff_name,
        valid_until=format_datetime(db_user.subscription_end),
    )

    keyboard = get_change_tariff_keyboard(
        tariffs, current_limit, is_subscription_active=is_active
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
        await callback.answer("Некорректный запрос", show_alert=True)
        return

    device_limit = parse_callback_id(callback.data, 1)

    if device_limit is None:
        await callback.answer("Некорректный запрос", show_alert=True)
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
        is_active = await _is_subscription_active(db_user)

        if is_active:
            current_limit = await _get_effective_device_limit(
                session, db_user
            )

            if device_limit < current_limit:
                await render_hub(
                    callback.bot,
                    callback.message.chat.id,
                    texts.PAYMENT_DOWNGRADE_BLOCKED.format(
                        current_limit=current_limit,
                        new_limit=device_limit,
                        valid_until=format_datetime(
                            db_user.subscription_end
                        ),
                    ),
                    get_back_button(back_to),
                )
                return

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
