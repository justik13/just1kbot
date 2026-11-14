"""Thin HTTP handlers for Mini App user dashboard, billing, devices, and referrals."""

from __future__ import annotations

from decimal import Decimal
import inspect
import logging
import math
from typing import Any, TypeVar
from urllib.parse import unquote
import uuid

from aiohttp import web
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select

from bot.miniapp.auth import require_authenticated_user
from bot.miniapp.schemas import (
    AutoRenewPayload,
    DevicePriorityPayload,
    StandardCheckoutPayload,
    StandardDowngradePayload,
    StandardQuotePayload,
    TopupPayload,
    WiCheckoutPayload,
    WiDowngradePayload,
)
from config.constants import (
    DEFAULT_FALLBACK_BOT_USERNAME,
    REFERRAL_ACTIVE_MIN_TOPUP_RUB,
    REFERRAL_WELCOME_DISCOUNT_PERCENT,
    STANDARD_PLANS,
    WHITE_INTERNET_BASE_PRICE_RUB,
    WHITE_INTERNET_EXTRA_DEVICE_PRICE_RUB,
    WHITE_INTERNET_MAX_DEVICE_LIMIT,
    WHITE_INTERNET_TOPUP_PACKS,
)
from config.settings import get_settings
from database.connection import session_scope
from database.models import Order, User
from database.repositories.account_ledger_repo import (
    get_account_balance,
    get_account_history,
)
from database.repositories.users_repo import create_user, get_user_by_telegram_id
from database.repositories.vless_subscription_repo import get_or_create_subscription
from database.repositories.white_internet_repo import get_subscription_by_user_id
from integrations.payment_gateways.factory import get_payment_gateway
from services import order_service, white_internet_service
from services.referral_bonus import get_referral_tier
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)
SchemaT = TypeVar("SchemaT", bound=BaseModel)


async def _parse_json_model(
    request: web.Request,
    model_cls: type[SchemaT],
) -> tuple[SchemaT | None, web.Response | None]:
    try:
        raw_body = await request.json()
    except Exception:
        return None, web.json_response({"ok": False, "error": "Invalid JSON body"}, status=400)

    if not isinstance(raw_body, dict):
        return None, web.json_response({"ok": False, "error": "JSON object expected"}, status=400)

    try:
        return model_cls.model_validate(raw_body), None
    except ValidationError as exc:
        first_err = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(part) for part in first_err.get("loc", ()))
        msg = first_err.get("msg", "Validation error")
        detail = f"{loc}: {msg}" if loc else msg
        return None, web.json_response({"ok": False, "error": detail}, status=400)


async def _ensure_user(session, ctx) -> User:
    user = await get_user_by_telegram_id(session, ctx.telegram_id)
    if user is None:
        user = await create_user(
            session,
            telegram_id=ctx.telegram_id,
            username=ctx.username or None,
            first_name=ctx.first_name or None,
        )
    return user


def _build_sub_url(token: str) -> str:
    settings = get_settings()
    domain = (settings.DOMAIN or "").strip().rstrip("/")
    if domain and not domain.startswith("http"):
        return f"https://{domain}/sub/{token}"
    if domain:
        return f"{domain}/sub/{token}"
    return f"/sub/{token}"


def _calc_payment_split(amount_rub: int, real_avail: int, bonus_avail: int, use_balance: bool) -> dict[str, int]:
    if not use_balance or amount_rub <= 0:
        return {"bonus_used": 0, "real_used": 0, "card_to_pay": max(0, amount_rub)}
    bonus_used = min(amount_rub, max(0, bonus_avail))
    rem = amount_rub - bonus_used
    real_used = min(rem, max(0, real_avail))
    card_to_pay = max(0, rem - real_used)
    return {
        "bonus_used": bonus_used,
        "real_used": real_used,
        "card_to_pay": card_to_pay,
    }


