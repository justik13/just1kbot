import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.constants import AdminAuditAction
from bot.keyboards.admin.users import (
    get_admin_confirm_action_keyboard,
    get_admin_subscription_keyboard,
)
from config.enums import WhiteInternetStatus
from database.repositories import white_internet_repo
from database.repositories.profiles_repo import get_user_profiles_count
from database.repositories.tariffs_repo import get_tariff_by_id
from database.repositories.users_repo import get_user_by_telegram_id
from services.audit_service import AuditService
from services.white_internet_service import WhiteInternetService
from utils.admin import is_admin
from utils.callbacks import parse_callback_id
from utils.datetime_helpers import now_utc
from utils.formatters import format_datetime
from bot.formatters import get_tariff_display_name

from .common import (
    _format_time_left,
    _get_white_internet_card_info,
    _is_subscription_active,
)

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("admin_subscription:"))
async def admin_subscription_menu(
    callback: CallbackQuery,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    telegram_id = parse_callback_id(callback.data, 1)

    if telegram_id is None:
        await callback.answer(
            texts.ERROR_INVALID_REQUEST,
            show_alert=True,
        )
        return

    try:
        await callback.answer(show_alert=False)
    except Exception:
        pass

    user = await get_user_by_telegram_id(session, telegram_id)

    if not user:
        await callback.message.edit_text(
            texts.ERROR_USER_NOT_FOUND
        )
        return

    has_active = _is_subscription_active(user)

    profiles_count = await get_user_profiles_count(
        session,
        user.id,
    )

    tariff_name = texts.PLACEHOLDER_DASH
    device_limit = user.device_limit or 0

    if user.current_tariff_id:
        tariff = await get_tariff_by_id(
            session,
            user.current_tariff_id,
        )

        if tariff:
            device_limit = tariff.device_limit

            tariff_name = (
                texts.ADMIN_SUB_MENU_DEVICE_COUNT_FORMAT.format(v0=get_tariff_display_name(device_limit), v1=device_limit)
            )

    if has_active:
        status_block = texts.ADMIN_SUB_STATUS_ACTIVE.format(
            tariff_name=tariff_name,
            valid_until=format_datetime(user.subscription_end),
            time_left=_format_time_left(user.subscription_end),
            devices_count=profiles_count,
            device_limit=device_limit,
        )

    elif user.subscription_end:
        status_block = texts.ADMIN_SUB_STATUS_INACTIVE.format(
            tariff_name=tariff_name,
            valid_until=format_datetime(user.subscription_end),
        )

    else:
        status_block = texts.ADMIN_SUB_STATUS_NONE.format(
            devices_count=profiles_count,
        )

    has_wl_sub = await white_internet_repo.has_user_any_subscription(session, user.id)
    wl_sub = await white_internet_repo.get_subscription_by_user_id(session, user.id)
    now = now_utc()
    wl_is_active = bool(
        wl_sub
        and wl_sub.status in (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.PENDING)
        and (wl_sub.expires_at is None or wl_sub.expires_at > now)
    )

    text = texts.ADMIN_SUBSCRIPTION_HEADER.format(
        telegram_id=telegram_id,
        status_block=status_block,
    )
    wl_info = await _get_white_internet_card_info(session, user.id)
    if wl_info:
        text = f"{text}\n\n{wl_info}"

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_subscription_keyboard(
                telegram_id,
                has_active,
                has_wl_sub=has_wl_sub,
                wl_is_active=wl_is_active,
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(
            f"admin_subscription_menu edit_text failed: {e}"
        )


@router.callback_query(F.data.startswith("admin_wl_reset_confirm:"))
async def admin_wl_reset_confirm(
    callback: CallbackQuery,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    telegram_id = parse_callback_id(callback.data, 1)
    if telegram_id is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    await callback.answer(show_alert=False)

    text = texts.ADMIN_WL_RESET_CONFIRM.format(telegram_id=telegram_id)
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_confirm_action_keyboard(
                confirm_callback=f"admin_wl_reset_apply:{telegram_id}",
                cancel_callback=f"admin_subscription:{telegram_id}",
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug("admin_wl_reset_confirm edit_text failed: %s", e)


@router.callback_query(F.data.startswith("admin_wl_reset_apply:"))
async def admin_wl_reset_apply(
    callback: CallbackQuery,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    telegram_id = parse_callback_id(callback.data, 1)
    if telegram_id is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    ok, msg = await WhiteInternetService.reset_user_trial(session, user.id)
    if not ok:
        await callback.answer(msg, show_alert=True)
        return

    await AuditService.log_action(
        session,
        admin_id=callback.from_user.id,
        action=AdminAuditAction.WHITE_INTERNET_RESET_TRIAL,
        target_type="user",
        target_id=user.id,
        details={"telegram_id": telegram_id},
    )

    await callback.answer(texts.ADMIN_WL_RESET_SUCCESS, show_alert=True)
    callback.data = f"admin_subscription:{telegram_id}"
    await admin_subscription_menu(callback, session)


@router.callback_query(F.data.startswith("admin_wl_grant_trial:"))
async def admin_wl_grant_trial(
    callback: CallbackQuery,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    telegram_id = parse_callback_id(callback.data, 1)
    if telegram_id is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    ok, msg, sub = await WhiteInternetService.create_trial_subscription(session, user.id)
    if not ok:
        await callback.answer(texts.ADMIN_WL_GRANT_FAILED.format(error=msg), show_alert=True)
        return

    await AuditService.log_action(
        session,
        admin_id=callback.from_user.id,
        action=AdminAuditAction.WHITE_INTERNET_GRANT_TRIAL,
        target_type="user",
        target_id=user.id,
        details={"telegram_id": telegram_id, "subscription_id": sub.id if sub else None},
    )

    await callback.answer(texts.ADMIN_WL_GRANT_SUCCESS, show_alert=True)
    callback.data = f"admin_subscription:{telegram_id}"
    await admin_subscription_menu(callback, session)