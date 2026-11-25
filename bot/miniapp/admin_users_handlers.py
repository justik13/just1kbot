"""Admin HTTP handlers for user search, details, adjustments, devices, and token rotation."""

from __future__ import annotations

import html
from datetime import timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import unquote
from uuid import uuid4

from aiohttp import web
from sqlalchemy import delete, func, or_, select

import bot.miniapp.handlers_admin as _ha
from bot.miniapp.schemas import (
    AdminDirectMessagePayload,
    AdminUserAdjustPayload,
    AdminUserOptionalNotifyPayload,
)
from config import constants
from config.enums import (
    AdminAuditAction,
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from database.models import (
    AccountLedgerEntry,
    AuditLog,
    DeviceRegistrationEvent,
    Order,
    User,
    UserDevice,
    VlessSubscription,
    WhiteInternetSubscription,
)
from database.repositories.account_ledger_repo import (
    create_admin_adjustment,
    forgive_real_debt,
    get_account_balance,
    get_account_balances_batch,
    get_account_history,
)
from utils.datetime_helpers import now_utc


def _is_pre_reactivation_held_order(order: Order) -> bool:
    """Return True if a held order must settle before manual WI reactivation."""
    if getattr(order, "service_type", None) != "white_internet":
        return True
    meta = getattr(order, "metadata_", None) or {}
    op = meta.get("operation")
    if op == "purchase":
        return True
    if op in ("renew", "topup", "topup_gb", "add_device_slot"):
        return False
    t_bytes = int(getattr(order, "traffic_bytes", 0) or 0)
    d_days = int(getattr(order, "duration_days", 0) or 0)
    d_limit = getattr(order, "device_limit", None)
    if (d_limit and not t_bytes and d_days == 0) or (t_bytes > 0 and d_days == 0):
        return False
    return True


def _serialize_admin_ledger_entry(entry: AccountLedgerEntry) -> dict[str, Any]:
    """Serialize an AccountLedgerEntry for admin user details."""
    meta = entry.metadata_ if isinstance(entry.metadata_, dict) else {}
    entry_type = entry.entry_type
    if entry_type == "payment_credit" and meta.get("source") == "admin_debt_forgiveness":
        entry_type = "debt_forgiveness"
        note = str(meta.get("note") or "").strip()
        description = "Погашение долга" + (f" · {note}" if note else "")
    else:
        description = str(meta.get("description") or meta.get("reason") or "")
    return {
        "id": entry.id,
        "entry_type": entry_type,
        "bucket": entry.bucket,
        "amount": int(entry.amount),
        "description": description,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


async def api_admin_users_handler(request: web.Request) -> web.Response:
    """Return user statistics and paginated user list with balances and subscriptions."""
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    q = (request.query.get("q") or "").strip()
    flt = (request.query.get("filter") or "all").strip()
    limit = _ha._parse_bounded_int_query(request, "limit", default=10, min_value=1, max_value=50)
    offset = _ha._parse_bounded_int_query(request, "offset", default=0, min_value=0, max_value=_ha._MAX_INT32)

    now = now_utc()

    async with _ha.session_scope() as session:
        filter_counts = await _ha.users_repo.get_user_filter_counts(session)
        total_users = filter_counts.get("all", 0)
        active_standard = filter_counts.get("active_standard", 0)
        active_wi = filter_counts.get("active_wi", 0)
        total_balance_rub = Decimal(
            await session.scalar(
                select(func.coalesce(func.sum(AccountLedgerEntry.amount), 0))
                .join(User, User.id == AccountLedgerEntry.user_id)
                .where(User.is_deleted.is_(False))
            )
            or 0
        )

        stmt = select(User).where(User.is_deleted.is_(False))
        if flt == "active":
            stmt = stmt.where(_ha.users_repo.get_effective_active_condition(now))
        elif flt == "active_standard":
            stmt = stmt.where(
                User.id.in_(
                    select(VlessSubscription.user_id).where(VlessSubscription.expires_at > now)
                )
            )
        elif flt == "active_wi":
            stmt = stmt.where(_ha.users_repo.get_effective_wi_active_condition(now))
        elif flt == "expiring_3d":
            stmt = stmt.where(_ha.users_repo.get_effective_expiring_3d_condition(now))
        elif flt == "new_7d":
            stmt = stmt.where(User.created_at >= now - timedelta(days=7))
        elif flt == "expired":
            stmt = stmt.where(_ha.users_repo.get_effective_expired_condition(now))
        elif flt == "never":
            stmt = stmt.where(_ha.users_repo.get_effective_never_condition())
        elif flt == "banned":
            stmt = stmt.where(User.is_bot_blocked.is_(True))

        if q:
            if q.isdigit() and int(q) <= _ha._MAX_INT64:
                q_num = int(q)
                id_conds = [User.telegram_id == q_num]
                if 0 < q_num <= _ha._MAX_INT32:
                    id_conds.append(User.id == q_num)
                stmt = stmt.where(or_(*id_conds))
            else:
                pattern = f"%{q.lstrip('@')}%"
                stmt = stmt.where(
                    or_(
                        User.username.ilike(pattern),
                        User.first_name.ilike(pattern),
                        User.id.in_(
                            select(UserDevice.user_id).where(UserDevice.hwid.ilike(pattern))
                        ),
                        User.id.in_(
                            select(VlessSubscription.user_id).where(
                                or_(
                                    VlessSubscription.uuid.ilike(pattern),
                                    VlessSubscription.token.ilike(pattern),
                                )
                            )
                        ),
                        User.id.in_(
                            select(WhiteInternetSubscription.user_id).where(
                                or_(
                                    WhiteInternetSubscription.uuid.ilike(pattern),
                                    WhiteInternetSubscription.token.ilike(pattern),
                                )
                            )
                        ),
                    )
                )

        total_matching = int(
            await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        )

        stmt = stmt.order_by(User.created_at.desc(), User.id.desc()).limit(limit).offset(offset)
        users = (await session.scalars(stmt)).all()

        user_ids = [u.id for u in users]
        wi_map: dict[int, WhiteInternetSubscription] = {}
        vless_map: dict[int, VlessSubscription] = {}
        if user_ids:
            wi_rows = (
                await session.scalars(
                    select(WhiteInternetSubscription)
                    .where(WhiteInternetSubscription.user_id.in_(user_ids))
                    .order_by(WhiteInternetSubscription.id.asc())
                )
            ).all()
            wi_map = {w.user_id: w for w in wi_rows}
            vless_rows = (
                await session.scalars(
                    select(VlessSubscription).where(
                        VlessSubscription.user_id.in_(user_ids)
                    )
                )
            ).all()
            vless_map = {v.user_id: v for v in vless_rows}

        balances_map = await get_account_balances_batch(session, user_ids)
        items: list[dict] = []
        for u in users:
            bal = balances_map[u.id]
            wi = wi_map.get(u.id)
            vless = vless_map.get(u.id)
            std_end = vless.expires_at if vless else None
            std_limit = int(vless.device_limit or 0) if vless else 0
            std_active = bool(std_end and std_end > now)
            wi_active = bool(
                wi
                and wi.status in (
                    WhiteInternetStatus.ACTIVE,
                    WhiteInternetStatus.PENDING,
                    WhiteInternetStatus.EXHAUSTED,
                )
                and wi.expires_at
                and wi.expires_at > now
            )
            items.append({
                "id": u.id,
                "telegram_id": u.telegram_id,
                "username": u.username,
                "first_name": u.first_name,
                "real_balance": int(bal.real_available),
                "bonus_balance": int(bal.bonus_available),
                "total_balance": int(bal.available),
                "standard_active": std_active,
                "standard_end": std_end.isoformat() if std_end else None,
                "standard_device_limit": std_limit,
                "wi_active": wi_active,
                "wi_end": wi.expires_at.isoformat() if wi and wi.expires_at else None,
                "wi_device_limit": int(wi.device_limit or 0) if wi else 0,
                "financial_hold": bool(getattr(u, "financial_hold", False)),
                "topup_blocked": bool(getattr(u, "topup_blocked", False)),
                "created_at": u.created_at.isoformat() if u.created_at else None,
            })

        return web.json_response({
            "ok": True,
            "stats": {
                "total_users": total_users,
                "active_standard": active_standard,
                "active_wi": active_wi,
                "total_balance_rub": int(max(Decimal(0), total_balance_rub)),
            },
            "filter_counts": filter_counts,
            "total": total_matching,
            "offset": offset,
            "limit": limit,
            "users": items,
        })


async def api_admin_user_adjust_handler(request: web.Request) -> web.Response:
    """Admin action: adjust user balance or grant/extend Standard or White Internet access."""
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None:
        return err_resp
    if ctx is None:
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)

    target_user_id, id_err = _ha._parse_positive_int(request, "user_id", label="user id")
    if id_err is not None or target_user_id is None:
        return id_err  # type: ignore[return-value]

    payload, parse_err = await _ha._parse_json_model(request, AdminUserAdjustPayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    note = (payload.note or "").strip()

    async with _ha.session_scope() as session:
        user = await session.get(User, target_user_id)
        if user is None or getattr(user, "is_deleted", False):
            return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)

        if payload.action == "balance":
            assert payload.amount_rub is not None
            await _ha.acquire_user_account_lock(session, user.id)
            bal_before = await get_account_balance(session, user_id=user.id, for_update=True)
            if payload.amount_rub < 0 and abs(payload.amount_rub) > bal_before.bonus_available:
                return web.json_response(
                    {"ok": False, "error": "Недостаточно бонусных средств на балансе пользователя"},
                    status=400,
                )
            default_desc = "Бонусное пополнение (админ)" if payload.amount_rub > 0 else "Списание администратором"
            await create_admin_adjustment(
                session,
                user_id=user.id,
                signed_amount=payload.amount_rub,
                idempotency_key=f"miniapp:admin:{ctx.telegram_id}:{user.id}:{uuid4().hex}",
                metadata={
                    "source": "miniapp_admin",
                    "admin_telegram_id": ctx.telegram_id,
                    "description": note or default_desc,
                },
            )
            bal_after = await get_account_balance(session, user_id=user.id)
            await _ha.AuditService.log_action(
                session,
                admin_id=ctx.telegram_id,
                action=(
                    AdminAuditAction.ADMIN_BALANCE_TOPUP
                    if payload.amount_rub >= 0
                    else AdminAuditAction.ADMIN_BALANCE_DEDUCT
                ),
                target_type="user",
                target_id=user.id,
                details={
                    "amount_rub": payload.amount_rub,
                    "before": {
                        "real_balance": int(bal_before.real_available),
                        "bonus_balance": int(bal_before.bonus_available),
                        "total_balance": int(bal_before.available),
                    },
                    "after": {
                        "real_balance": int(bal_after.real_available),
                        "bonus_balance": int(bal_after.bonus_available),
                        "total_balance": int(bal_after.available),
                    },
                    "note": note or default_desc,
                },
            )
            bot = request.app.get("bot")
            should_notify = True if payload.notify_user is None else bool(payload.notify_user)
            if should_notify and bot is not None and payload.amount_rub != 0:
                sign_word = "начислено" if payload.amount_rub > 0 else "списано"
                msg_text = (
                    f"Администратор изменил ваш бонусный баланс: "
                    f"{sign_word} {abs(payload.amount_rub)} ₽.\n"
                    f"Текущий баланс: {int(bal_after.available)} ₽."
                )
                if payload.send_note_to_user and note:
                    msg_text += f"\nКомментарий: {html.escape(note)}"
                _ha._queue_user_notification(
                    session,
                    bot,
                    user_id=user.id,
                    telegram_id=user.telegram_id,
                    text=msg_text,
                )
            return web.json_response({
                "ok": True,
                "action": "balance",
                "real_balance": int(bal_after.real_available),
                "bonus_balance": int(bal_after.bonus_available),
                "total_balance": int(bal_after.available),
                "message": f"Баланс обновлён ({payload.amount_rub:+d} ₽)",
            })

        if payload.action == "forgive_debt":
            await _ha.acquire_user_account_lock(session, user.id)
            bal_before = await get_account_balance(session, user_id=user.id, for_update=True)
            if bal_before.debt <= 0:
                return web.json_response(
                    {"ok": False, "error": "У пользователя нет задолженности"},
                    status=400,
                )
            forgive_amount = bal_before.debt.quantize(Decimal("1.00"))
            if payload.amount_rub is not None:
                forgive_amount = min(forgive_amount, Decimal(payload.amount_rub).quantize(Decimal("1.00")))
            await forgive_real_debt(
                session,
                user_id=user.id,
                amount_rub=int(forgive_amount),
                admin_telegram_id=ctx.telegram_id,
                note=note or None,
            )
            bal_after = await get_account_balance(session, user_id=user.id)
            await _ha.AuditService.log_action(
                session,
                admin_id=ctx.telegram_id,
                action=AdminAuditAction.ADMIN_BALANCE_TOPUP,
                target_type="user",
                target_id=user.id,
                details={
                    "action": "forgive_debt",
                    "forgiven_rub": int(forgive_amount),
                    "debt_before": int(bal_before.debt),
                    "debt_after": int(bal_after.debt),
                    "note": note,
                },
            )
            return web.json_response({
                "ok": True,
                "action": "forgive_debt",
                "forgiven_rub": int(forgive_amount),
                "debt": int(bal_after.debt),
                "real_balance": int(bal_after.real_available),
                "total_balance": int(bal_after.available),
                "message": f"Долг погашен на {int(forgive_amount)} ₽",
            })

        if payload.action == "grant_standard":
            if payload.days is None:
                return web.json_response({"ok": False, "error": "Укажите количество дней"}, status=400)
            if payload.days == 0 and payload.device_limit is None:
                return web.json_response({"ok": False, "error": "Укажите дни или лимит устройств"}, status=400)
            if payload.device_limit is not None and not (
                constants.STANDARD_MIN_DEVICES <= payload.device_limit <= constants.STANDARD_MAX_DEVICES
            ):
                return web.json_response(
                    {
                        "ok": False,
                        "error": f"Лимит устройств должен быть от {constants.STANDARD_MIN_DEVICES} до {constants.STANDARD_MAX_DEVICES}",
                    },
                    status=400,
                )

            if payload.days < 0:
                await _ha.acquire_user_account_lock(session, user.id)
                vless_sub = await _ha.vless_subscription_repo.get_subscription_by_user_id(session, user.id)
                now = now_utc()
                if vless_sub is None or not vless_sub.expires_at or vless_sub.expires_at <= now:
                    return web.json_response(
                        {"ok": False, "error": "Подписка «Стандарт» неактивна или уже истекла"},
                        status=400,
                    )
                before_end = vless_sub.expires_at.isoformat() if vless_sub.expires_at else None
                before_limit = int(getattr(vless_sub, "device_limit", 0) or 0)
                new_end = vless_sub.expires_at - timedelta(days=abs(payload.days))
                vless_sub.expires_at = new_end
                limit_changed = (
                    payload.device_limit is not None
                    and payload.device_limit != before_limit
                )
                if payload.device_limit is not None:
                    vless_sub.device_limit = payload.device_limit
                await session.flush()
                await _ha.AuditService.log_action(
                    session,
                    admin_id=ctx.telegram_id,
                    action=AdminAuditAction.REDUCE,
                    target_type="user",
                    target_id=user.id,
                    details={
                        "service": "standard",
                        "days": payload.days,
                        "before": {"expires_at": before_end, "device_limit": before_limit},
                        "after": {
                            "expires_at": vless_sub.expires_at.isoformat() if vless_sub.expires_at else None,
                            "device_limit": int(vless_sub.device_limit or 0),
                        },
                        "note": note,
                    },
                )
                if new_end <= now:
                    vless_sub.is_active = False
                    vless_sub.version = (getattr(vless_sub, "version", 1) or 1) + 1
                    await session.flush()
                    _ha.VlessSubscriptionService.ensure_synced_background(
                        user.id, is_active=False, session=session
                    )
                elif limit_changed:
                    vless_sub.version = (getattr(vless_sub, "version", 1) or 1) + 1
                    await session.flush()
                    _ha.VlessSubscriptionService.ensure_synced_background(
                        user.id, session=session
                    )
                if payload.notify_user is True:
                    _ha._queue_user_notification(
                        session,
                        request.app.get("bot"),
                        user_id=user.id,
                        telegram_id=user.telegram_id,
                        text=f"Параметры вашей подписки «Стандарт» обновлены (сокращение на {abs(payload.days)} дн.).",
                    )
                return web.json_response({
                    "ok": True,
                    "action": "grant_standard",
                    "standard_end": vless_sub.expires_at.isoformat() if vless_sub.expires_at else None,
                    "standard_device_limit": int(vless_sub.device_limit or 0),
                    "message": f"Подписка «Стандарт» сокращена на {abs(payload.days)} дн.",
                })

            cur_vless = await _ha.get_or_create_subscription(session, user.id)
            before_end = cur_vless.expires_at.isoformat() if cur_vless.expires_at else None
            before_limit = int(getattr(cur_vless, "device_limit", 0) or 0)
            target_limit = payload.device_limit or (before_limit if before_limit >= 2 else 2)
            try:
                updated_user = await _ha.SubscriptionService.extend_subscription(
                    session,
                    user.telegram_id,
                    days=payload.days,
                    new_device_limit=target_limit,
                    admin_id=ctx.telegram_id,
                    reason=note or "miniapp_admin_grant",
                )
            except ValueError as exc:
                return _ha._safe_admin_error_response(exc)
            if updated_user is None:
                return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)
            updated_vless = await _ha.vless_subscription_repo.get_subscription_by_user_id(session, updated_user.id)
            await _ha.AuditService.log_action(
                session,
                admin_id=ctx.telegram_id,
                action=(
                    AdminAuditAction.ADMIN_SUB_EXTEND
                    if payload.days > 0
                    else AdminAuditAction.ADMIN_SUB_CHANGE
                ),
                target_type="user",
                target_id=user.id,
                details={
                    "service": "standard",
                    "days": payload.days,
                    "device_limit": target_limit,
                    "before": {"expires_at": before_end, "device_limit": before_limit},
                    "after": {
                        "expires_at": updated_vless.expires_at.isoformat() if updated_vless and updated_vless.expires_at else None,
                        "device_limit": int(updated_vless.device_limit or 0) if updated_vless else 0,
                    },
                    "note": note,
                },
            )
            msg_done = (
                "Лимит устройств «Стандарт» обновлён"
                if payload.days == 0
                else f"Подписка «Стандарт» выдана/продлена на {payload.days} дн."
            )
            if payload.notify_user is True:
                dm_text = (
                    f"Лимит устройств вашей подписки «Стандарт» обновлён: {target_limit}."
                    if payload.days == 0
                    else f"Ваша подписка «Стандарт» продлена на +{payload.days} дн."
                )
                _ha._queue_user_notification(
                    session,
                    request.app.get("bot"),
                    user_id=user.id,
                    telegram_id=user.telegram_id,
                    text=dm_text,
                )
            return web.json_response({
                "ok": True,
                "action": "grant_standard",
                "standard_end": updated_vless.expires_at.isoformat() if updated_vless and updated_vless.expires_at else None,
                "standard_device_limit": int(updated_vless.device_limit or 0) if updated_vless else 0,
                "message": msg_done,
            })

        if payload.action == "wi_traffic":
            await _ha.acquire_user_account_lock(session, user.id)
            locked_user = (
                await session.execute(
                    select(User)
                    .where(User.id == user.id, User.is_deleted.is_(False))
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if locked_user is None:
                return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)
            wi_sub = await _ha.get_subscription_by_user_id(session, user.id)
            if wi_sub is None:
                return web.json_response({"ok": False, "error": "Подписка «Белый интернет» не найдена"}, status=404)
            before_used = int(wi_sub.traffic_used_bytes or 0)
            before_base = int(wi_sub.base_traffic_bytes or 0)
            before_extra = int(wi_sub.extra_traffic_bytes or 0)
            t_action = payload.traffic_action
            if t_action == "reset_used":
                try:
                    updated_sub = await _ha.white_internet_repo.reset_traffic_used_atomic(session, wi_sub.id)
                except _ha.white_internet_repo.WhiteInternetError as exc:
                    return _ha._safe_admin_error_response(exc)
                await _ha._queue_wi_inline_sync(session, updated_sub, "wi_traffic_reset")
                await _ha.AuditService.log_action(
                    session,
                    admin_id=ctx.telegram_id,
                    action=AdminAuditAction.WHITE_INTERNET_TRAFFIC_RESET,
                    target_type="user",
                    target_id=user.id,
                    details={
                        "subscription_id": wi_sub.id,
                        "before": {"traffic_used_bytes": before_used},
                        "after": {"traffic_used_bytes": int(updated_sub.traffic_used_bytes or 0)},
                        "note": note,
                    },
                )
                if payload.notify_user is True:
                    _ha._queue_user_notification(
                        session,
                        request.app.get("bot"),
                        user_id=user.id,
                        telegram_id=user.telegram_id,
                        text="Счётчик трафика вашей подписки «Белый интернет» сброшен.",
                    )
                return web.json_response({
                    "ok": True,
                    "action": "wi_traffic",
                    "traffic_used_bytes": int(updated_sub.traffic_used_bytes or 0),
                    "message": "Счётчик трафика «Белого интернета» сброшен",
                })
            if t_action in ("add_gb", "set_base_gb"):
                if payload.gb is None or payload.gb < 1:
                    return web.json_response({"ok": False, "error": "Укажите объём в ГБ (>= 1)"}, status=400)
                bytes_val = int(payload.gb) * 1024 * 1024 * 1024
                try:
                    if t_action == "add_gb":
                        updated_sub = await _ha.white_internet_repo.add_extra_traffic_atomic(
                            session, wi_sub.id, bytes_val
                        )
                        audit_act = AdminAuditAction.WHITE_INTERNET_TRAFFIC_ADDED
                        msg_ok = f"Добавлено +{payload.gb} ГБ трафика"
                        dm_text = f"Вам добавлено +{payload.gb} ГБ трафика в подписке «Белый интернет»."
                    else:
                        updated_sub = await _ha.white_internet_repo.set_base_traffic_quota_atomic(
                            session, wi_sub.id, bytes_val
                        )
                        audit_act = AdminAuditAction.WHITE_INTERNET_QUOTA_SET
                        msg_ok = f"Базовая квота установлена: {payload.gb} ГБ"
                        dm_text = f"Базовая квота трафика «Белый интернет» обновлена: {payload.gb} ГБ."
                except _ha.white_internet_repo.WhiteInternetError as exc:
                    return _ha._safe_admin_error_response(exc)
                await _ha._queue_wi_inline_sync(session, updated_sub, "wi_traffic")
                await _ha.AuditService.log_action(
                    session,
                    admin_id=ctx.telegram_id,
                    action=audit_act,
                    target_type="user",
                    target_id=user.id,
                    details={
                        "subscription_id": wi_sub.id,
                        "gb": payload.gb,
                        "before": {
                            "base_traffic_bytes": before_base,
                            "extra_traffic_bytes": before_extra,
                        },
                        "after": {
                            "base_traffic_bytes": int(updated_sub.base_traffic_bytes or 0),
                            "extra_traffic_bytes": int(updated_sub.extra_traffic_bytes or 0),
                        },
                        "note": note,
                    },
                )
                if payload.notify_user is True:
                    _ha._queue_user_notification(
                        session,
                        request.app.get("bot"),
                        user_id=user.id,
                        telegram_id=user.telegram_id,
                        text=dm_text,
                    )
                return web.json_response({
                    "ok": True,
                    "action": "wi_traffic",
                    "base_traffic_bytes": int(updated_sub.base_traffic_bytes or 0),
                    "extra_traffic_bytes": int(updated_sub.extra_traffic_bytes or 0),
                    "message": msg_ok,
                })
            return web.json_response({"ok": False, "error": "Укажите traffic_action"}, status=400)

        if payload.action == "restrictions":
            await _ha.acquire_user_account_lock(session, user.id)
            locked_user = (
                await session.execute(
                    select(User)
                    .where(User.id == user.id, User.is_deleted.is_(False))
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if locked_user is None:
                return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)
            before_state = {
                "financial_hold": bool(locked_user.financial_hold),
                "topup_blocked": bool(locked_user.topup_blocked),
                "financial_block_reason": locked_user.financial_block_reason,
            }
            if payload.financial_hold is not None:
                locked_user.financial_hold = bool(payload.financial_hold)
                if not locked_user.financial_hold:
                    locked_user.financial_block_reason = None
                elif note:
                    locked_user.financial_block_reason = note
            if payload.topup_blocked is not None:
                locked_user.topup_blocked = bool(payload.topup_blocked)
            await session.flush()
            await _ha.SubscriptionService.sync_access_state(session, locked_user)

            now = now_utc()
            pre_orders: list[Order] = []
            post_orders: list[Order] = []
            if not locked_user.financial_hold:
                held_stmt = select(Order).where(
                    Order.user_id == locked_user.id,
                    Order.status == "paid",
                    Order.metadata_["settlement_held"].astext == "true",
                )
                if locked_user.topup_blocked:
                    held_stmt = held_stmt.where(Order.service_type != "topup")
                held_orders = list((await session.scalars(held_stmt)).all())
                held_orders.sort(
                    key=lambda o: 0 if getattr(o, "service_type", None) == "topup" else 1
                )
                for held_order in held_orders:
                    if _is_pre_reactivation_held_order(held_order):
                        pre_orders.append(held_order)
                    else:
                        post_orders.append(held_order)

            for held_order in pre_orders:
                await _ha.OrderService.mark_order_paid(session, held_order.id)

            wi_reactivated = False
            hold_cleared = (
                before_state["financial_hold"] is True
                and locked_user.financial_hold is False
            )
            should_reactivate_wi = hold_cleared or bool(payload.reactivate_wi)
            if should_reactivate_wi and not locked_user.financial_hold:
                wi_sub = await _ha.get_subscription_by_user_id(session, user.id)
                if (
                    wi_sub is not None
                    and wi_sub.status == WhiteInternetStatus.DISABLED
                    and wi_sub.expires_at
                    and wi_sub.expires_at > now
                    and wi_sub.status_reason in (None, "", "financial_hold")
                ):
                    other_live_wi_id = await session.scalar(
                        select(WhiteInternetSubscription.id)
                        .where(
                            WhiteInternetSubscription.user_id == user.id,
                            WhiteInternetSubscription.id != wi_sub.id,
                            WhiteInternetSubscription.status.in_([
                                WhiteInternetStatus.PENDING,
                                WhiteInternetStatus.ACTIVE,
                                WhiteInternetStatus.EXHAUSTED,
                            ]),
                        )
                        .limit(1)
                    )
                    if other_live_wi_id is None:
                        locked_wi = await _ha.white_internet_repo.get_subscription_with_lock(
                            session, wi_sub.id
                        )
                        if (
                            locked_wi is not None
                            and locked_wi.status == WhiteInternetStatus.DISABLED
                            and locked_wi.expires_at
                            and locked_wi.expires_at > now
                            and locked_wi.status_reason in (None, "", "financial_hold")
                        ):
                            total_quota = (locked_wi.base_traffic_bytes or 0) + (
                                locked_wi.extra_traffic_bytes or 0
                            )
                            used_quota = max(
                                0,
                                (locked_wi.traffic_used_bytes or 0)
                                - (locked_wi.traffic_overage_bytes or 0),
                            )
                            if total_quota > 0 and used_quota < total_quota:
                                locked_wi.status = WhiteInternetStatus.ACTIVE
                                locked_wi.status_reason = None
                                locked_wi.provisioning_status = (
                                    WhiteInternetProvisioningStatus.PENDING_UPDATE
                                )
                                locked_wi.desired_version = (locked_wi.desired_version or 1) + 1
                                await session.flush()
                                await _ha._queue_wi_inline_sync(
                                    session, locked_wi, "restrictions_reactivate_wi"
                                )
                                wi_reactivated = True
                            elif total_quota > 0 and used_quota >= total_quota:
                                locked_wi.status = WhiteInternetStatus.EXHAUSTED
                                locked_wi.status_reason = "quota_exhausted"
                                locked_wi.provisioning_status = (
                                    WhiteInternetProvisioningStatus.SYNCED_INACTIVE
                                )
                                await session.flush()

            for held_order in post_orders:
                await _ha.OrderService.mark_order_paid(session, held_order.id)
            await _ha.AuditService.log_action(
                session,
                admin_id=ctx.telegram_id,
                action=(
                    AdminAuditAction.PAYMENT_DISPUTE_OPENED
                    if (locked_user.financial_hold or locked_user.topup_blocked)
                    else AdminAuditAction.PAYMENT_DISPUTE_RESOLVED
                ),
                target_type="user",
                target_id=user.id,
                details={
                    "before": before_state,
                    "after": {
                        "financial_hold": bool(locked_user.financial_hold),
                        "topup_blocked": bool(locked_user.topup_blocked),
                        "financial_block_reason": locked_user.financial_block_reason,
                        "wi_reactivated": wi_reactivated,
                    },
                    "note": note,
                },
            )
            return web.json_response({
                "ok": True,
                "action": "restrictions",
                "financial_hold": bool(locked_user.financial_hold),
                "topup_blocked": bool(locked_user.topup_blocked),
                "wi_reactivated": wi_reactivated,
                "message": "Ограничения пользователя обновлены",
            })

        # grant_wi
        if payload.days is None:
            return web.json_response({"ok": False, "error": "Укажите количество дней"}, status=400)
        if payload.days == 0 and payload.device_limit is None:
            return web.json_response({"ok": False, "error": "Укажите дни или лимит устройств"}, status=400)
        if payload.device_limit is not None and not (
            1 <= payload.device_limit <= constants.WHITE_INTERNET_MAX_DEVICE_LIMIT
        ):
            return web.json_response(
                {
                    "ok": False,
                    "error": f"Лимит устройств должен быть от 1 до {constants.WHITE_INTERNET_MAX_DEVICE_LIMIT}",
                },
                status=400,
            )
        await _ha.acquire_user_account_lock(session, user.id)
        locked_user = (
            await session.execute(
                select(User)
                .where(User.id == user.id, User.is_deleted.is_(False))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if locked_user is None:
            return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)
        if locked_user.financial_hold:
            return web.json_response(
                {
                    "ok": False,
                    "error": "На аккаунте установлена финансовая блокировка. Сначала снимите её.",
                },
                status=400,
            )
        wi_sub = await _ha.get_subscription_by_user_id(session, user.id)
        if wi_sub is not None and wi_sub.status == WhiteInternetStatus.DISABLED and payload.days <= 0:
            return web.json_response(
                {
                    "ok": False,
                    "error": "Подписка «Белый интернет» отключена. Для активации укажите положительное число дней.",
                },
                status=400,
            )
        if wi_sub is None or (
            wi_sub.status == WhiteInternetStatus.DISABLED and payload.days > 0
        ):
            if payload.days <= 0:
                return web.json_response(
                    {"ok": False, "error": "Для новой активации укажите положительное число дней"},
                    status=400,
                )
            ok, msg, created = await _ha.WhiteInternetService.purchase_subscription(
                session,
                user.id,
                device_limit=payload.device_limit or 1,
                duration_days=payload.days,
            )
            if not ok:
                return web.json_response({"ok": False, "error": msg}, status=400)
            await _ha.AuditService.log_action(
                session,
                admin_id=ctx.telegram_id,
                action=AdminAuditAction.WHITE_INTERNET_EXTEND,
                target_type="user",
                target_id=user.id,
                details={"days": payload.days, "device_limit": payload.device_limit or 1, "note": note},
            )
            if payload.notify_user is True:
                _ha._queue_user_notification(
                    session,
                    request.app.get("bot"),
                    user_id=user.id,
                    telegram_id=user.telegram_id,
                    text=f"Вам активирована подписка «Белый интернет» на {payload.days} дн.",
                )
            end_iso = created.expires_at.isoformat() if created and created.expires_at else None
            return web.json_response({
                "ok": True,
                "action": "grant_wi",
                "wi_end": end_iso,
                "message": f"Подписка «Белый интернет» активирована на {payload.days} дн.",
            })

        locked_wi = await _ha.white_internet_repo.get_subscription_with_lock(session, wi_sub.id)
        if locked_wi is None:
            return web.json_response({"ok": False, "error": "Подписка не найдена"}, status=404)
        wi_sub = locked_wi
        before_wi_end = wi_sub.expires_at.isoformat() if wi_sub.expires_at else None
        before_wi_limit = int(wi_sub.device_limit or 0)
        now = now_utc()

        if payload.days < 0:
            if not wi_sub.expires_at or wi_sub.expires_at <= now:
                return web.json_response(
                    {"ok": False, "error": "Подписка «Белый интернет» уже истекла"},
                    status=400,
                )
        elif payload.days > 0:
            try:
                wi_sub = await _ha.white_internet_repo.extend_subscription_atomic(
                    session, wi_sub.id, payload.days
                )
            except (_ha.white_internet_repo.WhiteInternetError, ValueError) as exc:
                return _ha._safe_admin_error_response(exc)

        devlimit_changed = False
        transitioned_to_exhausted = False
        if payload.device_limit is not None and payload.device_limit != wi_sub.device_limit:
            devlimit_changed = True
            wi_sub.device_limit = payload.device_limit
            wi_sub.base_traffic_bytes = payload.device_limit * constants.WHITE_INTERNET_BASE_TRAFFIC_BYTES
            wi_sub.pending_device_limit = None
            wi_sub.pending_from = None
            wi_sub.desired_version = (wi_sub.desired_version or 1) + 1
            total_quota = (wi_sub.base_traffic_bytes or 0) + (wi_sub.extra_traffic_bytes or 0)
            used_quota = max(0, (wi_sub.traffic_used_bytes or 0) - (wi_sub.traffic_overage_bytes or 0))
            if (
                wi_sub.status == WhiteInternetStatus.ACTIVE
                and total_quota > 0
                and used_quota >= total_quota
            ):
                wi_sub.status = WhiteInternetStatus.EXHAUSTED
                wi_sub.status_reason = "quota_exhausted"
                wi_sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_DELETE
                transitioned_to_exhausted = True
            elif wi_sub.status == WhiteInternetStatus.EXHAUSTED and total_quota > used_quota:
                wi_sub.status = WhiteInternetStatus.ACTIVE
                wi_sub.status_reason = None
                wi_sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
            elif wi_sub.status == WhiteInternetStatus.ACTIVE:
                wi_sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
            await session.flush()
            await _ha.AuditService.log_action(
                session,
                admin_id=ctx.telegram_id,
                action=AdminAuditAction.WHITE_INTERNET_DEVLIMIT_SET,
                target_type="user",
                target_id=user.id,
                details={
                    "before": {"device_limit": before_wi_limit},
                    "after": {"device_limit": payload.device_limit},
                    "note": note,
                },
            )

        if payload.days == 0:
            if transitioned_to_exhausted:
                await _ha._queue_wi_deprovision_all(
                    session, user.id, wi_sub, context="admin_devlimit_exhausted"
                )
            elif devlimit_changed:
                await _ha._queue_wi_inline_sync(session, wi_sub, "grant_wi_devlimit")
            if payload.notify_user is True:
                _ha._queue_user_notification(
                    session,
                    request.app.get("bot"),
                    user_id=user.id,
                    telegram_id=user.telegram_id,
                    text=f"Лимит устройств вашей подписки «Белый интернет» обновлён: {wi_sub.device_limit}.",
                )
            end_iso = wi_sub.expires_at.isoformat() if wi_sub.expires_at else None
            return web.json_response({
                "ok": True,
                "action": "grant_wi",
                "wi_end": end_iso,
                "message": "Лимит устройств «Белый интернет» обновлён",
            })

        if payload.days < 0:
            new_end = wi_sub.expires_at - timedelta(days=abs(payload.days))
            if new_end <= now:
                wi_sub.expires_at = now
                wi_sub.status = WhiteInternetStatus.EXPIRED
                wi_sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_DELETE
                wi_sub.desired_version = (wi_sub.desired_version or 1) + 1
                await session.flush()
                await _ha._queue_wi_deprovision_all(
                    session, user.id, wi_sub, context="admin_reduce_expired"
                )
            else:
                wi_sub.expires_at = new_end
                await session.flush()
                if transitioned_to_exhausted:
                    await _ha._queue_wi_deprovision_all(
                        session, user.id, wi_sub, context="admin_devlimit_exhausted"
                    )
                elif devlimit_changed:
                    await _ha._queue_wi_inline_sync(session, wi_sub, "grant_wi_reduce_devlimit")
            await _ha.AuditService.log_action(
                session,
                admin_id=ctx.telegram_id,
                action=AdminAuditAction.REDUCE,
                target_type="user",
                target_id=user.id,
                details={
                    "service": "white_internet",
                    "days": payload.days,
                    "before": {"expires_at": before_wi_end},
                    "after": {"expires_at": wi_sub.expires_at.isoformat() if wi_sub.expires_at else None},
                    "note": note,
                },
            )
            if payload.notify_user is True:
                _ha._queue_user_notification(
                    session,
                    request.app.get("bot"),
                    user_id=user.id,
                    telegram_id=user.telegram_id,
                    text=f"Параметры вашей подписки «Белый интернет» обновлены (сокращение на {abs(payload.days)} дн.).",
                )
            return web.json_response({
                "ok": True,
                "action": "grant_wi",
                "wi_end": wi_sub.expires_at.isoformat() if wi_sub.expires_at else None,
                "message": f"Подписка «Белый интернет» сокращена на {abs(payload.days)} дн.",
            })

        if transitioned_to_exhausted:
            await _ha._queue_wi_deprovision_all(
                session, user.id, wi_sub, context="admin_extend_devlimit_exhausted"
            )
        else:
            await _ha._queue_wi_inline_sync(session, wi_sub, "grant_wi_extend")
        await _ha.AuditService.log_action(
            session,
            admin_id=ctx.telegram_id,
            action=AdminAuditAction.WHITE_INTERNET_EXTEND,
            target_type="user",
            target_id=user.id,
            details={
                "days": payload.days,
                "before": {"expires_at": before_wi_end},
                "after": {"expires_at": wi_sub.expires_at.isoformat() if wi_sub and wi_sub.expires_at else None},
                "note": note,
            },
        )
        if payload.notify_user is True:
            _ha._queue_user_notification(
                session,
                request.app.get("bot"),
                user_id=user.id,
                telegram_id=user.telegram_id,
                text=f"Ваша подписка «Белый интернет» продлена на +{payload.days} дн.",
            )
        end_iso = wi_sub.expires_at.isoformat() if wi_sub and wi_sub.expires_at else None
        return web.json_response({
            "ok": True,
            "action": "grant_wi",
            "wi_end": end_iso,
            "message": f"Подписка «Белый интернет» продлена на {payload.days} дн.",
        })


async def api_admin_user_details_handler(request: web.Request) -> web.Response:
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    target_user_id, id_err = _ha._parse_positive_int(request, "user_id", label="user id")
    if id_err is not None or target_user_id is None:
        return id_err  # type: ignore[return-value]

    async with _ha.session_scope() as session:
        user = await session.get(User, target_user_id)
        if user is None or getattr(user, "is_deleted", False):
            return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)

        bal = await get_account_balance(session, user_id=user.id)
        vless_sub = await _ha.vless_subscription_repo.get_subscription_by_user_id(session, user.id)
        wi_sub = await _ha.get_subscription_by_user_id(session, user.id)
        slot_usage = await _ha.SubscriptionService.get_device_slot_usage(
            session, user, vless_sub=vless_sub, wi_sub=wi_sub
        )
        devices = slot_usage["devices"]

        inviter = None
        if user.referred_by:
            ref_user = await session.scalar(select(User).where(User.telegram_id == user.referred_by))
            if ref_user is not None:
                inviter = {
                    "id": ref_user.id,
                    "telegram_id": ref_user.telegram_id,
                    "username": ref_user.username,
                    "first_name": ref_user.first_name,
                }

        now = now_utc()
        churn_events_24h = int(
            (
                await session.scalar(
                    select(func.count(DeviceRegistrationEvent.id)).where(
                        DeviceRegistrationEvent.user_id == user.id,
                        DeviceRegistrationEvent.created_at >= now - timedelta(hours=24),
                    )
                )
            )
            or 0
        )

        recent_orders_rows = (
            await session.scalars(
                select(Order)
                .where(Order.user_id == user.id)
                .order_by(Order.created_at.desc(), Order.id.desc())
                .limit(20)
            )
        ).all()
        recent_ledger_rows = await get_account_history(session, user_id=user.id, limit=25)
        recent_device_event_rows = (
            await session.scalars(
                select(DeviceRegistrationEvent)
                .where(DeviceRegistrationEvent.user_id == user.id)
                .order_by(DeviceRegistrationEvent.created_at.desc(), DeviceRegistrationEvent.id.desc())
                .limit(20)
            )
        ).all()
        recent_audit_rows = (
            await session.scalars(
                select(AuditLog)
                .where(
                    func.lower(AuditLog.target_type) == "user",
                    or_(AuditLog.target_id == user.id, AuditLog.target_id == user.telegram_id),
                )
                .order_by(AuditLog.id.desc())
                .limit(20)
            )
        ).all()

        device_meta_by_hwid = {
            str(d.get("hwid")): d
            for d in devices
            if d.get("hwid")
        }

        sub_token = vless_sub.token if vless_sub and vless_sub.token else (wi_sub.token if wi_sub else None)
        return web.json_response({
            "ok": True,
            "server_now": now.isoformat(),
            "user": {
                "id": user.id,
                "telegram_id": user.telegram_id,
                "username": user.username,
                "first_name": user.first_name,
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "last_payment_at": user.last_payment_at.isoformat() if user.last_payment_at else None,
                "is_bot_blocked": bool(getattr(user, "is_bot_blocked", False)),
                "real_balance": int(bal.real_available),
                "bonus_balance": int(bal.bonus_available),
                "total_balance": int(bal.available),
                "debt": int(bal.debt),
                "real_position": int(bal.real_position),
                "standard_end": vless_sub.expires_at.isoformat() if vless_sub and vless_sub.expires_at else None,
                "standard_device_limit": int(vless_sub.device_limit or 0) if vless_sub else 0,
                "standard": (
                    {
                        "is_active": bool(vless_sub.is_active),
                        "auto_renew_enabled": bool(vless_sub.auto_renew_enabled),
                        "last_synced_at": vless_sub.last_synced_at.isoformat() if vless_sub.last_synced_at else None,
                    }
                    if vless_sub
                    else None
                ),
                "financial_hold": bool(getattr(user, "financial_hold", False)),
                "topup_blocked": bool(getattr(user, "topup_blocked", False)),
                "financial_block_reason": user.financial_block_reason,
                "monthly_vless_bytes": int(getattr(user, "monthly_vless_bytes", 0) or 0),
                "total_traffic_bytes": int(getattr(user, "total_traffic_bytes", 0) or 0),
                "monthly_wi_bytes": int(getattr(user, "monthly_wi_bytes", 0) or 0),
                "total_wi_traffic_bytes": int(getattr(user, "total_wi_traffic_bytes", 0) or 0),
                "churn_events_24h": churn_events_24h,
                "subscription_url": _ha._build_sub_url(sub_token) if sub_token else None,
                "subscription_backup_url": _ha._build_backup_sub_url(sub_token) if sub_token else None,
                "wi": (
                    {
                        "id": wi_sub.id,
                        "status": str(wi_sub.status),
                        "status_reason": wi_sub.status_reason,
                        "provisioning_status": str(wi_sub.provisioning_status),
                        "expires_at": wi_sub.expires_at.isoformat() if wi_sub.expires_at else None,
                        "device_limit": int(wi_sub.device_limit or 0),
                        "pending_device_limit": wi_sub.pending_device_limit,
                        "auto_renew_enabled": bool(wi_sub.auto_renew_enabled),
                        "desired_version": int(wi_sub.desired_version or 1),
                        "actual_version": int(wi_sub.actual_version or 0),
                        "node_sync_state": wi_sub.node_sync_state if isinstance(wi_sub.node_sync_state, dict) else {},
                        "last_synced_at": wi_sub.last_synced_at.isoformat() if wi_sub.last_synced_at else None,
                        "last_sync_error": wi_sub.last_sync_error,
                        "base_traffic_bytes": int(wi_sub.base_traffic_bytes or 0),
                        "extra_traffic_bytes": int(wi_sub.extra_traffic_bytes or 0),
                        "traffic_used_bytes": int(wi_sub.traffic_used_bytes or 0),
                    }
                    if wi_sub
                    else None
                ),
                "inviter": inviter,
                "devices": [
                    {
                        "hwid": d.get("hwid"),
                        "device_model": d.get("device_name"),
                        "platform": d.get("platform"),
                        "app_version": d.get("app_version"),
                        "is_std_priority": int(d.get("std_priority") or 0) > 0,
                        "is_std_assigned": bool(d.get("is_std_assigned", False)),
                        "is_bi_priority": int(d.get("bi_priority") or 0) > 0,
                        "is_bi_assigned": bool(d.get("is_bi_assigned", False)),
                        "last_seen_at": d.get("last_seen_at"),
                    }
                    for d in devices
                ],
                "recent_orders": [
                    {
                        "id": str(o.id),
                        "status": o.status,
                        "kind": o.service_type,
                        "amount_rub": float(o.amount_rub),
                        "description": o.description or "",
                        "external_id": o.external_id,
                        "created_at": o.created_at.isoformat() if o.created_at else None,
                        "paid_at": o.paid_at.isoformat() if o.paid_at else None,
                        "refunded_at": o.refunded_at.isoformat() if o.refunded_at else None,
                        "metadata_flags": _ha.admin_tools_service.extract_order_metadata_flags(o.metadata_),
                    }
                    for o in recent_orders_rows
                ],
                "recent_ledger": [
                    _serialize_admin_ledger_entry(entry)
                    for entry in recent_ledger_rows
                ],
                "recent_device_events": [
                    {
                        "id": ev.id,
                        "hwid": ev.hwid,
                        "device_model": (device_meta_by_hwid.get(ev.hwid) or {}).get("device_name"),
                        "platform": (device_meta_by_hwid.get(ev.hwid) or {}).get("platform"),
                        "created_at": ev.created_at.isoformat() if ev.created_at else None,
                    }
                    for ev in recent_device_event_rows
                ],
                "recent_audit": [
                    {
                        "id": a.id,
                        "action": a.action,
                        "admin_telegram_id": a.admin_id,
                        "details": a.details or "",
                        "created_at": a.created_at.isoformat() if a.created_at else None,
                    }
                    for a in recent_audit_rows
                ],
            },
        })


async def api_admin_user_devices_reset_handler(request: web.Request) -> web.Response:
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    target_user_id, id_err = _ha._parse_positive_int(request, "user_id", label="user id")
    if id_err is not None or target_user_id is None:
        return id_err  # type: ignore[return-value]

    notify_user = False
    raw_text = await request.text()
    if raw_text.strip():
        opt_payload, parse_err = await _ha._parse_json_model(request, AdminUserOptionalNotifyPayload)
        if parse_err is not None or opt_payload is None:
            return parse_err  # type: ignore[return-value]
        notify_user = bool(opt_payload.notify_user)

    async with _ha.session_scope() as session:
        await _ha.acquire_user_account_lock(session, target_user_id)
        user = await session.scalar(
            select(User).where(User.id == target_user_id).with_for_update()
        )
        if user is None or getattr(user, "is_deleted", False):
            return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)

        vless_sub = await _ha.vless_subscription_repo.get_subscription_by_user_id(session, user.id)
        wi_sub = await _ha.get_subscription_by_user_id(session, user.id)
        old_vless_uuid: str | None = None
        if vless_sub is not None:
            old_vless_uuid, _new_vless_uuid = await _ha.vless_subscription_repo.reset_hwids(
                session, vless_sub.id
            )
            vless_sub = (
                await _ha.vless_subscription_repo.get_subscription_by_user_id(session, user.id)
                or vless_sub
            )

        existing_devs = (await session.scalars(select(UserDevice).where(UserDevice.user_id == user.id))).all()
        dev_uuids = [d.device_uuid for d in existing_devs if getattr(d, "device_uuid", None)]
        if vless_sub is not None and dev_uuids:
            vless_sub = (
                await _ha.vless_subscription_repo.add_pending_revocation_uuids(
                    session, vless_sub.id, dev_uuids
                )
                or vless_sub
            )

        dev_del_res = await session.execute(
            delete(UserDevice).where(UserDevice.user_id == user.id)
        )
        events_del_res = await session.execute(
            delete(DeviceRegistrationEvent).where(DeviceRegistrationEvent.user_id == user.id)
        )

        if vless_sub is not None:
            sub_ver = getattr(vless_sub, "version", None)
            sub_id = vless_sub.id
            vless_rev_uuids = list(
                dict.fromkeys([u for u in [old_vless_uuid, *dev_uuids] if u])
            )
            for u_uuid in vless_rev_uuids:
                _ha.VlessSubscriptionService.deprovision_background(
                    u_uuid,
                    version=sub_ver,
                    sub_id=sub_id,
                    session=session,
                    services=("vless",),
                )
            _ha.VlessSubscriptionService.ensure_synced_background(user.id, session=session)

        if wi_sub is not None and dev_uuids:
            wi_sub = (
                await _ha.white_internet_repo.get_subscription_with_lock(session, wi_sub.id)
                or wi_sub
            )
            await _ha.white_internet_repo.add_pending_revocation_uuids(
                session, wi_sub.id, dev_uuids
            )
            wi_sub.desired_version = (wi_sub.desired_version or 1) + 1
            if wi_sub.status in (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.PENDING):
                wi_sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
            await session.flush()
            await _ha._queue_wi_revoke_uuids(
                session, wi_sub, dev_uuids, context="admin_devices_reset"
            )
            if wi_sub.status in (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.PENDING):
                await _ha._queue_wi_inline_sync(session, wi_sub, "devices_reset")
        await _ha.AuditService.log_action(
            session,
            admin_id=ctx.telegram_id,
            action=AdminAuditAction.VLESS_HWID_RESET,
            target_type="user",
            target_id=user.id,
            details={
                "deleted_devices": int(dev_del_res.rowcount or 0),
                "deleted_churn_events": int(events_del_res.rowcount or 0),
            },
        )
        if notify_user:
            _ha._queue_user_notification(
                session,
                request.app.get("bot"),
                user_id=user.id,
                telegram_id=user.telegram_id,
                text="Привязки устройств к вашей подписке сброшены администратором. Подключите устройства заново.",
            )

    return web.json_response({"ok": True, "message": "Привязки устройств сброшены"})


async def api_admin_user_device_delete_handler(request: web.Request) -> web.Response:
    """Delete a single bound HWID device for a user and queue deprovisioning/sync."""
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    target_user_id, id_err = _ha._parse_positive_int(request, "user_id", label="user id")
    if id_err is not None or target_user_id is None:
        return id_err  # type: ignore[return-value]

    hwid = unquote(request.match_info.get("hwid", "")).strip()
    if not hwid or len(hwid) > 128:
        return web.json_response({"ok": False, "error": "Invalid HWID"}, status=400)

    async with _ha.session_scope() as session:
        await _ha.acquire_user_account_lock(session, target_user_id)
        user = await session.scalar(
            select(User).where(User.id == target_user_id).with_for_update()
        )
        if user is None or getattr(user, "is_deleted", False):
            return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)

        deleted = await _ha.user_devices_repo.delete_user_device(session, user.id, hwid)
        if deleted is None:
            return web.json_response({"ok": False, "error": "Устройство не найдено"}, status=404)

        deleted_uuid = getattr(deleted, "device_uuid", None)
        if deleted_uuid:
            vless_sub = await _ha.vless_subscription_repo.get_subscription_by_user_id(session, user.id)
            sub_id = vless_sub.id if vless_sub else None
            if vless_sub is not None:
                await _ha.vless_subscription_repo.add_pending_revocation_uuids(
                    session, vless_sub.id, [deleted_uuid]
                )
            sub_ver = getattr(vless_sub, "version", None) if vless_sub else None

            _ha.VlessSubscriptionService.deprovision_background(
                deleted_uuid,
                version=sub_ver,
                sub_id=sub_id,
                session=session,
                services=("vless",),
            )
            _ha.VlessSubscriptionService.ensure_synced_background(user.id, session=session)

            wi_sub = await _ha.get_subscription_by_user_id(session, user.id)
            if wi_sub is not None:
                wi_sub = (
                    await _ha.white_internet_repo.get_subscription_with_lock(session, wi_sub.id)
                    or wi_sub
                )
                await _ha.white_internet_repo.add_pending_revocation_uuids(
                    session, wi_sub.id, [deleted_uuid]
                )
                wi_sub.desired_version = (wi_sub.desired_version or 1) + 1
                if wi_sub.status in (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.PENDING):
                    wi_sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
                await session.flush()
                await _ha._queue_wi_revoke_uuids(
                    session, wi_sub, [deleted_uuid], context="admin_device_delete"
                )
                if wi_sub.status in (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.PENDING):
                    await _ha._queue_wi_inline_sync(session, wi_sub, "device_delete")

        await _ha.AuditService.log_action(
            session,
            admin_id=ctx.telegram_id,
            action=AdminAuditAction.VLESS_HWID_RESET,
            target_type="user",
            target_id=user.id,
            details={"action": "delete_device", "hwid": hwid},
        )

    return web.json_response({"ok": True, "hwid": hwid, "message": "Устройство удалено"})