async def _get_referral_summary(
    session, user: User, *, bot_username: str | None = None
) -> dict[str, Any]:
    resolved_bot = (
        bot_username
        or get_settings().BOT_USERNAME
        or DEFAULT_FALLBACK_BOT_USERNAME
    ).lstrip("@")
    invited_count = int(
        await session.scalar(
            select(func.count(User.id)).where(
                User.referred_by == user.telegram_id,
                User.is_deleted.is_(False),
            )
        )
        or 0
    )
    active_count = int(
        await session.scalar(
            select(func.count(User.id)).where(
                User.referred_by == user.telegram_id,
                User.is_deleted.is_(False),
                User.last_payment_at.is_not(None),
            )
        )
        or 0
    )
    tier = get_referral_tier(active_count)
    return {
        "link": f"https://t.me/{resolved_bot}?start=ref_{user.telegram_id}",
        "invited_count": invited_count,
        "active_count": active_count,
        "tier_name": tier.name,
        "rate_pct": int(tier.rate * 100),
        "needed_for_next": tier.needed_for_next,
        "next_tier_name": tier.next_tier_name,
        "next_rate_pct": int(tier.next_rate * 100) if tier.next_rate is not None else None,
        "active_min_topup_rub": int(REFERRAL_ACTIVE_MIN_TOPUP_RUB),
        "welcome_discount_pct": int(REFERRAL_WELCOME_DISCOUNT_PERCENT * 100),
    }


async def _list_devices_safe(session, user_id: int, wi_device_limit: int, vless_sub) -> list[dict[str, Any]]:
    try:
        from database.repositories import user_devices_repo  # type: ignore[attr-defined]

        fn = getattr(user_devices_repo, "list_user_devices", None)
        if callable(fn):
            res = fn(session, user_id, wi_device_limit=wi_device_limit)
            return await res if inspect.isawaitable(res) else res
    except ImportError:
        pass

    hwids = vless_sub.active_hwids if vless_sub and isinstance(vless_sub.active_hwids, dict) else {}
    items: list[dict[str, Any]] = []
    for hwid, meta in hwids.items():
        info = meta if isinstance(meta, dict) else {"last_seen": meta}
        items.append({
            "hwid": hwid,
            "device_name": info.get("device_model") or info.get("device_name") or "Устройство",
            "platform": info.get("platform") or "",
            "os_version": info.get("os_version") or "",
            "app_version": info.get("app_version") or "",
            "last_seen_at": info.get("last_seen"),
            "bi_priority": 0,
            "is_bi_assigned": len(items) < wi_device_limit,
        })
    return items


def _resolve_bot_username(request: web.Request) -> str:
    app_name = request.app.get("bot_username") if hasattr(request, "app") else None
    raw = app_name or get_settings().BOT_USERNAME or DEFAULT_FALLBACK_BOT_USERNAME
    return str(raw).lstrip("@")


_LEDGER_ENTRY_LABELS: dict[str, str] = {
    "payment_credit": "Пополнение счёта",
    "purchase_debit": "Оплата доступа",
    "purchase_reversal": "Возврат оплаты",
    "admin_adjustment": "Начисление на счёт",
    "refund_debit": "Возврат средств",
}


