"""Telegram UI handlers for White Internet subscriptions, purchases, renewals, and topups."""

from __future__ import annotations

import html
import logging
import os
import urllib.parse

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from config.constants import (
    WHITE_INTERNET_BASE_DURATION_DAYS,
    WHITE_INTERNET_BASE_PRICE_RUB,
    WHITE_INTERNET_TOPUP_PACKS,
)
from config.enums import WhiteInternetProvisioningStatus, WhiteInternetStatus
from database.models import WhiteInternetSubscription
from database.repositories import white_internet_repo
from database.repositories.account_ledger_repo import get_account_balance
from database.repositories.users_repo import get_user_by_telegram_id
from services.white_internet_service import WhiteInternetService
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)
router = Router(name="white_internet")


def _format_bytes(bytes_count: int) -> str:
    gib = bytes_count / (1024**3)
    return f"{gib:.1f}{texts.GIB_SUFFIX}"


def _render_progress_bar(used_bytes: int, total_bytes: int, length: int = 10) -> str:
    if total_bytes <= 0:
        return "░" * length
    ratio = min(max(used_bytes / total_bytes, 0.0), 1.0)
    filled = int(round(ratio * length))
    return "█" * filled + "░" * (length - filled)


def get_white_internet_overview_keyboard(
    sub: WhiteInternetSubscription | None,
    bot_domain: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if sub is None:
        builder.button(
            text=texts.BTN_WL_BUY_ACCESS.format(
                price=int(WHITE_INTERNET_BASE_PRICE_RUB),
                days=WHITE_INTERNET_BASE_DURATION_DAYS,
            ),
            callback_data="wl_buy_confirm",
            style="success",
        )
        builder.adjust(1, 1)
    elif sub.status == WhiteInternetStatus.EXPIRED:
        builder.button(
            text=texts.BTN_WL_RENEW.format(price=int(WHITE_INTERNET_BASE_PRICE_RUB)),
            callback_data="wl_renew_confirm",
            style="success",
        )
        builder.adjust(1, 1)
    elif sub.status == WhiteInternetStatus.DISABLED:
        builder.adjust(1)
    elif sub.status == WhiteInternetStatus.PENDING:
        # Paid but not provisioned: never expose a connection promise.
        builder.adjust(1)
    else:
        if sub.status == WhiteInternetStatus.ACTIVE and sub.provisioning_status == WhiteInternetProvisioningStatus.ACTIVE:
            sub_url = f"https://{bot_domain}/sub/wl/{sub.token}"
            encoded_sub_url = urllib.parse.quote(sub_url, safe="")
            builder.button(
                text=texts.BTN_WL_CONNECT_CLIENT,
                url=f"https://t.me/share/url?url={encoded_sub_url}",
                style="success",
            )
            builder.button(text=texts.BTN_WL_SHOW_LINK, callback_data="wl_show_link")

        # EXHAUSTED may buy a top-up to reactivate; ACTIVE may top up too.
        if sub.status in (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.EXHAUSTED):
            builder.button(text=texts.BTN_WL_TOPUP, callback_data="wl_topup_menu")
            builder.button(
                text=texts.BTN_WL_RENEW.format(price=int(WHITE_INTERNET_BASE_PRICE_RUB)),
                callback_data="wl_renew_confirm",
            )
        builder.adjust(1, 1, 2, 1)

    builder.button(text=texts.BTN_BACK, callback_data="back_to_main_menu")
    return builder.as_markup()


def get_topup_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for pack_gb, price in sorted(WHITE_INTERNET_TOPUP_PACKS.items()):
        builder.button(
            text=texts.BTN_WL_TOPUP_PACK_ITEM.format(gb=pack_gb, price=int(price)),
            callback_data=f"wl_topup_pack_{pack_gb}",
        )
    builder.button(text=texts.BTN_BACK, callback_data="white_internet")
    builder.adjust(1, 1, 1, 1)
    return builder.as_markup()


@router.callback_query(F.data == "white_internet")
async def show_white_internet_menu(query: CallbackQuery, session: AsyncSession):
    await query.answer()
    user = await get_user_by_telegram_id(session, query.from_user.id)
    if user is None:
        await query.message.answer(texts.WL_USER_NOT_FOUND)
        return

    sub = await white_internet_repo.get_subscription_by_user_id(session, user.id)
    bot_domain = os.getenv("DOMAIN", "t.me")
    now = now_utc()

    if sub is None:
        text = texts.WL_OVERVIEW_NO_SUB.format(
            price=int(WHITE_INTERNET_BASE_PRICE_RUB),
            days=WHITE_INTERNET_BASE_DURATION_DAYS,
            traffic=50,
        )
    else:
        available_bytes = await white_internet_repo.get_available_quota_bytes(session, sub.id, now)
        total_limit = available_bytes + sub.traffic_used_bytes
        status_text_map = {
            WhiteInternetStatus.ACTIVE: texts.STATUS_SUBSCRIPTION_ACTIVE,
            WhiteInternetStatus.PENDING: texts.WL_STATUS_PENDING,
            WhiteInternetStatus.EXHAUSTED: texts.WL_STATUS_EXHAUSTED,
            WhiteInternetStatus.EXPIRED: texts.WL_STATUS_EXPIRED,
            WhiteInternetStatus.DISABLED: texts.WL_STATUS_DISABLED,
        }
        text = texts.WL_OVERVIEW_ACTIVE.format(
            status=status_text_map.get(sub.status, sub.status),
            expiry=sub.expires_at.strftime(texts.WL_DATETIME_FORMAT),
            available=_format_bytes(available_bytes),
            used=_format_bytes(sub.traffic_used_bytes),
            total=_format_bytes(total_limit),
            progress=_render_progress_bar(sub.traffic_used_bytes, total_limit),
        )

    kb = get_white_internet_overview_keyboard(sub, bot_domain)
    try:
        await query.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await query.message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "wl_buy_confirm")
