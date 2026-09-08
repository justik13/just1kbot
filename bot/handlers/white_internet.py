"""Telegram UI handlers for White Internet subscriptions, purchases, renewals, and topups."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import html
import logging
import os

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, CopyTextButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from config.constants import (
    WHITE_INTERNET_BASE_DURATION_DAYS,
    WHITE_INTERNET_BASE_PRICE_RUB,
    WHITE_INTERNET_BASE_TRAFFIC_BYTES,
    WHITE_INTERNET_EXTRA_DEVICE_PRICE_RUB,
    WHITE_INTERNET_HWID_TTL_HOURS,
    WHITE_INTERNET_MAX_DEVICE_LIMIT,
    WHITE_INTERNET_SUB_PATH_PREFIX,
    WHITE_INTERNET_TOPUP_PACKS,
    WHITE_INTERNET_TRIAL_DURATION_DAYS,
    WHITE_INTERNET_TRIAL_TRAFFIC_BYTES,
)
from bot.handlers.payment.balance_routes import _create_and_render_topup
from config.enums import WhiteInternetStatus
from config.settings import get_settings
from database.models import Server, WhiteInternetSubscription
from database.repositories import white_internet_repo
from database.repositories.account_ledger_repo import get_account_balance
from database.repositories.tariff_quotes_repo import get_or_create_current_version
from database.repositories.users_repo import get_user_by_telegram_id
from database.repositories.white_internet_repo import (
    WhiteInternetResetCooldownError,
)
from services.maintenance_service import MaintenanceService
from services.white_internet_service import (
    WhiteInternetService,
    get_white_internet_tier_price,
)
from utils.admin import is_admin
from utils.datetime_helpers import now_utc
from utils.formatters import format_traffic
from utils.security import normalize_public_domain

logger = logging.getLogger(__name__)
router = Router(name="white_internet")


def _format_bytes(bytes_count: int) -> str:
    return format_traffic(bytes_count)


def _render_progress_bar(used_bytes: int, total_bytes: int, length: int = 10) -> str:
    if total_bytes <= 0:
        return "░" * length
    ratio = min(max(used_bytes / total_bytes, 0.0), 1.0)
    filled = int(round(ratio * length))
    return "█" * filled + "░" * (length - filled)


def _resolve_subscription_prefix(origin_node: Server | None = None) -> str:
    if origin_node and isinstance(origin_node.extra_data, dict) and origin_node.extra_data.get("sub_path_prefix"):
        raw = str(origin_node.extra_data["sub_path_prefix"]).strip().rstrip("/")
        if not raw.startswith("/"):
            raw = f"/{raw}"
        return raw
    env_prefix = (os.getenv("WHITE_INTERNET_SUB_PATH_PREFIX") or WHITE_INTERNET_SUB_PATH_PREFIX).strip().rstrip("/")
    if not env_prefix.startswith("/"):
        env_prefix = f"/{env_prefix}"
    return env_prefix


def _build_subscription_url(domain: str, token: str, sub_prefix: str | None = None) -> str:
    if not sub_prefix:
        sub_prefix = os.getenv("WHITE_INTERNET_SUB_PATH_PREFIX") or WHITE_INTERNET_SUB_PATH_PREFIX
    sub_prefix = sub_prefix.strip().rstrip("/")
    if not sub_prefix.startswith("/"):
        sub_prefix = f"/{sub_prefix}"
    return f"https://{domain}{sub_prefix}/{token}"


def get_white_internet_overview_keyboard(
    sub: WhiteInternetSubscription | None,
    bot_domain: str | None,
    base_price: int = int(WHITE_INTERNET_BASE_PRICE_RUB),
    sub_prefix: str | None = None,
    is_admin_user: bool = False,
    has_trial_available: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if sub is None:
        if has_trial_available:
            builder.button(
                text=texts.BTN_WL_ACTIVATE_TRIAL,
                callback_data="wl_trial_activate",
                style="success",
            )
        builder.button(
            text=texts.BTN_WL_BUY_ACCESS.format(
                price=base_price,
                days=WHITE_INTERNET_BASE_DURATION_DAYS,
            ),
            callback_data="wl_buy_preview",
            style="success" if not has_trial_available else None,
        )
        builder.button(text=texts.BTN_BACK, callback_data="back_to_main_menu")
        builder.adjust(1)
        return builder.as_markup()

    if sub.status == WhiteInternetStatus.PENDING:
        builder.button(text=texts.BTN_WL_REFRESH_STATUS, callback_data="white_internet")
        builder.button(text=texts.BTN_BACK, callback_data="back_to_main_menu")
        builder.adjust(1)
        return builder.as_markup()

    if sub.status == WhiteInternetStatus.EXPIRED:
        sub_limit = max(1, getattr(sub, "device_limit", 1) or 1)
        renew_price = int(get_white_internet_tier_price(sub_limit, base_price=Decimal(base_price)))
        if getattr(sub, "is_trial", False):
            builder.button(
                text=texts.BTN_WL_CONVERT_TRIAL.format(price=base_price),
                callback_data="wl_renew_preview",
                style="success",
            )
        else:
            builder.button(
                text=texts.BTN_WL_RENEW.format(price=renew_price),
                callback_data="wl_renew_preview",
                style="success",
            )
        builder.button(text=texts.BTN_BACK, callback_data="back_to_main_menu")
        builder.adjust(1)
        return builder.as_markup()

    if sub.status == WhiteInternetStatus.DISABLED:
        builder.button(text=texts.BTN_BACK, callback_data="back_to_main_menu")
        builder.adjust(1)
        return builder.as_markup()

    has_sub_link = bool(
        sub.status == WhiteInternetStatus.ACTIVE
        and getattr(sub, "token", None)
        and bot_domain
    )
    if has_sub_link:
        sub_url = _build_subscription_url(bot_domain, sub.token, sub_prefix=sub_prefix)
        builder.button(
            text=texts.BTN_WL_COPY_LINK,
            copy_text=CopyTextButton(text=sub_url),
        )
        builder.button(
            text=texts.BTN_WL_INCY_INSTRUCTIONS if getattr(sub, "is_trial", False) else texts.BTN_WL_INSTRUCTIONS,
            callback_data="wl_show_link",
        )

    now = now_utc()
    can_renew = (
        sub.expires_at is None
        or (sub.expires_at - now).total_seconds() <= 30 * 86400
    )
    sub_limit = max(1, getattr(sub, "device_limit", 1) or 1)
    renew_price = int(get_white_internet_tier_price(sub_limit, base_price=Decimal(base_price)))

    if can_renew:
        if getattr(sub, "is_trial", False):
            builder.button(
                text=texts.BTN_WL_CONVERT_TRIAL.format(price=base_price),
                callback_data="wl_renew_preview",
                style="success",
            )
        else:
            builder.button(
                text=texts.BTN_WL_RENEW.format(price=renew_price),
                callback_data="wl_renew_preview",
            )

    if not getattr(sub, "is_trial", False):
        builder.button(text=texts.BTN_WL_TOPUP, callback_data="wl_topup_menu")
        if is_admin_user and sub_limit < WHITE_INTERNET_MAX_DEVICE_LIMIT:
            builder.button(text=texts.BTN_WL_ADD_DEVICE, callback_data="wl_add_device_menu")

    if sub.status in (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.EXHAUSTED):
        builder.button(text=texts.BTN_WL_RESET_DEVICES, callback_data="wl_reset_devices")

    builder.button(text=texts.BTN_BACK, callback_data="back_to_main_menu")
    builder.adjust(1)

    return builder.as_markup()


def get_topup_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for pack_gb, price in sorted(WHITE_INTERNET_TOPUP_PACKS.items()):
        builder.button(
            text=texts.BTN_WL_TOPUP_PACK_ITEM.format(gb=pack_gb, price=int(price)),
            callback_data=f"wl_topup_preview:{pack_gb}",
        )
    builder.button(text=texts.BTN_BACK, callback_data="white_internet")
    builder.adjust(1)
    return builder.as_markup()


async def _get_effective_tariff_info(
    session: AsyncSession,
) -> tuple[Decimal, int, int, int]:
    base_price = WHITE_INTERNET_BASE_PRICE_RUB
    duration_days = WHITE_INTERNET_BASE_DURATION_DAYS
    base_quota_bytes = WHITE_INTERNET_BASE_TRAFFIC_BYTES
    try:
        tariff = await WhiteInternetService.get_or_create_white_internet_tariff(session)
        tariff_version = await get_or_create_current_version(session, tariff)
        price_val = getattr(tariff_version, "price_rub", None)
        if isinstance(price_val, (int, float, str, Decimal)):
            base_price = Decimal(str(price_val))
        if isinstance(getattr(tariff, "duration_days", None), int):
            duration_days = tariff.duration_days
        quota_val = getattr(tariff_version, "base_quota_bytes", None)
        if isinstance(quota_val, int) and quota_val > 0:
            base_quota_bytes = quota_val
    except Exception:
        pass
    return base_price, int(base_price), duration_days, base_quota_bytes


async def _get_effective_base_price(session: AsyncSession) -> tuple[Decimal, int, int]:
    base_price, base_price_int, duration_days, _ = await _get_effective_tariff_info(session)
    return base_price, base_price_int, duration_days


async def _resolve_subscription_domain(
    session: AsyncSession,
    sub: WhiteInternetSubscription | None,
) -> str | None:
    """Resolve the optimal public domain for subscription delivery.

    Priority:
    1. Origin server CDN domain (server.extra_data['cdn_domain']) - whitelisted by RU ISPs via Yandex CDN Anycast.
    2. WHITE_INTERNET_CDN_DOMAIN environment variable fallback.
    3. Primary bot public domain (settings.DOMAIN / DOMAIN / BOT_DOMAIN).
    4. Origin server primary hostname (server.domain).
    """
    origin_node: Server | None = None
    if sub and getattr(sub, "origin_node_id", None):
        try:
            origin_node = await session.get(Server, sub.origin_node_id)
        except Exception:
            origin_node = None
        if origin_node and isinstance(origin_node.extra_data, dict):
            cdn_domain = normalize_public_domain(origin_node.extra_data.get("cdn_domain"))
            if cdn_domain:
                return cdn_domain

    env_cdn = normalize_public_domain(os.getenv("WHITE_INTERNET_CDN_DOMAIN"))
    if env_cdn:
        return env_cdn

    bot_domain = None
    try:
        settings_domain = get_settings().DOMAIN
        bot_domain = normalize_public_domain(settings_domain)
    except Exception:
        pass
    if not bot_domain:
        bot_domain = normalize_public_domain(os.getenv("DOMAIN") or os.getenv("BOT_DOMAIN"))
    if bot_domain:
        return bot_domain

    if origin_node:
        node_domain = normalize_public_domain(getattr(origin_node, "domain", None))
        if node_domain:
            return node_domain
        if getattr(origin_node, "api_url", None):
            node_api_domain = normalize_public_domain(origin_node.api_url)
            if node_api_domain:
                return node_api_domain

    return None


async def _resolve_subscription_prefix_for_sub(
    session: AsyncSession,
    sub: WhiteInternetSubscription | None,
) -> str:
    origin_node: Server | None = None
    if sub and getattr(sub, "origin_node_id", None):
        try:
            origin_node = await session.get(Server, sub.origin_node_id)
        except Exception:
            origin_node = None
    return _resolve_subscription_prefix(origin_node)


async def _resolve_subscription_target(
    session: AsyncSession,
    sub: WhiteInternetSubscription | None,
) -> tuple[str | None, str]:
    domain = await _resolve_subscription_domain(session, sub)
    prefix = await _resolve_subscription_prefix_for_sub(session, sub)
    return domain, prefix


@router.callback_query(F.data == "white_internet")
async def show_white_internet_menu(query: CallbackQuery, session: AsyncSession):
    user = await get_user_by_telegram_id(session, query.from_user.id)
    if user is None:
        try:
            await query.answer()
        except Exception:
            pass
        await query.message.answer(texts.WL_USER_NOT_FOUND)
        return

    sub = await white_internet_repo.get_subscription_by_user_id(session, user.id)
    has_trial_available = not await white_internet_repo.has_ever_activated_trial(session, user.id)
    sub_domain = await _resolve_subscription_domain(session, sub)
    sub_prefix = await _resolve_subscription_prefix_for_sub(session, sub)
    now = now_utc()

    _base_price, base_price_int, duration_days, base_quota_bytes = await _get_effective_tariff_info(session)

    if sub is None:
        if has_trial_available:
            text = texts.WL_OVERVIEW_TRIAL_NO_SUB.format(
                days=WHITE_INTERNET_TRIAL_DURATION_DAYS,
                traffic=int(WHITE_INTERNET_TRIAL_TRAFFIC_BYTES / (1024**3)),
            )
        else:
            text = texts.WL_OVERVIEW_NO_SUB.format(
                price=base_price_int,
                days=duration_days,
                traffic=int(base_quota_bytes // (1024**3)),
            )
    else:
        available_bytes = await white_internet_repo.get_available_quota_bytes(session, sub.id, now)
        total_limit = sub.traffic_limit_bytes
        status_text_map = {
            WhiteInternetStatus.PENDING: texts.WL_STATUS_PENDING,
            WhiteInternetStatus.ACTIVE: (
                texts.WL_STATUS_TRIAL_ACTIVE
                if getattr(sub, "is_trial", False)
                else texts.STATUS_SUBSCRIPTION_ACTIVE
            ),
            WhiteInternetStatus.EXHAUSTED: texts.WL_STATUS_EXHAUSTED,
            WhiteInternetStatus.EXPIRED: texts.WL_STATUS_EXPIRED,
            WhiteInternetStatus.DISABLED: texts.WL_STATUS_DISABLED,
        }
        status_str = status_text_map.get(sub.status, str(sub.status))
        used_bytes = max(0, total_limit - available_bytes)
        progress_bar = _render_progress_bar(used_bytes, total_limit)
        expiry_str = sub.expires_at.strftime(texts.WL_DATETIME_FORMAT) if sub.expires_at else texts.TIME_FOREVER

        current_hwids = dict(sub.active_hwids or {})
        cutoff = (now - timedelta(hours=WHITE_INTERNET_HWID_TTL_HOURS)).isoformat()
        active_devices = sum(
            1 for ts in current_hwids.values() if isinstance(ts, str) and ts >= cutoff
        )
        effective_device_limit = max(1, getattr(sub, "device_limit", 1) or 1)

        text = texts.WL_OVERVIEW_ACTIVE.format(
            status=status_str,
            expiry=expiry_str,
            available=_format_bytes(available_bytes),
            used=_format_bytes(used_bytes),
            total=_format_bytes(total_limit),
            progress=progress_bar,
            active_devices=active_devices,
            device_limit=effective_device_limit,
        )
        if sub.status == WhiteInternetStatus.ACTIVE and not sub_domain:
            text += texts.WL_DOMAIN_UNCONFIGURED_BANNER

    is_admin_user = is_admin(query.from_user.id)
    kb = get_white_internet_overview_keyboard(
        sub,
        sub_domain,
        base_price=base_price_int,
        sub_prefix=sub_prefix,
        is_admin_user=is_admin_user,
        has_trial_available=has_trial_available,
    )
    try:
        await query.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        try:
            await query.answer(texts.WL_ALERT_TRAFFIC_UPDATED, show_alert=False)
        except Exception:
            pass
    except TelegramBadRequest as exc:
        if "not_modified" in str(exc).lower().replace(" ", "_"):
            try:
                await query.answer(texts.WL_ALERT_TRAFFIC_UP_TO_DATE, show_alert=False)
            except Exception:
                pass
        else:
            logger.warning("TelegramBadRequest editing white internet menu: %s", exc)
            try:
                await query.answer()
            except Exception:
                pass
    except Exception as exc:
        logger.warning("Unexpected error editing white internet menu: %s", exc)
        try:
            await query.answer()
        except Exception:
            pass


@router.callback_query(F.data == "wl_trial_activate")
async def process_white_internet_trial_activate(query: CallbackQuery, session: AsyncSession):
    try:
        await query.answer()
    except Exception:
        pass
    user = await get_user_by_telegram_id(session, query.from_user.id)
    if user is None:
        return

    try:
        success, msg, _sub = await WhiteInternetService.create_trial_subscription(session, user.id)
    except Exception as exc:
        logger.error("Unexpected error during white internet trial activation: %s", exc)
        success, msg = False, texts.WL_NO_SERVERS_AVAILABLE

    if not success:
        kb = InlineKeyboardBuilder()
        kb.button(text=texts.BTN_BACK, callback_data="white_internet")
        await query.message.edit_text(html.escape(msg), reply_markup=kb.as_markup(), parse_mode="HTML")
        return

    await session.commit()
    await show_white_internet_menu(query, session)


@router.callback_query(F.data == "wl_buy_preview")
async def process_white_internet_buy_preview(query: CallbackQuery, session: AsyncSession):
    try:
        await query.answer()
    except Exception:
        pass
    user = await get_user_by_telegram_id(session, query.from_user.id)
    if user is None:
        return
    base_price, base_price_int, duration_days, base_quota_bytes = await _get_effective_tariff_info(session)
    traffic_gb = int(base_quota_bytes // (1024**3))
    balance_snapshot = await get_account_balance(session, user_id=user.id)
    balance = balance_snapshot.available

    builder = InlineKeyboardBuilder()
    if balance >= base_price:
        balance_details = texts.WL_PREVIEW_BALANCE_OK.format(remaining=(balance - base_price))
        builder.button(
            text=texts.BTN_WL_PAY_BASE.format(price=base_price_int),
            callback_data="wl_buy_execute",
            style="success",
        )
    else:
        shortage = base_price - balance
        balance_details = texts.WL_PREVIEW_BALANCE_SHORTAGE.format(shortage=shortage)
        builder.button(
            text=texts.BTN_WL_TOPUP_SHORTAGE.format(shortage=int(shortage)),
            callback_data=f"wl_topup_shortage:{int(shortage)}",
            style="success",
        )
    builder.button(text=texts.BTN_BACK, callback_data="white_internet")
    builder.adjust(1, 1)

    text = texts.WL_BUY_PREVIEW_TEXT.format(
        days=duration_days,
        traffic=traffic_gb,
        price=base_price_int,
        balance=balance,
        balance_details=balance_details,
    )
    await query.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.in_({"wl_buy_execute", "wl_buy_confirm"}))
async def process_white_internet_buy(query: CallbackQuery, session: AsyncSession):
    try:
        await query.answer()
    except Exception:
        pass
    user = await get_user_by_telegram_id(session, query.from_user.id)
    if user is None:
        return
    base_price, base_price_int, _days = await _get_effective_base_price(session)

    balance_snapshot = await get_account_balance(session, user_id=user.id)
    if balance_snapshot.available < base_price:
        shortage = base_price - balance_snapshot.available
        kb = InlineKeyboardBuilder()
        kb.button(
            text=texts.BTN_WL_TOPUP_SHORTAGE.format(shortage=int(shortage)),
            callback_data=f"wl_topup_shortage:{int(shortage)}",
            style="success",
        )
        kb.button(text=texts.BTN_BACK, callback_data="white_internet")
        kb.adjust(1, 1)
        await query.message.edit_text(
            texts.WL_INSUFFICIENT_BALANCE_BUY.format(
                price=base_price_int,
                balance=balance_snapshot.available,
                shortage=shortage,
            ),
            reply_markup=kb.as_markup(),
            parse_mode="HTML",
        )
        return

    try:
        success, msg, _sub = await WhiteInternetService.purchase_subscription(session, user.id)
    except Exception as exc:
        logger.error("Unexpected error during white internet purchase: %s", exc)
        success, msg = False, texts.WL_NO_SERVERS_AVAILABLE

    if not success:
        kb = InlineKeyboardBuilder()
        kb.button(text=texts.BTN_BACK, callback_data="white_internet")
        await query.message.edit_text(html.escape(msg), reply_markup=kb.as_markup())
        return
    await session.commit()
    await show_white_internet_menu(query, session)


@router.callback_query(F.data == "wl_renew_preview")
async def process_white_internet_renew_preview(query: CallbackQuery, session: AsyncSession):
    try:
        await query.answer()
    except Exception:
        pass
    user = await get_user_by_telegram_id(session, query.from_user.id)
    if user is None:
        return
    sub = await white_internet_repo.get_subscription_by_user_id(session, user.id)
    sub_limit = max(1, getattr(sub, "device_limit", 1) or 1) if sub else 1
    _base_price, _base_price_int, _duration_days, base_quota_bytes = await _get_effective_tariff_info(session)
    tier_price = get_white_internet_tier_price(sub_limit, base_price=Decimal(_base_price))
    tier_price_int = int(tier_price)
    base_gb = int(base_quota_bytes // (1024**3))
    traffic_gb = sub_limit * base_gb

    balance_snapshot = await get_account_balance(session, user_id=user.id)
    balance = balance_snapshot.available

    is_trial = getattr(sub, "is_trial", False)
    trial_note = texts.WL_RENEW_TRIAL_NOTE if is_trial else ""

    builder = InlineKeyboardBuilder()
    if balance >= tier_price:
        balance_details = texts.WL_PREVIEW_BALANCE_OK.format(remaining=(balance - tier_price))
        builder.button(
            text=texts.BTN_WL_CONFIRM_RENEW.format(price=tier_price_int),
            callback_data="wl_renew_execute",
            style="success",
        )
    else:
        shortage = tier_price - balance
        balance_details = texts.WL_PREVIEW_BALANCE_SHORTAGE.format(shortage=shortage)
        builder.button(
            text=texts.BTN_WL_TOPUP_SHORTAGE.format(shortage=int(shortage)),
            callback_data=f"wl_topup_shortage:{int(shortage)}",
            style="success",
        )
    builder.button(text=texts.BTN_BACK, callback_data="white_internet")
    builder.adjust(1, 1)

    text = texts.WL_RENEW_PREVIEW_TEXT.format(
        price=tier_price_int,
        devices=sub_limit,
        traffic=traffic_gb,
        trial_note=trial_note,
        balance=balance,
        balance_details=balance_details,
    )
    await query.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.in_({"wl_renew_execute", "wl_renew_confirm"}))
async def process_white_internet_renew(query: CallbackQuery, session: AsyncSession):
    try:
        await query.answer()
    except Exception:
        pass
    user = await get_user_by_telegram_id(session, query.from_user.id)
    if user is None:
        return
    sub = await white_internet_repo.get_subscription_by_user_id(session, user.id)
    sub_limit = max(1, getattr(sub, "device_limit", 1) or 1) if sub else 1
    _base_price, _base_price_int, _duration_days, _ = await _get_effective_tariff_info(session)
    tier_price = get_white_internet_tier_price(sub_limit, base_price=Decimal(_base_price))

    balance_snapshot = await get_account_balance(session, user_id=user.id)
    if balance_snapshot.available < tier_price:
        shortage = tier_price - balance_snapshot.available
        kb = InlineKeyboardBuilder()
        kb.button(
            text=texts.BTN_WL_TOPUP_SHORTAGE.format(shortage=int(shortage)),
            callback_data=f"wl_topup_shortage:{int(shortage)}",
            style="success",
        )
        kb.button(text=texts.BTN_BACK, callback_data="white_internet")
        kb.adjust(1, 1)
        await query.message.edit_text(
            texts.WL_INSUFFICIENT_BALANCE_RENEW.format(
                price=int(tier_price),
                balance=balance_snapshot.available,
                shortage=shortage,
            ),
            reply_markup=kb.as_markup(),
            parse_mode="HTML",
        )
        return

    try:
        success, msg, _sub = await WhiteInternetService.renew_subscription(session, user.id)
    except Exception as exc:
        logger.error("Unexpected error during white internet renewal: %s", exc)
        success, msg = False, texts.WL_NO_SERVERS_AVAILABLE

    if not success:
        kb = InlineKeyboardBuilder()
        kb.button(text=texts.BTN_BACK, callback_data="white_internet")
        await query.message.edit_text(html.escape(msg), reply_markup=kb.as_markup())
        return
    await session.commit()
    await show_white_internet_menu(query, session)


@router.callback_query(F.data == "wl_topup_menu")
async def show_topup_menu(query: CallbackQuery, session: AsyncSession):
    try:
        await query.answer()
    except Exception:
        pass
    user = await get_user_by_telegram_id(session, query.from_user.id)
    if user is None:
        return
    sub = await white_internet_repo.get_subscription_by_user_id(session, user.id)
    if sub is None or sub.status not in (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.EXHAUSTED):
        sub_domain, sub_prefix = await _resolve_subscription_target(session, sub)
        await query.message.edit_text(
            texts.WL_SUB_NOT_READY,
            reply_markup=get_white_internet_overview_keyboard(
                sub, sub_domain, sub_prefix=sub_prefix, is_admin_user=is_admin(query.from_user.id)
            ),
        )
        return
    if getattr(sub, "is_trial", False):
        await query.answer(texts.WL_TRIAL_CANNOT_TOPUP, show_alert=True)
        return
    await query.message.edit_text(texts.WL_TOPUP_MENU_TEXT, reply_markup=get_topup_keyboard(), parse_mode="HTML")


@router.callback_query(F.data.startswith("wl_topup_preview:"))
async def process_topup_preview(query: CallbackQuery, session: AsyncSession):
    try:
        await query.answer()
    except Exception:
        pass
    try:
        pack_gb = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        return
    pack_price = WHITE_INTERNET_TOPUP_PACKS.get(pack_gb)
    if pack_price is None:
        return
    user = await get_user_by_telegram_id(session, query.from_user.id)
    if user is None:
        return
    balance_snapshot = await get_account_balance(session, user_id=user.id)
    balance = balance_snapshot.available

    builder = InlineKeyboardBuilder()
    if balance >= pack_price:
        balance_details = texts.WL_PREVIEW_BALANCE_OK.format(remaining=(balance - pack_price))
        builder.button(
            text=texts.BTN_WL_PAY_PACK.format(gb=pack_gb, price=int(pack_price)),
            callback_data=f"wl_topup_execute:{pack_gb}",
            style="success",
        )
    else:
        shortage = pack_price - balance
        balance_details = texts.WL_PREVIEW_BALANCE_SHORTAGE.format(shortage=shortage)
        builder.button(
            text=texts.BTN_WL_TOPUP_SHORTAGE.format(shortage=int(shortage)),
            callback_data=f"wl_topup_shortage:{int(shortage)}",
            style="success",
        )
    builder.button(text=texts.BTN_BACK, callback_data="wl_topup_menu")
    builder.adjust(1, 1)

    text = texts.WL_TOPUP_PREVIEW_TEXT.format(
        gb=pack_gb,
        price=int(pack_price),
        balance=balance,
        balance_details=balance_details,
    )
    await query.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith("wl_topup_execute:") | F.data.startswith("wl_topup_pack_"))
async def process_topup_execute(query: CallbackQuery, session: AsyncSession):
    try:
        await query.answer()
    except Exception:
        pass
    data = query.data
    if ":" in data:
        pack_str = data.split(":")[1]
    else:
        pack_str = data.replace("wl_topup_pack_", "")
    try:
        pack_gb = int(pack_str)
    except ValueError:
        return
    user = await get_user_by_telegram_id(session, query.from_user.id)
    if user is None:
        return
    pack_price = WHITE_INTERNET_TOPUP_PACKS.get(pack_gb)
    if pack_price is None:
        return

    balance_snapshot = await get_account_balance(session, user_id=user.id)
    if balance_snapshot.available < pack_price:
        shortage = pack_price - balance_snapshot.available
        kb = InlineKeyboardBuilder()
        kb.button(
            text=texts.BTN_WL_TOPUP_SHORTAGE.format(shortage=int(shortage)),
            callback_data=f"wl_topup_shortage:{int(shortage)}",
            style="success",
        )
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

    try:
        success, msg, _grant = await WhiteInternetService.topup_quota(
            session, user.id, pack_gb, actor_telegram_id=query.from_user.id
        )
    except Exception as exc:
        logger.error("Unexpected error during topup: %s", exc)
        success, msg = False, texts.WL_DEBIT_FAILED

    if not success:
        kb = InlineKeyboardBuilder()
        kb.button(text=texts.BTN_BACK, callback_data="white_internet")
        await query.message.edit_text(html.escape(msg), reply_markup=kb.as_markup())
        return
    await session.commit()
    await show_white_internet_menu(query, session)


# Backward-compatibility alias
process_topup_pack = process_topup_execute


@router.callback_query(F.data.startswith("wl_topup_shortage:"))
async def process_wl_topup_shortage(query: CallbackQuery, session: AsyncSession):
    try:
        shortage_val = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        try:
            await query.answer()
        except Exception:
            pass
        return

    if shortage_val <= 0:
        try:
            await query.answer()
        except Exception:
            pass
        return

    user = await get_user_by_telegram_id(session, query.from_user.id)
    if user is None:
        return

    if not await MaintenanceService.can_user_perform_action(session, query.from_user.id):
        try:
            await query.answer(texts.MAINTENANCE_DEFAULT_MESSAGE, show_alert=True)
        except Exception:
            pass
        return

    await _create_and_render_topup(
        target=query,
        session=session,
        user=user,
        amount=shortage_val,
        context={"source": "white_internet"},
    )


@router.callback_query(F.data == "wl_add_device_menu")
async def show_add_device_menu(query: CallbackQuery, session: AsyncSession):
    if not is_admin(query.from_user.id):
        await query.answer(texts.WL_ADMIN_ONLY_ALERT, show_alert=True)
        return
    try:
        await query.answer()
    except Exception:
        pass
    user = await get_user_by_telegram_id(session, query.from_user.id)
    if user is None:
        return
    sub = await white_internet_repo.get_subscription_by_user_id(session, user.id)
    if sub is None:
        return
    current_limit = max(1, getattr(sub, "device_limit", 1) or 1)
    if current_limit >= WHITE_INTERNET_MAX_DEVICE_LIMIT:
        await query.answer(texts.WL_DEVICE_LIMIT_MAX_REACHED, show_alert=True)
        return

    next_limit = current_limit + 1
    _base_price, _base_price_int, _duration_days, base_quota_bytes = await _get_effective_tariff_info(session)
    next_price = int(get_white_internet_tier_price(next_limit, base_price=Decimal(_base_price)))
    base_gb = int(base_quota_bytes // (1024**3))
    next_traffic = next_limit * base_gb

    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BTN_WL_ADD_DEVICE_CONFIRM, callback_data="wl_add_device_confirm")
    kb.button(text=texts.BTN_BACK, callback_data="white_internet")
    kb.adjust(1, 1)

    text = texts.WL_ADD_DEVICE_CONFIRM.format(
        next_price=next_price,
        next_limit=next_limit,
        next_traffic=next_traffic,
    )
    await query.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")


@router.callback_query(F.data == "wl_add_device_confirm")
async def process_add_device_confirm(query: CallbackQuery, session: AsyncSession):
    if not is_admin(query.from_user.id):
        await query.answer(texts.WL_ADMIN_ONLY_ALERT, show_alert=True)
        return
    try:
        await query.answer()
    except Exception:
        pass
    user = await get_user_by_telegram_id(session, query.from_user.id)
    if user is None:
        return

    price = WHITE_INTERNET_EXTRA_DEVICE_PRICE_RUB
    balance_snapshot = await get_account_balance(session, user_id=user.id)
    if balance_snapshot.available < price:
        shortage = price - balance_snapshot.available
        kb = InlineKeyboardBuilder()
        kb.button(
            text=texts.BTN_WL_TOPUP_SHORTAGE.format(shortage=int(shortage)),
            callback_data=f"wl_topup_shortage:{int(shortage)}",
            style="success",
        )
        kb.button(text=texts.BTN_BACK, callback_data="white_internet")
        kb.adjust(1, 1)
        await query.message.edit_text(
            texts.WL_INSUFFICIENT_BALANCE_BUY.format(
                price=int(price),
                balance=balance_snapshot.available,
                shortage=shortage,
            ),
            reply_markup=kb.as_markup(),
            parse_mode="HTML",
        )
        return

    success, msg, _sub = await WhiteInternetService.purchase_device_slot(
        session, user.id, actor_telegram_id=query.from_user.id
    )
    if not success:
        kb = InlineKeyboardBuilder()
        kb.button(text=texts.BTN_BACK, callback_data="white_internet")
        await query.message.edit_text(html.escape(msg), reply_markup=kb.as_markup())
        return

    await session.commit()
    await show_white_internet_menu(query, session)


@router.callback_query(F.data == "wl_show_link")
async def show_subscription_link(query: CallbackQuery, session: AsyncSession):
    try:
        await query.answer()
    except Exception:
        pass
    user = await get_user_by_telegram_id(session, query.from_user.id)
    if user is None:
        return
    sub = await white_internet_repo.get_subscription_by_user_id(session, user.id)
    sub_domain, sub_prefix = await _resolve_subscription_target(session, sub)
    if sub is None or sub.status != WhiteInternetStatus.ACTIVE:
        await query.message.edit_text(
            texts.WL_SUB_NOT_READY,
            reply_markup=get_white_internet_overview_keyboard(
                sub, sub_domain, sub_prefix=sub_prefix, is_admin_user=is_admin(query.from_user.id)
            ),
        )
        return
    if not sub_domain:
        kb = InlineKeyboardBuilder()
        kb.button(text=texts.BTN_BACK, callback_data="white_internet")
        await query.message.edit_text(texts.WL_DOMAIN_UNCONFIGURED, reply_markup=kb.as_markup())
        return

    sub_url = _build_subscription_url(sub_domain, sub.token, sub_prefix=sub_prefix)

    kb = InlineKeyboardBuilder()
    kb.button(
        text=texts.BTN_WL_COPY_LINK,
        copy_text=CopyTextButton(text=sub_url),
    )
    kb.button(text=texts.BTN_BACK, callback_data="white_internet")
    kb.adjust(1, 1)

    await query.message.edit_text(
        texts.WL_SHOW_LINK_TEXT.format(url=html.escape(sub_url)),
        reply_markup=kb.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "wl_reset_devices")
async def handle_wl_reset_devices(query: CallbackQuery, session: AsyncSession):
    user = await get_user_by_telegram_id(session, query.from_user.id)
    if user is None:
        try:
            await query.answer(texts.WL_USER_NOT_FOUND, show_alert=True)
        except Exception:
            pass
        return

    sub = await white_internet_repo.get_subscription_by_user_id(session, user.id)
    if sub is None:
        try:
            await query.answer(texts.WL_USER_NOT_FOUND, show_alert=True)
        except Exception:
            pass
        return

    try:
        await white_internet_repo.reset_active_hwids_atomic(session, sub.id)
        await session.commit()
        await query.answer(texts.WL_ALERT_DEVICES_RESET, show_alert=True)
    except WhiteInternetResetCooldownError as exc:
        await query.answer(
            texts.WL_RESET_COOLDOWN_ALERT.format(seconds=exc.remaining_seconds),
            show_alert=True,
        )
        return
    except Exception as exc:
        logger.warning("Failed to reset active hwids: %s", exc)
        await query.answer(texts.WL_RESET_DEVICES_FAILED, show_alert=True)
        return

    await show_white_internet_menu(query, session)