def _serialize_ledger_entry(entry) -> dict[str, Any]:
    meta = entry.metadata_ if isinstance(entry.metadata_, dict) else {}
    raw_desc = str(meta.get("description") or "").strip()
    source = str(meta.get("source") or "").strip()
    if raw_desc:
        description = raw_desc
    elif source == "referral_bonus":
        description = "Реферальный бонус"
    elif entry.entry_type == "admin_adjustment":
        description = "Бонусное начисление" if Decimal(entry.amount) > 0 else "Коррекция баланса"
    else:
        description = _LEDGER_ENTRY_LABELS.get(entry.entry_type, "Операция по счёту")
    return {
        "id": entry.id,
        "type": entry.entry_type,
        "amount_rub": int(entry.amount),
        "description": description,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


async def api_user_dashboard_handler(request: web.Request) -> web.Response:
    """Return complete user cabinet state: balance, subscriptions, devices, referrals, history."""
    ctx, err_resp = require_authenticated_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    now = now_utc()
    async with session_scope() as session:
        user = await _ensure_user(session, ctx)
        bal = await get_account_balance(session, user_id=user.id)
        vless_sub = await get_or_create_subscription(session, user.id)
        wi_sub = await get_subscription_by_user_id(session, user.id)

        std_active = bool(user.subscription_end and user.subscription_end > now)
        wi_active = bool(
            wi_sub and wi_sub.status == "active" and wi_sub.expires_at and wi_sub.expires_at > now
        )
        wi_limit = int(wi_sub.device_limit or 1) if wi_active and wi_sub else 0

        add_slot_price = None
        if wi_active and wi_sub and wi_sub.device_limit < WHITE_INTERNET_MAX_DEVICE_LIMIT:
            calc_slot = getattr(white_internet_service, "calculate_wi_add_slot_price", None)
            if callable(calc_slot):
                add_slot_price = int(calc_slot(wi_sub.expires_at, now=now))
            else:
                rem_days = max(1, math.ceil((wi_sub.expires_at - now).total_seconds() / 86400.0))
                add_slot_price = int(
                    math.ceil(float(WHITE_INTERNET_EXTRA_DEVICE_PRICE_RUB) * min(30, rem_days) / 30.0)
                )

        devices = await _list_devices_safe(session, user.id, wi_limit, vless_sub)
        referrals = await _get_referral_summary(
            session, user, bot_username=_resolve_bot_username(request)
        )
        history_rows = await get_account_history(session, user_id=user.id, limit=30)

        cooldown_until = getattr(user, "standard_downgrade_cooldown_until", None)
        pending_limit = getattr(wi_sub, "pending_device_limit", None) if wi_sub else None
        pending_from = getattr(wi_sub, "pending_from", None) if wi_sub else None

        return web.json_response({
            "ok": True,
            "balance": {
                "real": int(bal.real_available),
                "bonus": int(bal.bonus_available),
                "total": int(bal.available),
            },
            "subscription_url": _build_sub_url(vless_sub.token),
            "standard": {
                "active": std_active,
                "expires_at": user.subscription_end.isoformat() if user.subscription_end else None,
                "device_limit": int(user.device_limit or 0),
                "auto_renew_enabled": bool(getattr(user, "auto_renew_enabled", False)),
                "downgrade_cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
            },
            "wi": {
                "active": wi_active,
                "status": wi_sub.status if wi_sub else "none",
                "expires_at": wi_sub.expires_at.isoformat() if wi_sub and wi_sub.expires_at else None,
                "device_limit": int(wi_sub.device_limit or 0) if wi_sub else 0,
                "traffic_used_bytes": int(wi_sub.traffic_used_bytes or 0) if wi_sub else 0,
                "traffic_limit_bytes": int(wi_sub.traffic_limit_bytes or 0) if wi_sub else 0,
                "auto_renew_enabled": bool(getattr(wi_sub, "auto_renew_enabled", False)) if wi_sub else False,
                "pending_device_limit": pending_limit,
                "pending_from": pending_from.isoformat() if pending_from else None,
                "add_slot_price_rub": add_slot_price,
            },
            "pricing": {
                "min_topup_rub": 20,
                "standard_devices_min": 2,
                "standard_devices_max": 10,
                "standard_durations": [7, 30, 90],
                "standard_plans": {
                    str(days): {"base_rub": int(base), "step_rub": int(step)}
                    for days, (base, step) in sorted(STANDARD_PLANS.items())
                },
                "wi_base_price_rub": int(WHITE_INTERNET_BASE_PRICE_RUB),
                "wi_extra_device_price_rub": int(WHITE_INTERNET_EXTRA_DEVICE_PRICE_RUB),
                "wi_max_devices": WHITE_INTERNET_MAX_DEVICE_LIMIT,
                "wi_topup_packs": [
                    {"gb": int(gb), "price_rub": int(price)}
                    for gb, price in sorted(WHITE_INTERNET_TOPUP_PACKS.items())
                ],
            },
            "devices": devices,
            "referrals": referrals,
            "history": [_serialize_ledger_entry(e) for e in history_rows],
        })


async def api_user_standard_quote_handler(request: web.Request) -> web.Response:
    """Calculate Standard tariff price or change quote for target devices and duration."""
    ctx, err_resp = require_authenticated_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, StandardQuotePayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    async with session_scope() as session:
        user = await _ensure_user(session, ctx)
        bal = await get_account_balance(session, user_id=user.id)

        quote_fn = getattr(order_service.OrderService, "quote_standard_change", None)
        if callable(quote_fn):
            quote = await quote_fn(session, user.id, payload.device_limit, payload.duration_days)
            amount_rub = int(quote.get("due_rub") or 0)
            split = _calc_payment_split(amount_rub, int(bal.real_available), int(bal.bonus_available), True)
            return web.json_response({"ok": True, "price_rub": amount_rub, "amount_rub": amount_rub, **quote, **split})

        calc_fn = getattr(order_service, "calculate_standard_price", None)
        if callable(calc_fn):
            price_rub = int(calc_fn(payload.device_limit, payload.duration_days))
        else:
            # Linear fallback until billing-core branch is merged
            base_30 = 90 + max(0, payload.device_limit - 2) * 30
            factor = {7: 0.38, 30: 1.0, 90: 2.7}[payload.duration_days]
            price_rub = int(round(base_30 * factor))

        split = _calc_payment_split(price_rub, int(bal.real_available), int(bal.bonus_available), True)
        return web.json_response({
            "ok": True,
            "device_limit": payload.device_limit,
            "duration_days": payload.duration_days,
            "price_rub": price_rub,
            "amount_rub": price_rub,
            "is_downgrade": bool(user.device_limit and payload.device_limit < user.device_limit),
            **split,
        })


async def api_user_standard_checkout_handler(request: web.Request) -> web.Response:
    """Purchase or extend Standard subscription with bonus-first balance + card/SBP fallback."""
    ctx, err_resp = require_authenticated_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, StandardCheckoutPayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    checkout_fn = getattr(order_service.OrderService, "checkout_order", None)
    if not callable(checkout_fn):
        return web.json_response(
            {"ok": False, "error": "Модуль оплаты обновляется, попробуйте позже"},
            status=503,
        )

    try:
        async with session_scope() as session:
            user = await _ensure_user(session, ctx)
            res = await checkout_fn(
                session,
                user_id=user.id,
                service_type="standard",
                device_limit=payload.device_limit,
                duration_days=payload.duration_days,
                use_balance=payload.use_balance,
                bot_username=_resolve_bot_username(request),
            )
            return web.json_response({"ok": True, **res})
    except order_service.ServiceUnavailableError as exc:
        logger.warning("Standard checkout unavailable for %s: %s", ctx.telegram_id, exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=503)
    except Exception as exc:
        logger.warning("Standard checkout error for %s: %s", ctx.telegram_id, exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=400)


async def api_user_standard_downgrade_handler(request: web.Request) -> web.Response:
    """Downgrade Standard device limit (subject to 7-day cooldown)."""
    ctx, err_resp = require_authenticated_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, StandardDowngradePayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    dg_fn = getattr(order_service.OrderService, "downgrade_standard_devices", None)
    if not callable(dg_fn):
        return web.json_response({"ok": False, "error": "Операция временно недоступна"}, status=503)

    try:
        async with session_scope() as session:
            user = await _ensure_user(session, ctx)
            res = await dg_fn(session, user.id, payload.target_device_limit)
            return web.json_response({"ok": True, **res})
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)


