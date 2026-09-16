from datetime import datetime, timedelta
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.constants import PERMANENT_END_DATE, PERMANENT_SUBSCRIPTION_DAYS
from bot.formatters import get_tariff_display_name
from bot.keyboards import get_back_button
from bot.keyboards.admin.users import (
    get_admin_awg_subscription_keyboard,
    get_admin_confirm_action_keyboard,
    get_admin_wi_device_limit_keyboard,
    get_admin_wi_devices_keyboard,
    get_admin_wi_extend_days_keyboard,
    get_admin_wi_quota_keyboard,
    get_admin_wi_subscription_keyboard,
    get_admin_wi_traffic_add_keyboard,
)
from bot.states import AdminStates
from config.constants import WHITE_INTERNET_HWID_TTL_HOURS
from config.enums import AdminAuditAction, WhiteInternetStatus
from database.repositories import white_internet_repo
from database.repositories.white_internet_repo import count_active_hwids
from database.repositories.idempotency_repo import (
    check_and_record_admin_op,
    make_admin_op_key,
)
from database.repositories.profiles_repo import get_user_profiles_count
from database.repositories.tariffs_repo import get_tariff_by_id
from database.repositories.users_repo import get_user_by_telegram_id
from services.audit_service import AuditService
from services.white_internet_service import WhiteInternetService
from utils.admin import is_admin
from utils.callbacks import parse_callback_id, parse_callback_int, parse_callback_parts
from utils.datetime_helpers import now_utc
from utils.formatters import format_datetime
from utils.telegram import render_hub, safe

from .common import (
    _format_time_left,
    _get_white_internet_card_info,
    _is_subscription_active,
    _validate_positive_int,
)

router = Router()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AmneziaWG Submenu
# ---------------------------------------------------------------------------