async def api_admin_user_resync_handler(request: web.Request) -> web.Response:
    """Manually trigger background/inline sync for user's Standard and WI subscriptions."""
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    target_user_id, id_err = _ha._parse_positive_int(request, "user_id", label="user id")
    if id_err is not None or target_user_id is None:
        return id_err  # type: ignore[return-value]

    async with _ha.session_scope() as session:
        await _ha.acquire_user_account_lock(session, target_user_id)
        user = await session.scalar(
            select(User).where(User.id == target_user_id).with_for_update()
        )
        if user is None or getattr(user, "is_deleted", False):
            return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)

        vless_sub = await _ha.vless_subscription_repo.get_subscription_by_user_id(session, user.id)
        wi_sub = await _ha.get_subscription_by_user_id(session, user.id)
        synced_standard = False
        synced_wi = False

        if vless_sub is not None:
            _ha.VlessSubscriptionService.ensure_synced_background(user.id, session=session)
            synced_standard = True
        if wi_sub is not None and wi_sub.status in (
            WhiteInternetStatus.ACTIVE,
            WhiteInternetStatus.PENDING,
        ):
            locked_wi = await _ha.white_internet_repo.get_subscription_with_lock(session, wi_sub.id)
            if locked_wi is not None and locked_wi.status in (
                WhiteInternetStatus.ACTIVE,
                WhiteInternetStatus.PENDING,
            ):
                locked_wi.desired_version = (locked_wi.desired_version or 1) + 1
                locked_wi.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
                await session.flush()
                await _ha._queue_wi_inline_sync(session, locked_wi, "manual_resync")
                synced_wi = True

        await _ha.AuditService.log_action(
            session,
            admin_id=ctx.telegram_id,
            action=AdminAuditAction.ADMIN_SUB_CHANGE,
            target_type="user",
            target_id=user.id,
            details={"action": "manual_resync", "standard": synced_standard, "wi": synced_wi},
        )

    return web.json_response({
        "ok": True,
        "synced_standard": synced_standard,
        "synced_wi": synced_wi,
        "message": "Синхронизация запущена",
    })