async def api_user_wi_checkout_handler(request: web.Request) -> web.Response:
    """Buy, renew, add device slot, or top up traffic for the high-resilience option."""
    ctx, err_resp = require_authenticated_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, WiCheckoutPayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    checkout_fn = getattr(order_service.OrderService, "checkout_order", None)
    if not callable(checkout_fn):
        return web.json_response(
            {"ok": False, "error": "Модуль оплаты обновляется, попробуйте позже"},
            status=503,
        )

    op_map = {
        "buy": "purchase",
        "renew": "renew",
        "add_slot": "add_device_slot",
        "topup": "topup",
    }
    try:
        async with session_scope() as session:
            user = await _ensure_user(session, ctx)
            res = await checkout_fn(
                session,
                user_id=user.id,
                service_type="white_internet",
                operation=op_map[payload.action],
                device_limit=payload.device_limit,
                gb=payload.gb,
                use_balance=payload.use_balance,
                bot_username=_resolve_bot_username(request),
            )
            return web.json_response({"ok": True, **res})
    except order_service.ServiceUnavailableError as exc:
        logger.warning("WI checkout unavailable for %s: %s", ctx.telegram_id, exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=503)
    except Exception as exc:
        logger.warning("WI checkout error for %s: %s", ctx.telegram_id, exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=400)


