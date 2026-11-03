import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from bot.keyboards.admin.users import (
    get_admin_confirm_action_keyboard,
    get_admin_user_devices_keyboard,
)
from utils.admin import is_admin
from utils.callbacks import (
    parse_callback_id,
)
from utils.client_parser import parse_client_from_user_agent
from bot.formatters import format_admin_breadcrumbs
from utils.formatters import format_datetime
from utils.telegram import safe

from .common import _get_user_with_profiles

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("admin_user_devices:"))
async def admin_user_devices(
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

    await callback.answer(show_alert=False)

    user = await _get_user_with_profiles(session, telegram_id)
    if not user:
        await callback.message.edit_text(
            texts.ERROR_USER_NOT_FOUND,
            reply_markup=get_back_button("admin_users"),
        )
        return

    header = format_admin_breadcrumbs(
        texts.BTN_USERS,
        f"ID {telegram_id}",
        texts.ADMIN_BTN_SUB_STANDARD,
        texts.ADMIN_USERS_DEVICE_DEVICES,
    )
    text = header

    # VLESS subscription & active HWIDs
    from database.repositories import vless_subscription_repo
    from database.repositories.vless_subscription_repo import VLESS_HWID_TTL_HOURS
    from services.vless_subscription_service import VlessSubscriptionService

    try:
        vless_sub = await vless_subscription_repo.get_subscription_by_user_id(session, user.id)
    except Exception as e:
        logger.debug("Failed to get VLESS subscription for admin devices: %s", e)
        vless_sub = None

    vless_sub_url: str | None = None
    raw_vless_hwids: dict = {}
    if vless_sub:
        vless_sub_url = VlessSubscriptionService.build_subscription_url(vless_sub.token)
        raw_vless_hwids = getattr(vless_sub, "active_hwids", None) or {}

    active_hwids = vless_subscription_repo.prune_stale_hwids(raw_vless_hwids, ttl_hours=VLESS_HWID_TTL_HOURS)
    vless_active_count = len(active_hwids)

    if not raw_vless_hwids:
        vless_devices_list = texts.ADMIN_VLESS_NO_DEVICES
    else:
        vless_lines = []
        for hwid, meta_or_ts in sorted(
            raw_vless_hwids.items(),
            key=lambda item: str(
                (item[1].get("last_seen") if isinstance(item[1], dict) else item[1]) or ""
            ),
            reverse=True,
        ):
            if isinstance(meta_or_ts, dict):
                raw_ts = meta_or_ts.get("last_seen")
                model = meta_or_ts.get("model")
                os_name = meta_or_ts.get("os")
                client = meta_or_ts.get("client") or parse_client_from_user_agent(meta_or_ts.get("ua"))
                info_parts = [p for p in (model, os_name) if p]
                parts = ([f"[{client}]"] if client else []) + info_parts
                if parts:
                    hwid_display = f"📱 {' '.join(parts)} — {hwid[:8]}…"
                else:
                    hwid_display = hwid
            else:
                raw_ts = meta_or_ts
                hwid_display = hwid

            if isinstance(raw_ts, str):
                try:
                    dt = datetime.fromisoformat(raw_ts)
                    last_conn = format_datetime(dt)
                except Exception:
                    last_conn = raw_ts
                status = (
                    texts.ADMIN_VLESS_DEVICE_STATUS_ACTIVE
                    if hwid in active_hwids
                    else texts.ADMIN_VLESS_DEVICE_STATUS_INACTIVE
                )
            else:
                last_conn = texts.PLACEHOLDER_DASH
                status = texts.ADMIN_VLESS_DEVICE_STATUS_INACTIVE
            vless_lines.append(
                texts.ADMIN_VLESS_DEVICE_ITEM.format(
                    hwid=safe(hwid_display),
                    last_conn=safe(last_conn),
                    status=status,
                )
            )
        vless_devices_list = "\n".join(vless_lines)

    vless_block = texts.ADMIN_VLESS_DEVICES_HEADER.format(
        active=vless_active_count,
        devices_list=vless_devices_list,
    )
    text = f"{text}\n\n{vless_block}"

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_user_devices_keyboard(
                telegram_id,
                vless_sub_url=vless_sub_url,
                has_vless_hwids=bool(raw_vless_hwids),
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"admin_user_devices edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_vless_hwid_reset_confirm:"))
async def admin_vless_hwid_reset_confirm(
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


    text = texts.ADMIN_VLESS_HWID_RESET_CONFIRM_TITLE.format(telegram_id=telegram_id)
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_confirm_action_keyboard(
                confirm_callback=f"admin_vless_hwid_reset_apply:{telegram_id}",
                cancel_callback=f"admin_user_devices:{telegram_id}",
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"admin_vless_hwid_reset_confirm edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_vless_hwid_reset:"))
async def admin_vless_hwid_reset_legacy(
    callback: CallbackQuery,
    session: AsyncSession,
):
    """Старые сообщения: прямой сброс запрещён, ведём на экран подтверждения."""
    telegram_id = parse_callback_id(callback.data, 1)
    if telegram_id is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return
    redirected = callback.model_copy(
        update={"data": f"admin_vless_hwid_reset_confirm:{telegram_id}"}
    )
    await admin_vless_hwid_reset_confirm(redirected, session)