async def api_admin_user_token_rotate_handler(request: web.Request) -> web.Response:
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    target_user_id, id_err = _ha._parse_positive_int(request, "user_id", label="user id")
    if id_err is not None or target_user_id is None:
        return id_err  # type: ignore[return-value]

    notify_user = False
    raw_text = await request.text()
    if raw_text.strip():
        opt_payload, parse_err = await _ha._parse_json_model(request, AdminUserOptionalNotifyPayload)
        if parse_err is not None or opt_payload is None:
            return parse_err  # type: ignore[return-value]
        notify_user = bool(opt_payload.notify_user)

    async with _ha.session_scope() as session:
        await _ha.acquire_user_account_lock(session, target_user_id)
        user = await session.scalar(
            select(User).where(User.id == target_user_id).with_for_update()
        )
        if user is None or getattr(user, "is_deleted", False):
            return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)

        vless_sub = await _ha.vless_subscription_repo.get_subscription_by_user_id(session, user.id)
        wi_sub = await _ha.get_subscription_by_user_id(session, user.id)
        new_token: str | None = None
        old_vless_uuid: str | None = None
        old_wi_uuid: str | None = wi_sub.uuid if wi_sub is not None else None
        if vless_sub is not None:
            new_token, old_vless_uuid, _new_u = await _ha.vless_subscription_repo.rotate_token(
                session, vless_sub.id, reset_hwids=True
            )
            vless_sub = (
                await _ha.vless_subscription_repo.get_subscription_by_user_id(session, user.id)
                or vless_sub
            )
        if wi_sub is not None:
            wi_token = await _ha.white_internet_repo.reset_subscription_token_atomic(
                session, wi_sub.id, new_token=new_token, reset_hwids=True
            )
            new_token = new_token or wi_token
            wi_sub = (
                await _ha.white_internet_repo.get_subscription_with_lock(session, wi_sub.id)
                or wi_sub
            )

        existing_devs = (await session.scalars(select(UserDevice).where(UserDevice.user_id == user.id))).all()
        dev_uuids = [d.device_uuid for d in existing_devs if getattr(d, "device_uuid", None)]

        if vless_sub is not None:
            if dev_uuids:
                vless_sub = (
                    await _ha.vless_subscription_repo.add_pending_revocation_uuids(
                        session, vless_sub.id, dev_uuids
                    )
                    or vless_sub
                )
            vless_rev_uuids = list(
                dict.fromkeys([u for u in [old_vless_uuid, *dev_uuids] if u])
            )
            sub_ver = getattr(vless_sub, "version", None)
            for u_uuid in vless_rev_uuids:
                _ha.VlessSubscriptionService.deprovision_background(
                    u_uuid,
                    version=sub_ver,
                    sub_id=vless_sub.id,
                    session=session,
                    services=("vless",),
                )

        if wi_sub is not None:
            wi_rev_uuids = list(
                dict.fromkeys([u for u in [old_wi_uuid, *dev_uuids] if u])
            )
            if wi_rev_uuids:
                await _ha.white_internet_repo.add_pending_revocation_uuids(
                    session, wi_sub.id, wi_rev_uuids
                )
                if wi_sub.status in (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.PENDING):
                    wi_sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
                await session.flush()
                await _ha._queue_wi_revoke_uuids(
                    session, wi_sub, wi_rev_uuids, context="admin_token_rotate"
                )

        await session.execute(
            delete(UserDevice).where(UserDevice.user_id == user.id)
        )
        if vless_sub is not None:
            _ha.VlessSubscriptionService.ensure_synced_background(user.id, session=session)
        if wi_sub is not None:
            await _ha._queue_wi_inline_sync(session, wi_sub, "token_rotate")
        await _ha.AuditService.log_action(
            session,
            admin_id=ctx.telegram_id,
            action=AdminAuditAction.VLESS_TOKEN_RESET,
            target_type="user",
            target_id=user.id,
        )
        if notify_user:
            _ha._queue_user_notification(
                session,
                request.app.get("bot"),
                user_id=user.id,
                telegram_id=user.telegram_id,
                text="Ссылка вашей подписки перевыпущена администратором. Откройте приложение для обновления.",
            )

    return web.json_response({
        "ok": True,
        "subscription_url": _ha._build_sub_url(new_token) if new_token else None,
        "subscription_backup_url": _ha._build_backup_sub_url(new_token) if new_token else None,
        "message": "Токен подписки перевыпущен, старая ссылка отключена",
    })


async def api_admin_user_message_handler(request: web.Request) -> web.Response:
    ctx, err_resp = _ha.require_admin_user(request)
    if err_resp is not None or ctx is None:
        return err_resp  # type: ignore[return-value]

    user_id, id_err = _ha._parse_positive_int(request, "user_id", label="user id")
    if id_err is not None or user_id is None:
        return id_err  # type: ignore[return-value]

    payload, parse_err = await _ha._parse_json_model(request, AdminDirectMessagePayload)
    if parse_err is not None or payload is None:
        return parse_err  # type: ignore[return-value]

    try:
        res = await _ha.admin_tools_service.send_direct_message(
            request.app.get("bot"),
            admin_id=ctx.telegram_id,
            user_id=user_id,
            text=payload.text,
        )
    except LookupError as exc:
        return _ha._safe_admin_error_response(exc, status=404)
    except ValueError as exc:
        return _ha._safe_admin_error_response(exc)

    return web.json_response({"ok": True, **res, "message": "Сообщение отправлено"})