async def process_white_internet_buy(query: CallbackQuery, session: AsyncSession):
    await query.answer()
    user = await get_user_by_telegram_id(session, query.from_user.id)
    if user is None:
        return
    balance_snapshot = await get_account_balance(session, user.id)
    if balance_snapshot.available < WHITE_INTERNET_BASE_PRICE_RUB:
        shortage = WHITE_INTERNET_BASE_PRICE_RUB - balance_snapshot.available
        kb = InlineKeyboardBuilder()
        kb.button(text=texts.BUTTON_TOPUP, callback_data="menu_balance")
        kb.button(text=texts.BTN_BACK, callback_data="white_internet")
        kb.adjust(1, 1)
        await query.message.edit_text(
            texts.WL_INSUFFICIENT_BALANCE_BUY.format(
                price=int(WHITE_INTERNET_BASE_PRICE_RUB),
                balance=balance_snapshot.available,
                shortage=shortage,
            ),
            reply_markup=kb.as_markup(),
            parse_mode="HTML",
        )
        return

    success, msg, _sub = await WhiteInternetService.purchase_subscription(session, user.id)
    if not success:
        kb = InlineKeyboardBuilder()
        kb.button(text=texts.BTN_BACK, callback_data="white_internet")
        await query.message.edit_text(html.escape(msg), reply_markup=kb.as_markup())
        return
    await session.commit()
    await show_white_internet_menu(query, session)


@router.callback_query(F.data == "wl_renew_confirm")
async def process_white_internet_renew(query: CallbackQuery, session: AsyncSession):
    await query.answer()
    user = await get_user_by_telegram_id(session, query.from_user.id)
    if user is None:
        return
    balance_snapshot = await get_account_balance(session, user.id)
    if balance_snapshot.available < WHITE_INTERNET_BASE_PRICE_RUB:
        shortage = WHITE_INTERNET_BASE_PRICE_RUB - balance_snapshot.available
        kb = InlineKeyboardBuilder()
        kb.button(text=texts.BUTTON_TOPUP, callback_data="menu_balance")
        kb.button(text=texts.BTN_BACK, callback_data="white_internet")
        kb.adjust(1, 1)
        await query.message.edit_text(
            texts.WL_INSUFFICIENT_BALANCE_RENEW.format(
                price=int(WHITE_INTERNET_BASE_PRICE_RUB),
                balance=balance_snapshot.available,
                shortage=shortage,
            ),
            reply_markup=kb.as_markup(),
            parse_mode="HTML",
        )
        return

    success, msg, _sub = await WhiteInternetService.renew_subscription(session, user.id)
    if not success:
        kb = InlineKeyboardBuilder()
        kb.button(text=texts.BTN_BACK, callback_data="white_internet")
        await query.message.edit_text(html.escape(msg), reply_markup=kb.as_markup())
        return
    await session.commit()
    await show_white_internet_menu(query, session)