@router.callback_query(F.data.startswith("admin_vless_hwid_reset_apply:"))
async def admin_vless_hwid_reset(
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

    user = await _get_user_with_profiles(session, telegram_id)
    if not user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    from config.enums import AdminAuditAction
    from database.repositories import vless_subscription_repo
    from services.audit_service import AuditService
    from services.subscription import SubscriptionService
    from services.vless_subscription_service import VlessSubscriptionService

    sub = await vless_subscription_repo.get_subscription_by_user_id(session, user.id)
    if sub:
        target_active = (
            SubscriptionService.check_vpn_access(user)
            and not getattr(user, "financial_hold", False)
            and not getattr(user, "is_banned", False)
        )
        old_uuid, _ = await vless_subscription_repo.reset_hwids(session, sub.id)
        sub.is_active = target_active
        session.add(sub)
        if old_uuid:
            VlessSubscriptionService.deprovision_background(
                old_uuid, version=sub.version, sub_id=sub.id, session=session
            )
            VlessSubscriptionService.ensure_synced_background(user.id, is_active=target_active, session=session)
        await AuditService.log_action(
            session,
            admin_id=callback.from_user.id,
            action=AdminAuditAction.VLESS_HWID_RESET,
            target_type="user",
            target_id=user.id,
            details={"telegram_id": telegram_id, "subscription_id": sub.id},
        )
        await session.commit()

    await callback.answer(texts.ADMIN_ALERT_VLESS_HWID_RESET_SUCCESS, show_alert=True)
    if callback.data.endswith(":menu"):
        from .subscription_menu_routes import admin_subscription_menu

        try:
            updated_cb = callback.model_copy(update={"data": f"admin_sub_awg_menu:{telegram_id}"})
        except Exception:
            callback.data = f"admin_sub_awg_menu:{telegram_id}"
            updated_cb = callback
        await admin_subscription_menu(updated_cb, session)
    else:
        try:
            updated_cb = callback.model_copy(update={"data": f"admin_user_devices:{telegram_id}"})
        except Exception:
            callback.data = f"admin_user_devices:{telegram_id}"
            updated_cb = callback
        await admin_user_devices(updated_cb, session)


@router.callback_query(F.data.startswith("admin_vless_token_rotate_confirm:"))
async def admin_vless_token_rotate_confirm(
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


    text = texts.ADMIN_VLESS_TOKEN_ROTATE_CONFIRM_TITLE.format(telegram_id=telegram_id)
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_confirm_action_keyboard(
                confirm_callback=f"admin_vless_token_rotate_apply:{telegram_id}",
                cancel_callback=f"admin_user_devices:{telegram_id}",
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"admin_vless_token_rotate_confirm edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_vless_token_rotate:"))
async def admin_vless_token_rotate_legacy(
    callback: CallbackQuery,
    session: AsyncSession,
):
    """Старые сообщения: прямой перевыпуск запрещён, ведём на экран подтверждения."""
    telegram_id = parse_callback_id(callback.data, 1)
    if telegram_id is None:
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
        return
    redirected = callback.model_copy(
        update={"data": f"admin_vless_token_rotate_confirm:{telegram_id}"}
    )
    await admin_vless_token_rotate_confirm(redirected, session)


@router.callback_query(F.data.startswith("admin_vless_token_rotate_apply:"))
async def admin_vless_token_rotate(
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

    user = await _get_user_with_profiles(session, telegram_id)
    if not user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    from config.enums import AdminAuditAction
    from database.repositories import vless_subscription_repo
    from services.audit_service import AuditService
    from services.subscription import SubscriptionService
    from services.vless_subscription_service import VlessSubscriptionService

    sub = await vless_subscription_repo.get_or_create_subscription(session, user.id)
    if sub:
        target_active = (
            SubscriptionService.check_vpn_access(user)
            and not getattr(user, "financial_hold", False)
            and not getattr(user, "is_banned", False)
        )
        _, old_uuid, _ = await vless_subscription_repo.rotate_token(session, sub.id)
        sub.is_active = target_active
        session.add(sub)
        if old_uuid:
            VlessSubscriptionService.deprovision_background(
                old_uuid, version=sub.version, sub_id=sub.id, session=session
            )
            VlessSubscriptionService.ensure_synced_background(user.id, is_active=target_active, session=session)
        await AuditService.log_action(
            session,
            admin_id=callback.from_user.id,
            action=AdminAuditAction.VLESS_TOKEN_RESET,
            target_type="user",
            target_id=user.id,
            details={"telegram_id": telegram_id, "subscription_id": sub.id, "token_rotated": True},
        )
        await session.commit()

    await callback.answer(texts.ADMIN_ALERT_VLESS_TOKEN_ROTATE_SUCCESS, show_alert=True)
    if callback.data.endswith(":menu"):
        from .subscription_menu_routes import admin_subscription_menu

        try:
            updated_cb = callback.model_copy(update={"data": f"admin_sub_awg_menu:{telegram_id}"})
        except Exception:
            callback.data = f"admin_sub_awg_menu:{telegram_id}"
            updated_cb = callback
        await admin_subscription_menu(updated_cb, session)
    else:
        try:
            updated_cb = callback.model_copy(update={"data": f"admin_user_devices:{telegram_id}"})
        except Exception:
            callback.data = f"admin_user_devices:{telegram_id}"
            updated_cb = callback
        await admin_user_devices(updated_cb, session)