async def api_user_wi_downgrade_handler(request: web.Request) -> web.Response:
    """Schedule deferred device-limit downgrade for the high-resilience option."""
    ctx, err_resp = require_authenticated_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, WiDowngradePayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    sched_fn = getattr(white_internet_service, "schedule_wi_downgrade", None)
    if not callable(sched_fn):
        return web.json_response({"ok": False, "error": "Операция временно недоступна"}, status=503)

    async with session_scope() as session:
        user = await _ensure_user(session, ctx)
        ok, msg, _sub = await sched_fn(session, user.id, payload.target_device_limit)
        if not ok:
            return web.json_response({"ok": False, "error": msg}, status=400)
        return web.json_response({"ok": True, "message": msg})


async def api_user_wi_downgrade_cancel_handler(request: web.Request) -> web.Response:
    """Cancel scheduled deferred downgrade before expiration date."""
    ctx, err_resp = require_authenticated_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    cancel_fn = getattr(white_internet_service, "cancel_wi_downgrade", None)
    if not callable(cancel_fn):
        return web.json_response({"ok": False, "error": "Операция временно недоступна"}, status=503)

    async with session_scope() as session:
        user = await _ensure_user(session, ctx)
        ok, msg, _sub = await cancel_fn(session, user.id)
        if not ok:
            return web.json_response({"ok": False, "error": msg}, status=400)
        return web.json_response({"ok": True, "message": msg})


async def api_user_topup_handler(request: web.Request) -> web.Response:
    """Create a balance top-up payment (minimum 20 RUB)."""
    ctx, err_resp = require_authenticated_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, TopupPayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    try:
        async with session_scope() as session:
            user = await _ensure_user(session, ctx)
            checkout_fn = getattr(order_service.OrderService, "checkout_order", None)
            if callable(checkout_fn):
                res = await checkout_fn(
                    session,
                    user_id=user.id,
                    service_type="topup",
                    amount_rub=Decimal(payload.amount_rub),
                    use_balance=False,
                    bot_username=_resolve_bot_username(request),
                )
                return web.json_response({"ok": True, **res})

            order, pay_url = await order_service.OrderService.create_topup_order(
                session=session,
                user_id=user.id,
                amount_rub=Decimal(payload.amount_rub),
            )
            return web.json_response({
                "ok": True,
                "order_id": str(order.id),
                "status": order.status,
                "confirmation_url": pay_url,
            })
    except Exception as exc:
        logger.warning("Topup error for %s: %s", ctx.telegram_id, exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=400)