@router.callback_query(
    F.data.startswith("admin_sub_awg_menu:") | F.data.startswith("admin_subscription:")
)
async def admin_subscription_menu(
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

    try:
        await callback.answer(show_alert=False)
    except Exception:
        pass

    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        try:
            await callback.message.edit_text(texts.ERROR_USER_NOT_FOUND)
        except TelegramBadRequest:
            pass
        return

    has_active = _is_subscription_active(user)
    profiles_count = await get_user_profiles_count(session, user.id)

    tariff_name = texts.PLACEHOLDER_DASH
    device_limit = user.device_limit or 0

    if user.current_tariff_id:
        tariff = await get_tariff_by_id(session, user.current_tariff_id)
        if tariff:
            device_limit = tariff.device_limit
            tariff_name = texts.ADMIN_SUB_MENU_DEVICE_COUNT_FORMAT.format(
                v0=get_tariff_display_name(device_limit), v1=device_limit
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

    text = texts.ADMIN_SUBSCRIPTION_HEADER.format(
        telegram_id=telegram_id,
        status_block=status_block,
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_awg_subscription_keyboard(telegram_id, has_active),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"admin_subscription_menu edit_text failed: {e}")


def _safe_update_callback_data(callback: CallbackQuery, new_data: str) -> CallbackQuery:
    """Safely update callback data on frozen Pydantic models or mocks."""
    object.__setattr__(callback, "data", new_data)
    return callback


# ---------------------------------------------------------------------------
# White Internet Submenu
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("admin_sub_wi_menu:"))
async def admin_wi_subscription_menu(
    callback: CallbackQuery,
    session: AsyncSession,
    target_telegram_id: int | None = None,
    state: FSMContext | None = None,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    if state is not None:
        await state.clear()

    telegram_id = (
        target_telegram_id
        if target_telegram_id is not None
        else parse_callback_id(callback.data, 1)
    )
    if telegram_id is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    try:
        await callback.answer(show_alert=False)
    except Exception:
        pass

    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    wi_sub = await white_internet_repo.get_subscription_by_user_id(session, user.id)
    has_wi_sub = wi_sub is not None
    now = now_utc()
    wi_is_active = bool(
        wi_sub
        and wi_sub.status in (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.PENDING)
        and (wi_sub.expires_at is None or wi_sub.expires_at > now)
    )

    header = texts.ADMIN_WI_MENU_TITLE.format(telegram_id=telegram_id)
    wi_info = await _get_white_internet_card_info(session, user.id, sub=wi_sub)
    if not wi_info:
        body = texts.ADMIN_WI_MENU_NO_SUB
    else:
        body = wi_info

    text = f"{header}\n\n{body}"

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_wi_subscription_keyboard(
                telegram_id,
                has_wi_sub=has_wi_sub,
                wi_is_active=wi_is_active,
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"admin_wi_subscription_menu edit_text failed: {e}")


# ---------------------------------------------------------------------------
# White Internet: Traffic Add
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("admin_wi_traffic_add_menu:"))
async def admin_wi_traffic_add_menu(
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

    text = texts.ADMIN_WI_TRAFFIC_ADD_MENU_TITLE.format(telegram_id=telegram_id)

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_wi_traffic_add_keyboard(telegram_id),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"admin_wi_traffic_add_menu edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_wi_traffic_add:"))
async def admin_wi_traffic_add(
    callback: CallbackQuery,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    telegram_id = int(parts[1])
    gb = int(parts[2])

    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    wi_sub = await white_internet_repo.get_subscription_by_user_id(session, user.id)
    if not wi_sub:
        await callback.answer(texts.ADMIN_WI_SUB_NOT_FOUND, show_alert=True)
        return

    message = getattr(callback, "message", None)
    chat_id = getattr(getattr(message, "chat", None), "id", callback.from_user.id) if message else callback.from_user.id
    message_id = getattr(message, "message_id", 0) if message else 0
    op_key = make_admin_op_key(
        action="wi_traffic_add",
        admin_id=callback.from_user.id,
        target_id=user.id,
        chat_id=chat_id,
        message_id=message_id,
        value=gb,
    )
    is_new = await check_and_record_admin_op(
        session,
        op_key=op_key,
        admin_id=callback.from_user.id,
        target_id=user.id,
    )
    if not is_new:
        await callback.answer(texts.ADMIN_BALANCE_OP_ALREADY_PROCESSED, show_alert=True)
        return

    extra_bytes = gb * 1024 * 1024 * 1024
    try:
        await white_internet_repo.add_extra_traffic_atomic(session, wi_sub.id, extra_bytes)
    except white_internet_repo.WhiteInternetError as e:
        await session.rollback()
        await callback.answer(texts.ADMIN_WI_ACTION_FAILED.format(error=str(e)), show_alert=True)
        return

    await AuditService.log_action(
        session,
        admin_id=callback.from_user.id,
        action=AdminAuditAction.WHITE_INTERNET_TRAFFIC_ADDED,
        target_type="user",
        target_id=user.id,
        details={"telegram_id": telegram_id, "extra_gb": gb, "extra_bytes": extra_bytes},
    )

    await callback.answer(texts.ADMIN_WI_TRAFFIC_ADDED_SUCCESS.format(gb=gb), show_alert=True)
    callback = _safe_update_callback_data(callback, f"admin_sub_wi_menu:{telegram_id}")
    await admin_wi_subscription_menu(callback, session, target_telegram_id=telegram_id)


# ---------------------------------------------------------------------------
# White Internet: Reset Used Traffic to 0
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("admin_wi_traffic_reset_confirm:"))
async def admin_wi_traffic_reset_confirm(
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

    text = texts.ADMIN_WI_TRAFFIC_RESET_CONFIRM_TITLE.format(telegram_id=telegram_id)

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_confirm_action_keyboard(
                confirm_callback=f"admin_wi_traffic_reset_apply:{telegram_id}",
                cancel_callback=f"admin_sub_wi_menu:{telegram_id}",
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"admin_wi_traffic_reset_confirm edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_wi_traffic_reset_apply:"))
async def admin_wi_traffic_reset_apply(
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

    wi_sub = await white_internet_repo.get_subscription_by_user_id(session, user.id)
    if not wi_sub:
        await callback.answer(texts.ADMIN_WI_SUB_NOT_FOUND, show_alert=True)
        return

    try:
        await white_internet_repo.reset_traffic_used_atomic(session, wi_sub.id)
    except white_internet_repo.WhiteInternetError as e:
        await callback.answer(texts.ADMIN_WI_TRAFFIC_RESET_FAILED.format(error=str(e)), show_alert=True)
        return

    await AuditService.log_action(
        session,
        admin_id=callback.from_user.id,
        action=AdminAuditAction.WHITE_INTERNET_TRAFFIC_RESET,
        target_type="user",
        target_id=user.id,
        details={"telegram_id": telegram_id, "subscription_id": wi_sub.id},
    )

    await callback.answer(texts.ADMIN_WI_TRAFFIC_RESET_SUCCESS, show_alert=True)
    callback = _safe_update_callback_data(callback, f"admin_sub_wi_menu:{telegram_id}")
    await admin_wi_subscription_menu(callback, session, target_telegram_id=telegram_id)


# ---------------------------------------------------------------------------
# White Internet: Base Traffic Quota
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("admin_wi_quota_menu:"))
async def admin_wi_quota_menu(
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

    text = texts.ADMIN_WI_QUOTA_MENU_TITLE.format(telegram_id=telegram_id)

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_wi_quota_keyboard(telegram_id),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"admin_wi_quota_menu edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_wi_quota_set:"))
async def admin_wi_quota_set(
    callback: CallbackQuery,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    telegram_id = int(parts[1])
    gb = int(parts[2])

    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    wi_sub = await white_internet_repo.get_subscription_by_user_id(session, user.id)
    if not wi_sub:
        await callback.answer(texts.ADMIN_WI_SUB_NOT_FOUND, show_alert=True)
        return

    quota_bytes = gb * 1024 * 1024 * 1024
    try:
        await white_internet_repo.set_base_traffic_quota_atomic(session, wi_sub.id, quota_bytes)
    except white_internet_repo.WhiteInternetError as e:
        await session.rollback()
        await callback.answer(texts.ADMIN_WI_ACTION_FAILED.format(error=str(e)), show_alert=True)
        return

    await AuditService.log_action(
        session,
        admin_id=callback.from_user.id,
        action=AdminAuditAction.WHITE_INTERNET_QUOTA_SET,
        target_type="user",
        target_id=user.id,
        details={"telegram_id": telegram_id, "quota_gb": gb, "quota_bytes": quota_bytes},
    )

    await callback.answer(texts.ADMIN_WI_QUOTA_SET_SUCCESS.format(gb=gb), show_alert=True)
    callback = _safe_update_callback_data(callback, f"admin_sub_wi_menu:{telegram_id}")
    await admin_wi_subscription_menu(callback, session, target_telegram_id=telegram_id)


# ---------------------------------------------------------------------------
# White Internet: Device Limit
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("admin_wi_devlimit_menu:"))
async def admin_wi_devlimit_menu(
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

    text = texts.ADMIN_WI_DEVLIMIT_MENU_TITLE.format(telegram_id=telegram_id)

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_wi_device_limit_keyboard(telegram_id),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"admin_wi_devlimit_menu edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_wi_devlimit_set:"))
async def admin_wi_devlimit_set(
    callback: CallbackQuery,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    telegram_id = int(parts[1])
    limit = int(parts[2])

    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    wi_sub = await white_internet_repo.get_subscription_by_user_id(session, user.id)
    if not wi_sub:
        await callback.answer(texts.ADMIN_WI_SUB_NOT_FOUND, show_alert=True)
        return

    try:
        await white_internet_repo.set_device_limit_atomic(session, wi_sub.id, limit)
    except white_internet_repo.WhiteInternetError as e:
        await session.rollback()
        await callback.answer(texts.ADMIN_WI_ACTION_FAILED.format(error=str(e)), show_alert=True)
        return

    await AuditService.log_action(
        session,
        admin_id=callback.from_user.id,
        action=AdminAuditAction.WHITE_INTERNET_DEVLIMIT_SET,
        target_type="user",
        target_id=user.id,
        details={"telegram_id": telegram_id, "device_limit": limit},
    )

    await callback.answer(texts.ADMIN_WI_DEVLIMIT_SET_SUCCESS.format(limit=limit), show_alert=True)
    callback = _safe_update_callback_data(callback, f"admin_sub_wi_menu:{telegram_id}")
    await admin_wi_subscription_menu(callback, session, target_telegram_id=telegram_id)


# ---------------------------------------------------------------------------
# White Internet: Reset Active HWIDs
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("admin_wi_hwid_reset_confirm:"))
async def admin_wi_hwid_reset_confirm(
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

    text = texts.ADMIN_WI_HWID_RESET_CONFIRM_TITLE.format(telegram_id=telegram_id)

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_confirm_action_keyboard(
                confirm_callback=f"admin_wi_hwid_reset_apply:{telegram_id}",
                cancel_callback=f"admin_sub_wi_menu:{telegram_id}",
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"admin_wi_hwid_reset_confirm edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_wi_hwid_reset_apply:"))
async def admin_wi_hwid_reset_apply(
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

    wi_sub = await white_internet_repo.get_subscription_by_user_id(session, user.id)
    if not wi_sub:
        await callback.answer(texts.ADMIN_WI_SUB_NOT_FOUND, show_alert=True)
        return

    try:
        await white_internet_repo.reset_active_hwids_atomic(
            session, wi_sub.id, cooldown_seconds=0
        )
    except white_internet_repo.WhiteInternetError as e:
        await callback.answer(texts.ADMIN_WI_ACTION_FAILED.format(error=str(e)), show_alert=True)
        return

    await AuditService.log_action(
        session,
        admin_id=callback.from_user.id,
        action=AdminAuditAction.WHITE_INTERNET_HWID_RESET,
        target_type="user",
        target_id=user.id,
        details={"telegram_id": telegram_id, "subscription_id": wi_sub.id},
    )

    await callback.answer(texts.ADMIN_WI_HWID_RESET_SUCCESS, show_alert=True)
    callback = _safe_update_callback_data(callback, f"admin_sub_wi_menu:{telegram_id}")
    await admin_wi_subscription_menu(callback, session, target_telegram_id=telegram_id)


# ---------------------------------------------------------------------------
# White Internet: Trial Reset & Grant
# ---------------------------------------------------------------------------


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

    try:
        await callback.answer(show_alert=False)
    except Exception:
        pass

    text = texts.ADMIN_WL_RESET_CONFIRM.format(telegram_id=telegram_id)
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_confirm_action_keyboard(
                confirm_callback=f"admin_wl_reset_apply:{telegram_id}",
                cancel_callback=f"admin_sub_wi_menu:{telegram_id}",
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

    try:
        await callback.answer(texts.ADMIN_WL_RESET_SUCCESS, show_alert=True)
    except Exception:
        pass
    callback = _safe_update_callback_data(callback, f"admin_sub_wi_menu:{telegram_id}")
    await admin_wi_subscription_menu(callback, session, target_telegram_id=telegram_id)


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

    try:
        await callback.answer(texts.ADMIN_WL_GRANT_SUCCESS, show_alert=True)
    except Exception:
        pass
    callback = _safe_update_callback_data(callback, f"admin_sub_wi_menu:{telegram_id}")
    await admin_wi_subscription_menu(callback, session, target_telegram_id=telegram_id)


# ---------------------------------------------------------------------------
# White Internet: Subscription Extension
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("admin_wi_extend_menu:"))
async def admin_wi_extend_menu(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext | None = None,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    if state is not None:
        await state.clear()

    telegram_id = parse_callback_id(callback.data, 1)
    if telegram_id is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    wi_sub = await white_internet_repo.get_subscription_by_user_id(session, user.id)
    if not wi_sub:
        await callback.answer(texts.ADMIN_WI_SUB_NOT_FOUND, show_alert=True)
        return

    await callback.answer(show_alert=False)

    valid_until = format_datetime(wi_sub.expires_at) if wi_sub.expires_at else texts.PLACEHOLDER_DASH
    text = texts.ADMIN_WI_EXTEND_HEADER.format(
        telegram_id=telegram_id,
        valid_until=valid_until,
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_wi_extend_days_keyboard(telegram_id),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug("admin_wi_extend_menu edit_text failed: %s", e)


@router.callback_query(F.data.startswith("admin_wi_confirm_extend:"))
async def admin_wi_confirm_extend(
    callback: CallbackQuery,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    parts = parse_callback_parts(callback.data, 3)
    if parts is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    telegram_id = parse_callback_int(parts, 1)
    days = parse_callback_int(parts, 2)

    if (
        telegram_id is None
        or days is None
        or days < 1
        or days > PERMANENT_SUBSCRIPTION_DAYS
    ):
        await callback.answer(texts.ERROR_INVALID_DAYS_COUNT, show_alert=True)
        return

    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    wi_sub = await white_internet_repo.get_subscription_by_user_id(session, user.id)
    if not wi_sub:
        await callback.answer(texts.ADMIN_WI_SUB_NOT_FOUND, show_alert=True)
        return

    await callback.answer(show_alert=False)

    current_time = now_utc()
    current_end = (
        wi_sub.expires_at
        if (wi_sub.expires_at and wi_sub.expires_at > current_time)
        else current_time
    )

    new_end = (
        PERMANENT_END_DATE
        if days >= PERMANENT_SUBSCRIPTION_DAYS
        else current_end + timedelta(days=days)
    )

    days_text = (
        texts.ADMIN_SUB_PERMANENT_LABEL
        if days >= PERMANENT_SUBSCRIPTION_DAYS
        else texts.TIME_DAYS_FORMAT.format(days=days)
    )

    text = texts.ADMIN_WI_CONFIRM_EXTEND.format(
        telegram_id=telegram_id,
        days_text=days_text,
        new_end=format_datetime(new_end),
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_confirm_action_keyboard(
                confirm_callback=f"admin_wi_apply_extend:{telegram_id}:{days}",
                cancel_callback=f"admin_wi_extend_menu:{telegram_id}",
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug("admin_wi_confirm_extend edit_text failed: %s", e)


@router.callback_query(F.data.startswith("admin_wi_apply_extend:"))
async def admin_wi_apply_extend(
    callback: CallbackQuery,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    parts = parse_callback_parts(callback.data, 3)
    if parts is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    telegram_id = parse_callback_int(parts, 1)
    days = parse_callback_int(parts, 2)

    if (
        telegram_id is None
        or days is None
        or days < 1
        or days > PERMANENT_SUBSCRIPTION_DAYS
    ):
        await callback.answer(texts.ERROR_INVALID_DAYS_COUNT, show_alert=True)
        return

    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    if user.is_banned:
        await callback.answer(texts.ADMIN_MANUAL_GRANT_USER_BANNED, show_alert=True)
        return

    ok, msg, sub = await WhiteInternetService.extend_subscription(session, user.id, days)
    if not ok:
        await callback.answer(texts.ADMIN_WI_ACTION_FAILED.format(error=msg), show_alert=True)
        return

    days_text = (
        texts.ADMIN_SUB_PERMANENT_LABEL
        if days >= PERMANENT_SUBSCRIPTION_DAYS
        else texts.TIME_DAYS_FORMAT.format(days=days)
    )

    await AuditService.log_action(
        session,
        admin_id=callback.from_user.id,
        action=AdminAuditAction.WHITE_INTERNET_EXTEND,
        target_type="user",
        target_id=user.id,
        details={"telegram_id": telegram_id, "days": days_text},
    )

    new_end_str = (
        format_datetime(sub.expires_at)
        if sub and sub.expires_at
        else texts.PLACEHOLDER_DASH
    )

    text = texts.ADMIN_WI_EXTEND_SUCCESS.format(
        telegram_id=telegram_id,
        days_text=days_text,
        new_end=new_end_str,
    )

    try:
        await callback.answer(text, show_alert=True)
    except Exception:
        pass

    callback = _safe_update_callback_data(callback, f"admin_sub_wi_menu:{telegram_id}")
    await admin_wi_subscription_menu(callback, session, target_telegram_id=telegram_id)


@router.callback_query(F.data.startswith("admin_wi_extend_custom:"))
async def admin_wi_extend_custom_start(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    telegram_id = parse_callback_id(callback.data, 1)
    if telegram_id is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return

    await callback.answer(show_alert=False)
    await state.clear()

    await state.set_state(AdminStates.admin_wi_extending_custom)
    await state.update_data(admin_telegram_id=telegram_id)

    text = texts.ADMIN_WI_EXTEND_PROMPT.format(telegram_id=telegram_id)

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_back_button(f"admin_wi_extend_menu:{telegram_id}"),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug("admin_wi_extend_custom_start edit_text failed: %s", e)


@router.message(AdminStates.admin_wi_extending_custom)
async def admin_wi_extend_custom_process(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(message.from_user.id):
        return

    data = await state.get_data()
    telegram_id = data.get("admin_telegram_id")
    if not telegram_id:
        await state.clear()
        return

    days = _validate_positive_int(message.text)
    if days is None:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_DAYS_OVERFLOW,
            get_back_button(f"admin_wi_extend_menu:{telegram_id}"),
            parse_mode="HTML",
        )
        return

    await state.clear()

    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_USER_NOT_FOUND,
            get_back_button(f"admin_wi_extend_menu:{telegram_id}"),
        )
        return

    wi_sub = await white_internet_repo.get_subscription_by_user_id(session, user.id)
    if not wi_sub:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ADMIN_WI_SUB_NOT_FOUND,
            get_back_button(f"admin_sub_wi_menu:{telegram_id}"),
        )
        return

    current_time = now_utc()
    current_end = (
        wi_sub.expires_at
        if (wi_sub.expires_at and wi_sub.expires_at > current_time)
        else current_time
    )

    new_end = (
        PERMANENT_END_DATE
        if days >= PERMANENT_SUBSCRIPTION_DAYS
        else current_end + timedelta(days=days)
    )

    days_text = (
        texts.ADMIN_SUB_PERMANENT_LABEL
        if days >= PERMANENT_SUBSCRIPTION_DAYS
        else texts.TIME_DAYS_FORMAT.format(days=days)
    )

    confirm_text = texts.ADMIN_WI_CONFIRM_EXTEND.format(
        telegram_id=telegram_id,
        days_text=days_text,
        new_end=format_datetime(new_end),
    )

    await render_hub(
        message.bot,
        message.chat.id,
        confirm_text,
        get_admin_confirm_action_keyboard(
            confirm_callback=f"admin_wi_apply_extend:{telegram_id}:{days}",
            cancel_callback=f"admin_wi_extend_menu:{telegram_id}",
        ),
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# White Internet: HWID Devices Visibility
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("admin_wi_devices:"))
async def admin_wi_devices_view(
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

    wi_sub = await white_internet_repo.get_subscription_by_user_id(session, user.id)
    if not wi_sub:
        await callback.answer(texts.ADMIN_WI_SUB_NOT_FOUND, show_alert=True)
        return

    await callback.answer(show_alert=False)

    raw_hwids = getattr(wi_sub, "active_hwids", None) or {}
    now = now_utc()
    cutoff = (now - timedelta(hours=WHITE_INTERNET_HWID_TTL_HOURS)).isoformat()
    active_count = count_active_hwids(raw_hwids, now=now)
    dev_limit = max(1, getattr(wi_sub, "device_limit", 1) or 1)

    if not raw_hwids:
        devices_list = texts.ADMIN_WI_NO_DEVICES
    else:
        lines = []
        for hwid, ts in sorted(
            raw_hwids.items(),
            key=lambda item: item[1] if isinstance(item[1], str) else "",
            reverse=True,
        ):
            if isinstance(ts, str):
                try:
                    dt = datetime.fromisoformat(ts)
                    last_conn = format_datetime(dt)
                except Exception:
                    last_conn = ts
                status = (
                    texts.ADMIN_WI_DEVICE_STATUS_ACTIVE
                    if ts >= cutoff
                    else texts.ADMIN_WI_DEVICE_STATUS_INACTIVE
                )
            else:
                last_conn = texts.PLACEHOLDER_DASH
                status = texts.ADMIN_WI_DEVICE_STATUS_INACTIVE
            lines.append(
                texts.ADMIN_WI_DEVICE_ITEM.format(
                    hwid=safe(hwid),
                    last_conn=last_conn,
                    status=status,
                )
            )
        devices_list = "\n".join(lines)

    text = texts.ADMIN_WI_DEVICES_TITLE.format(
        telegram_id=telegram_id,
        active=active_count,
        limit=dev_limit,
        devices_list=devices_list,
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_wi_devices_keyboard(
                telegram_id,
                has_hwids=bool(raw_hwids),
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug("admin_wi_devices_view edit_text failed: %s", e)