@router.callback_query(F.data == "wl_topup_menu")
async def show_topup_menu(query: CallbackQuery, session: AsyncSession):
    await query.answer()
    user = await get_user_by_telegram_id(session, query.from_user.id)
    if user is not None:
        sub = await white_internet_repo.get_subscription_by_user_id(session, user.id)
        if sub is None or sub.status not in (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.EXHAUSTED):
            await query.message.edit_text(texts.WL_SUB_NOT_READY, reply_markup=get_white_internet_overview_keyboard(sub, os.getenv("DOMAIN", "t.me")))
            return
    await query.message.edit_text(texts.WL_TOPUP_MENU_TEXT, reply_markup=get_topup_keyboard(), parse_mode="HTML")


@router.callback_query(F.data.startswith("wl_topup_pack_"))
async def process_topup_pack(query: CallbackQuery, session: AsyncSession):
    await query.answer()
    try:
        pack_gb = int(query.data.replace("wl_topup_pack_", ""))
    except ValueError:
        return
    user = await get_user_by_telegram_id(session, query.from_user.id)
    if user is None:
        return
    pack_price = WHITE_INTERNET_TOPUP_PACKS.get(pack_gb)
    if pack_price is None:
        return

    balance_snapshot = await get_account_balance(session, user.id)
    if balance_snapshot.available < pack_price:
        shortage = pack_price - balance_snapshot.available
        kb = InlineKeyboardBuilder()
        kb.button(text=texts.BUTTON_TOPUP, callback_data="menu_balance")
        kb.button(text=texts.BTN_BACK, callback_data="wl_topup_menu")
        kb.adjust(1, 1)
        await query.message.edit_text(
            texts.WL_INSUFFICIENT_BALANCE_TOPUP.format(
                gb=pack_gb,
                price=int(pack_price),
                balance=balance_snapshot.available,
                shortage=shortage,
            ),
            reply_markup=kb.as_markup(),
            parse_mode="HTML",
        )
        return

    success, msg, _grant = await WhiteInternetService.topup_quota(session, user.id, pack_gb)
    if not success:
        kb = InlineKeyboardBuilder()
        kb.button(text=texts.BTN_BACK, callback_data="white_internet")
        await query.message.edit_text(html.escape(msg), reply_markup=kb.as_markup())
        return
    await session.commit()
    await show_white_internet_menu(query, session)


@router.callback_query(F.data == "wl_show_link")
async def show_subscription_link(query: CallbackQuery, session: AsyncSession):
    await query.answer()
    user = await get_user_by_telegram_id(session, query.from_user.id)
    if user is None:
        return
    sub = await white_internet_repo.get_subscription_by_user_id(session, user.id)
    if sub is None or sub.status != WhiteInternetStatus.ACTIVE:
        await query.message.edit_text(texts.WL_SUB_NOT_READY, reply_markup=get_white_internet_overview_keyboard(sub, os.getenv("DOMAIN", "t.me")))
        return
    bot_domain = os.getenv("DOMAIN", "t.me")
    sub_url = f"https://{bot_domain}/sub/wl/{sub.token}"
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_BACK, callback_data="white_internet")
    await query.message.edit_text(
        texts.WL_SHOW_LINK_TEXT.format(url=html.escape(sub_url)),
        reply_markup=kb.as_markup(),
        parse_mode="HTML",
    )
