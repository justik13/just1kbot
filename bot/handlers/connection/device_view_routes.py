"""VLESS subscription and device connection screens."""

import html
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from database.models import User
from database.repositories import vless_subscription_repo
from services.subscription import SubscriptionService
from services.vless_subscription_service import VlessSubscriptionService
from utils.telegram import render_hub

from .common import _render_connections

router = Router()
logger = logging.getLogger(__name__)


async def render_vless_sub_screen(
    bot,
    chat_id: int,
    user: User,
    session: AsyncSession,
) -> None:
    sub = await vless_subscription_repo.get_or_create_subscription(session, user.id)
    token = getattr(sub, "token", None) if sub else None
    try:
        sub_url = VlessSubscriptionService.build_subscription_url(str(token)) if token else ""
    except Exception as exc:
        logger.warning("Failed to resolve VLESS subscription url for user %s: %s", user.id, exc)
        sub_url = ""

    text = texts.VLESS_SUB_SCREEN_TEXT.format(
        sub_url=html.escape(sub_url) if sub_url else "",
    )

    builder = InlineKeyboardBuilder()
    if sub_url and 1 <= len(sub_url) <= 256:
        builder.button(
            text=texts.BTN_SUB_CONNECT_INCY,
            url=sub_url,
            style="success",
        )
    builder.button(
        text=texts.BTN_BACK_TO_CONNECTION,
        callback_data="back_to_connections",
    )
    builder.button(
        text=texts.BTN_MAIN_MENU_NAV,
        callback_data="back_to_main_menu",
    )
    builder.adjust(1)

    await render_hub(
        bot,
        chat_id,
        text,
        builder.as_markup(),
    )


@router.callback_query(F.data == "vless_sub_feed_info")
async def vless_sub_feed_info(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()

    if not db_user:
        try:
            await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        except Exception:
            pass
        return

    has_access = SubscriptionService.check_vpn_access(db_user)
    if not has_access:
        try:
            await callback.answer(texts.DEVICE_ACCESS_INACTIVE, show_alert=True)
        except Exception:
            pass
        return

    await callback.answer()

    VlessSubscriptionService.ensure_synced_background(db_user.id, is_active=True, session=session)

    await render_vless_sub_screen(
        callback.bot,
        callback.message.chat.id,
        db_user,
        session,
    )


@router.callback_query(F.data == "vless_sub_reset")
async def vless_sub_reset(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()

    from services.maintenance_service import MaintenanceService
    if not await MaintenanceService.can_user_perform_action(session, callback.from_user.id):
        try:
            await callback.answer(texts.MAINTENANCE_DEFAULT_MESSAGE, show_alert=True)
        except Exception:
            pass
        return

    if not db_user:
        try:
            await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        except Exception:
            pass
        return

    if not SubscriptionService.check_vpn_access(db_user):
        try:
            await callback.answer(texts.ERROR_NO_SUBSCRIPTION, show_alert=True)
        except Exception:
            pass
        return

    sub = await vless_subscription_repo.get_subscription_by_user_id(session, db_user.id)
    if not sub or not sub.active_hwids:
        try:
            await callback.answer(texts.VLESS_RESET_EMPTY, show_alert=True)
        except Exception:
            pass
        return

    await callback.answer()
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_VLESS_RESET_CONFIRM,
        callback_data="vless_sub_reset_confirm",
        style="danger",
    )
    builder.button(
        text=texts.BTN_CANCEL_ACTION,
        callback_data="back_to_connections",
    )
    builder.adjust(1)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.VLESS_RESET_CONFIRM_TEXT,
        builder.as_markup(),
    )


@router.callback_query(F.data == "vless_sub_reset_confirm")
async def vless_sub_reset_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()

    from services.maintenance_service import MaintenanceService
    if not await MaintenanceService.can_user_perform_action(session, callback.from_user.id):
        try:
            await callback.answer(texts.MAINTENANCE_DEFAULT_MESSAGE, show_alert=True)
        except Exception:
            pass
        return

    if not db_user:
        try:
            await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        except Exception:
            pass
        return

    if not SubscriptionService.check_vpn_access(db_user):
        try:
            await callback.answer(texts.ERROR_NO_SUBSCRIPTION, show_alert=True)
        except Exception:
            pass
        return

    sub = await vless_subscription_repo.get_subscription_by_user_id(session, db_user.id)
    if not sub or not sub.active_hwids:
        try:
            await callback.answer(texts.VLESS_RESET_EMPTY, show_alert=True)
        except Exception:
            pass
        return

    old_uuid, _ = await vless_subscription_repo.reset_hwids(session, sub.id)
    if old_uuid:
        VlessSubscriptionService.deprovision_background(
            old_uuid, version=sub.version, sub_id=sub.id, session=session
        )
        VlessSubscriptionService.ensure_synced_background(db_user.id, is_active=True, session=session)

    try:
        await callback.answer(texts.VLESS_RESET_SUCCESS, show_alert=True)
    except Exception:
        pass

    await _render_connections(
        callback.message,
        db_user,
        session,
    )