async def api_user_order_check_handler(request: web.Request) -> web.Response:
    """Check pending external payment status and settle immediately if paid."""
    ctx, err_resp = require_authenticated_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    raw_order_id = unquote(request.match_info.get("order_id", "")).strip()
    try:
        order_uuid = uuid.UUID(raw_order_id)
    except ValueError:
        return web.json_response({"ok": False, "error": "Некорректный идентификатор заказа"}, status=400)

    async with session_scope() as session:
        user = await _ensure_user(session, ctx)
        order = await session.scalar(
            select(Order).where(Order.id == order_uuid, Order.user_id == user.id)
        )
        if order is None:
            return web.json_response({"ok": False, "error": "Заказ не найден"}, status=404)
        if order.status != "pending" or not order.external_id:
            return web.json_response({
                "ok": True,
                "order_id": str(order.id),
                "status": order.status,
                "service_type": order.service_type,
            })
        user_id = user.id
        external_id = order.external_id
        payment_method = order.payment_method
        service_type = order.service_type

    try:
        gateway = get_payment_gateway(payment_method)
        status_res = await gateway.check_payment_status(external_id)
    except Exception as exc:
        logger.warning("Mini App order %s gateway check error: %s", order_uuid, exc)
        return web.json_response({
            "ok": True,
            "order_id": str(order_uuid),
            "status": "pending",
            "service_type": service_type,
        })

    if not (status_res.is_paid or status_res.is_canceled):
        return web.json_response({
            "ok": True,
            "order_id": str(order_uuid),
            "status": "pending",
            "service_type": service_type,
        })

    try:
        async with session_scope() as session:
            locked = await session.scalar(
                select(Order)
                .where(Order.id == order_uuid, Order.user_id == user_id)
                .with_for_update()
            )
            if locked is None:
                return web.json_response({"ok": False, "error": "Заказ не найден"}, status=404)
            if locked.status == "pending":
                if status_res.is_paid:
                    paid = await order_service.OrderService.mark_order_paid(
                        session,
                        locked.id,
                        external_id=external_id,
                        paid_amount_rub=status_res.amount_rub,
                    )
                    final_status = paid.status if paid else locked.status
                else:
                    order_service.OrderService.mark_order_canceled(
                        locked,
                        reason=status_res.cancellation_reason or "gateway_canceled",
                    )
                    final_status = locked.status
            else:
                final_status = locked.status
            return web.json_response({
                "ok": True,
                "order_id": str(locked.id),
                "status": final_status,
                "service_type": locked.service_type,
            })
    except Exception as exc:
        logger.warning("Mini App order %s check fulfillment error: %s", order_uuid, exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def api_user_auto_renew_handler(request: web.Request) -> web.Response:
    """Toggle balance auto-renewal flag (TODO worker hook) for Standard or WI."""
    ctx, err_resp = require_authenticated_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    payload, parse_err = await _parse_json_model(request, AutoRenewPayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    async with session_scope() as session:
        user = await _ensure_user(session, ctx)
        if payload.scope == "standard":
            if not hasattr(user, "auto_renew_enabled"):
                return web.json_response({"ok": False, "error": "Поле автопродления ещё не мигрировано"}, status=503)
            user.auto_renew_enabled = payload.enabled
            await session.flush()
            return web.json_response({"ok": True, "scope": "standard", "enabled": payload.enabled})

        wi_sub = await get_subscription_by_user_id(session, user.id)
        if wi_sub is None:
            return web.json_response({"ok": False, "error": "Подписка не найдена"}, status=404)
        if not hasattr(wi_sub, "auto_renew_enabled"):
            return web.json_response({"ok": False, "error": "Поле автопродления ещё не мигрировано"}, status=503)
        wi_sub.auto_renew_enabled = payload.enabled
        await session.flush()
        return web.json_response({"ok": True, "scope": "wi", "enabled": payload.enabled})


async def api_user_device_delete_handler(request: web.Request) -> web.Response:
    """Remove a bound HWID device."""
    ctx, err_resp = require_authenticated_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    hwid = unquote(request.match_info.get("hwid", "")).strip()
    if not hwid or len(hwid) > 128:
        return web.json_response({"ok": False, "error": "Invalid HWID"}, status=400)

    async with session_scope() as session:
        user = await _ensure_user(session, ctx)
        try:
            from database.repositories import user_devices_repo  # type: ignore[attr-defined]

            del_fn = getattr(user_devices_repo, "delete_user_device", None)
            if callable(del_fn):
                deleted = await del_fn(session, user.id, hwid)
                if not deleted:
                    return web.json_response({"ok": False, "error": "Устройство не найдено"}, status=404)
                return web.json_response({"ok": True})
        except ImportError:
            pass

        vless_sub = await get_or_create_subscription(session, user.id)
        hwids = dict(vless_sub.active_hwids or {})
        if hwid not in hwids:
            return web.json_response({"ok": False, "error": "Устройство не найдено"}, status=404)
        hwids.pop(hwid, None)
        vless_sub.active_hwids = hwids
        await session.flush()
        return web.json_response({"ok": True})


async def api_user_device_priority_handler(request: web.Request) -> web.Response:
    """Update manual high-resilience slot priority for a bound HWID device."""
    ctx, err_resp = require_authenticated_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    hwid = unquote(request.match_info.get("hwid", "")).strip()
    if not hwid or len(hwid) > 128:
        return web.json_response({"ok": False, "error": "Invalid HWID"}, status=400)

    payload, parse_err = await _parse_json_model(request, DevicePriorityPayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    async with session_scope() as session:
        user = await _ensure_user(session, ctx)
        try:
            from database.repositories import user_devices_repo  # type: ignore[attr-defined]

            prio_fn = getattr(user_devices_repo, "set_device_bi_priority", None)
            if callable(prio_fn):
                updated = await prio_fn(session, user.id, hwid, payload.priority)
                if not updated:
                    return web.json_response({"ok": False, "error": "Устройство не найдено"}, status=404)
                return web.json_response({"ok": True, "priority": payload.priority})
        except ImportError:
            pass

        return web.json_response(
            {"ok": False, "error": "Управление приоритетом станет доступно после обновления БД"},
            status=503,
        )
