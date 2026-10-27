import asyncio
import logging
import uuid
from datetime import datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.formatters import format_admin_breadcrumbs
from bot.handlers.admin.users.common import format_subscription_days_text
from bot.keyboards import get_back_button
from bot.states import AdminStates
from config.constants import AdminAuditAction
from config.enums import WhiteInternetProvisioningStatus, WhiteInternetStatus
from database.connection import session_scope
from database.models import Server, User, VlessSubscription, VPNProfile, WhiteInternetSubscription
from database.repositories import white_internet_repo
from database.repositories.servers_repo import (
    get_all_servers,
    get_server_by_id,
    is_awg_capable_server,
    is_pure_vless_server,
    is_vless_capable_server,
    is_white_internet_origin_server,
)
from services.audit_service import AuditService
from services.subscription import SubscriptionService
from utils.admin import is_admin
from utils.datetime_helpers import now_utc
from utils.rate_limiter import global_send_limiter
from utils.telegram import render_hub, safe

router = Router()
logger = logging.getLogger(__name__)

_mass_extend_in_progress: set[int] = set()
_active_mass_extend_tasks: set[asyncio.Task] = set()


def _handle_mass_extend_task_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error("Background mass extend task failed: %s", e, exc_info=True)


def _start_mass_extend_task(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _active_mass_extend_tasks.add(task)
    task.add_done_callback(
        lambda t: (_active_mass_extend_tasks.discard(t), _handle_mass_extend_task_result(t))
    )
    return task


def _build_mass_extend_audience_filter(target_aud: str, now: datetime, server: Server | None = None):
    """Builds SQL condition for target audience of mass extension."""
    if target_aud == "active_standard":
        return User.subscription_end > now
    elif target_aud == "active_wi":
        return User.id.in_(
            select(WhiteInternetSubscription.user_id).where(
                WhiteInternetSubscription.status == WhiteInternetStatus.ACTIVE,
                WhiteInternetSubscription.expires_at > now,
                WhiteInternetSubscription.is_trial.is_(False),
                WhiteInternetSubscription.provisioning_status != WhiteInternetProvisioningStatus.PENDING_DELETE,
            )
        )
    elif target_aud == "active_all":
        wi_subq = select(WhiteInternetSubscription.user_id).where(
            WhiteInternetSubscription.status == WhiteInternetStatus.ACTIVE,
            WhiteInternetSubscription.expires_at > now,
            WhiteInternetSubscription.is_trial.is_(False),
            WhiteInternetSubscription.provisioning_status != WhiteInternetProvisioningStatus.PENDING_DELETE,
        )
        return or_(User.subscription_end > now, User.id.in_(wi_subq))
    elif target_aud.startswith("server_"):
        if not server:
            return User.id == -1

        conds = []
        if is_awg_capable_server(server):
            conds.append(
                (User.subscription_end > now)
                & User.profiles.any(
                    (VPNProfile.server_id == server.id)
                    & (VPNProfile.is_active.is_(True))
                    & (VPNProfile.desired_is_active.is_(True))
                    & (VPNProfile.provisioning_status.notin_(("deleting", "create_failed", "create_cleanup_pending")))
                )
            )
        if is_vless_capable_server(server):
            if is_pure_vless_server(server):
                conds.append(
                    (User.subscription_end > now)
                    & User.vless_subscription.has(VlessSubscription.is_active.is_(True))
                )
            else:
                conds.append(
                    (User.subscription_end > now)
                    & User.vless_subscription.has(
                        (VlessSubscription.is_active.is_(True))
                        & VlessSubscription.node_traffic_snapshots.has_key(str(server.id))
                    )
                )
        if is_white_internet_origin_server(server):
            conds.append(
                User.id.in_(
                    select(WhiteInternetSubscription.user_id).where(
                        WhiteInternetSubscription.origin_node_id == server.id,
                        WhiteInternetSubscription.status == WhiteInternetStatus.ACTIVE,
                        WhiteInternetSubscription.expires_at > now,
                        WhiteInternetSubscription.is_trial.is_(False),
                        WhiteInternetSubscription.provisioning_status != WhiteInternetProvisioningStatus.PENDING_DELETE,
                    )
                )
            )
        return or_(*conds) if conds else (User.id == -1)
    else:
        return User.subscription_end > now


@router.callback_query(F.data == "admin_mass_extend")
async def start_mass_extend(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    await state.clear()
    header = format_admin_breadcrumbs(texts.BTN_MASS_EXTEND, texts.ADMIN_USERS_MASS_BONUS_SELECT_AUDIENCE)

    text = (
        f"{header}\n\n"
        f"{texts.ADMIN_USERS_MASS_EXTEND_TITLE}"
        f"{texts.ADMIN_USERS_MASS_EXTEND_SELECT_AUDIENCE}"
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.ADMIN_USERS_MASS_EXTEND_AUD_ACTIVE_STANDARD,
        callback_data="mass_ext_aud:active_standard",
    )
    builder.button(
        text=texts.ADMIN_USERS_MASS_EXTEND_AUD_ACTIVE_WI,
        callback_data="mass_ext_aud:active_wi",
    )
    builder.button(
        text=texts.ADMIN_USERS_MASS_EXTEND_AUD_ACTIVE_ALL,
        callback_data="mass_ext_aud:active_all",
    )
    builder.button(
        text=texts.ADMIN_USERS_MASS_EXTEND_AUD_SERVER,
        callback_data="mass_ext_servers",
    )
    builder.button(
        text=texts.BTN_ADMIN_MENU,
        callback_data="admin_menu",
    )
    builder.adjust(1)

    try:
        await callback.message.edit_text(
            text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    except Exception:
        pass

    await callback.answer(show_alert=False)


@router.callback_query(F.data == "mass_ext_servers")
async def select_mass_extend_server(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    servers = await get_all_servers(session)
    header = format_admin_breadcrumbs(texts.BTN_MASS_EXTEND, texts.BROADCAST_BREADCRUMB_SERVER_SELECTION)
    text = f"{header}\n\n{texts.ADMIN_USERS_MASS_EXTEND_SELECT_SERVER}"

    builder = InlineKeyboardBuilder()
    for s in servers:
        flag = s.country_flag or "🌐"
        builder.button(
            text=f"{flag} {s.name}",
            callback_data=f"mass_ext_aud:server_{s.id}",
        )
    builder.button(text=texts.BTN_BACK, callback_data="admin_mass_extend")
    builder.adjust(1)

    try:
        await callback.message.edit_text(
            text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer(show_alert=False)


@router.callback_query(F.data.startswith("mass_ext_aud:"))
async def select_mass_extend_audience(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    target_aud = callback.data.split(":")[1]
    await state.update_data(target_aud=target_aud)
    await state.set_state(AdminStates.entering_mass_extend_days)

    header = format_admin_breadcrumbs(texts.BTN_MASS_EXTEND, texts.ADMIN_USERS_MASS_EXTEND_HEADER_DAYS)
    text = f"{header}\n\n{texts.ADMIN_USERS_MASS_EXTEND_ENTER_DAYS_PROMPT}"

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_back_button("admin_mass_extend"),
            parse_mode="HTML",
        )
    except Exception:
        pass

    await callback.answer(show_alert=False)


@router.message(AdminStates.entering_mass_extend_days)
async def process_mass_extend_days(
    message: Message,
    state: FSMContext,
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if message.text and message.text.startswith("/"):
        await state.clear()
        try:
            await message.delete()
        except Exception:
            pass
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_OPERATION_CANCELLED,
            get_back_button("admin_mass_extend"),
            trigger_message_id=message.message_id,
        )
        return

    try:
        days = int(message.text.strip())
        if days < 1 or days > 365:
            raise ValueError()
    except (ValueError, AttributeError):
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ADMIN_USERS_MASS_EXTEND_INVALID_DAYS,
            get_back_button("admin_mass_extend"),
            trigger_message_id=message.message_id,
        )
        return

    await state.update_data(days=days)
    await state.set_state(AdminStates.entering_mass_extend_reason)

    days_text = format_subscription_days_text(days)
    header = format_admin_breadcrumbs(texts.BTN_MASS_EXTEND, texts.ADMIN_USERS_MASS_BONUS_VVOD_PRICHINY)
    text = (
        f"{header}\n\n"
        f"{texts.ADMIN_USERS_MASS_EXTEND_ENTER_REASON_PROMPT.format(days_text=days_text)}"
    )

    await render_hub(
        message.bot,
        message.chat.id,
        text,
        get_back_button("admin_mass_extend"),
        parse_mode="HTML",
        trigger_message_id=message.message_id,
    )


@router.message(AdminStates.entering_mass_extend_reason)
async def process_mass_extend_reason(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if message.text and message.text.startswith("/"):
        await state.clear()
        try:
            await message.delete()
        except Exception:
            pass
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_OPERATION_CANCELLED,
            get_back_button("admin_mass_extend"),
            trigger_message_id=message.message_id,
        )
        return

    raw_reason = message.text.strip() if message.text else ""
    if len(raw_reason) > 100:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_REASON_TOO_LONG.format(max=100),
            get_back_button("admin_mass_extend"),
            parse_mode="HTML",
            trigger_message_id=message.message_id,
        )
        return

    reason = raw_reason if raw_reason else texts.ADMIN_USERS_MASS_EXTEND_DEFAULT_REASON
    data = await state.get_data()
    target_aud = data.get("target_aud", "active_all")
    days = data.get("days", 1)
    days_text = format_subscription_days_text(days)

    now = now_utc()
    server = None
    if target_aud.startswith("server_"):
        try:
            server_id = int(target_aud.split("_")[1])
        except (IndexError, ValueError):
            server_id = -1
        server = await get_server_by_id(session, server_id)
        server_name = server.name if server else f"ID {server_id}"
        server_flag = (server.country_flag or "🌐") if server else "🌐"
        aud_label = texts.ADMIN_USERS_MASS_BONUS_SERVER_AUDIENCE_LABEL.format(flag=server_flag, name=safe(server_name))
    else:
        aud_label = texts.ADMIN_MASS_EXTEND_AUDIENCE_LABELS.get(target_aud, target_aud)

    base_cond = (User.is_deleted.is_(False)) & (User.is_banned.is_(False))
    aud_cond = _build_mass_extend_audience_filter(target_aud, now, server=server)
    stmt = select(func.count(User.id)).where(base_cond, aud_cond)

    user_count = int((await session.scalar(stmt)) or 0)

    await state.update_data(reason=reason, user_count=user_count)
    await state.set_state(AdminStates.confirming_mass_extend)

    header = format_admin_breadcrumbs(texts.BTN_MASS_EXTEND, texts.ADMIN_USERS_MASS_BONUS_CONFIRM)
    text = (
        f"{header}\n\n"
        f"{texts.ADMIN_USERS_MASS_EXTEND_CONFIRM_TITLE}"
        f"{texts.ADMIN_USERS_MASS_BONUS_AUDIENCE.format(aud_label=aud_label)}"
        f"{texts.ADMIN_USERS_MASS_BONUS_RECIPIENTS_CHEL.format(user_count=user_count)}"
        f"{texts.ADMIN_USERS_MASS_EXTEND_CONFIRM_DAYS.format(days_text=days_text)}"
        f"{texts.ADMIN_USERS_MASS_BONUS_REASON.format(safe_reason=safe(reason))}"
        f"{texts.ADMIN_USERS_MASS_EXTEND_CONFIRM_QUESTION}"
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.ADMIN_USERS_MASS_EXTEND_BTN_START,
        callback_data="confirm_mass_extend_apply",
    )
    builder.button(
        text=texts.BTN_CANCEL,
        callback_data="admin_mass_extend",
    )
    builder.adjust(1)

    await render_hub(
        message.bot,
        message.chat.id,
        text,
        builder.as_markup(),
        parse_mode="HTML",
        trigger_message_id=message.message_id,
    )


@router.callback_query(AdminStates.confirming_mass_extend, F.data == "confirm_mass_extend_apply")
async def apply_mass_extend(
    callback: CallbackQuery,
    state: FSMContext,
):
    admin_id = callback.from_user.id
    if not is_admin(admin_id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    if _mass_extend_in_progress:
        await callback.answer(texts.ADMIN_USERS_MASS_EXTEND_ALREADY_RUNNING, show_alert=True)
        return

    data = await state.get_data()
    if not data or "target_aud" not in data or "days" not in data:
        await callback.answer(texts.ERROR_OPERATION_CANCELLED, show_alert=True)
        await state.clear()
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    # Immediately remove inline keyboard to prevent duplicate clicks
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    _mass_extend_in_progress.add(admin_id)
    try:
        target_aud = data["target_aud"]
        days = data["days"]
        reason = data.get("reason", texts.ADMIN_USERS_MASS_EXTEND_DEFAULT_REASON)
        days_text = format_subscription_days_text(days)

        await state.clear()
        await callback.answer(texts.ADMIN_USERS_MASS_EXTEND_STARTED_ALERT, show_alert=True)

        header = format_admin_breadcrumbs(texts.BTN_MASS_EXTEND, texts.ADMIN_USERS_MASS_BONUS_REZULTAT)
        try:
            await callback.message.edit_text(
                texts.ADMIN_USERS_MASS_EXTEND_STARTED_TEXT.format(header=header, days_text=days_text),
                parse_mode="HTML",
            )
        except Exception:
            pass

        batch_id = uuid.uuid4().hex[:16]
        _start_mass_extend_task(
            _run_mass_extend_background(
                bot=callback.bot,
                admin_id=admin_id,
                target_aud=target_aud,
                days=days,
                reason=reason,
                batch_id=batch_id,
            )
        )
    except Exception:
        _mass_extend_in_progress.discard(admin_id)
        raise


@router.callback_query(F.data == "confirm_mass_extend_apply")
async def stale_confirm_mass_extend(
    callback: CallbackQuery,
    state: FSMContext,
):
    """Safely handles clicks on stale/expired confirmation buttons."""
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    await state.clear()
    await callback.answer(texts.ERROR_OPERATION_CANCELLED, show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


async def _run_mass_extend_background(
    bot,
    admin_id: int,
    target_aud: str,
    days: int,
    reason: str,
    batch_id: str | int,
):
    """Executes mass subscription extension across target audience in batches of 50.

    Invariants:
    - Transaction fault isolation: each user extension runs inside session.begin_nested() (SAVEPOINT).
    - Rate-limited Telegram notifications: dispatched with global_send_limiter.
    - Clean chat compliance: user notifications include dismiss_notification button.
    """
    days_text = format_subscription_days_text(days)
    try:
        success_count = 0
        fail_count = 0
        blocked_count = 0

        server = None
        if target_aud.startswith("server_"):
            try:
                srv_id = int(target_aud.split("_")[1])
            except (IndexError, ValueError):
                srv_id = -1
            async with session_scope() as session:
                server = await session.get(Server, srv_id)

            if not server:
                logger.error("Server %s not found. Fail-closed: aborting mass extend.", target_aud)
                try:
                    header = format_admin_breadcrumbs(texts.BTN_MASS_EXTEND, texts.ADMIN_USERS_MASS_BONUS_ITOGI)
                    builder = InlineKeyboardBuilder()
                    builder.button(text=texts.ADMIN_USERS_MASS_EXTEND_BTN_NEW, callback_data="admin_mass_extend")
                    builder.button(text=texts.ADMIN_USERS_MASS_BONUS_V_ADMIN_MENYU, callback_data="admin_menu")
                    builder.adjust(1)

                    await render_hub(
                        bot,
                        admin_id,
                        texts.ADMIN_USERS_MASS_EXTEND_COMPLETED_TEXT.format(
                            header=header,
                            success_count=0,
                            days_text=days_text,
                            fail_count=0,
                            blocked_count=0,
                        ),
                        reply_markup=builder.as_markup(),
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.error("Failed to notify admin of missing server: %s", e)
                return

        now = now_utc()
        base_cond = (User.is_deleted.is_(False)) & (User.is_banned.is_(False))
        aud_cond = _build_mass_extend_audience_filter(target_aud, now, server=server)
        stmt = (
            select(User.id, User.telegram_id)
            .where(base_cond, aud_cond)
            .distinct()
            .order_by(User.id.asc())
        )

        async with session_scope() as session:
            result = await session.execute(stmt)
            users = result.all()

        if not users:
            try:
                header = format_admin_breadcrumbs(texts.BTN_MASS_EXTEND, texts.ADMIN_USERS_MASS_BONUS_ITOGI)
                builder = InlineKeyboardBuilder()
                builder.button(text=texts.ADMIN_USERS_MASS_EXTEND_BTN_NEW, callback_data="admin_mass_extend")
                builder.button(text=texts.ADMIN_USERS_MASS_BONUS_V_ADMIN_MENYU, callback_data="admin_menu")
                builder.adjust(1)

                await render_hub(
                    bot,
                    admin_id,
                    texts.ADMIN_USERS_MASS_EXTEND_COMPLETED_TEXT.format(
                        header=header,
                        success_count=0,
                        days_text=days_text,
                        fail_count=0,
                        blocked_count=0,
                    ),
                    reply_markup=builder.as_markup(),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error("Failed to notify admin of empty mass extension: %s", e)
            return

        CHUNK_SIZE = 50
        for i in range(0, len(users), CHUNK_SIZE):
            chunk = users[i : i + CHUNK_SIZE]
            extended_in_batch = []

            async with session_scope() as session:
                for uid, tg_id in chunk:
                    try:
                        async with session.begin_nested():
                            user = await session.get(User, uid)
                            if not user or user.is_deleted or user.is_banned:
                                continue

                            extended = False
                            current_now = now_utc()
                            if target_aud == "active_standard":
                                if user.subscription_end and user.subscription_end > current_now:
                                    res = await SubscriptionService.extend_subscription(
                                        session,
                                        tg_id,
                                        days,
                                        admin_id=admin_id,
                                        reason=reason,
                                    )
                                    if res:
                                        extended = True
                            elif target_aud == "active_wi":
                                sub = await white_internet_repo.get_subscription_by_user_id(session, uid)
                                if (
                                    sub
                                    and not getattr(sub, "is_trial", False)
                                    and sub.status == WhiteInternetStatus.ACTIVE
                                    and sub.expires_at
                                    and sub.expires_at > current_now
                                ):
                                    res_sub = await white_internet_repo.extend_subscription_atomic(session, sub.id, days)
                                    if res_sub:
                                        extended = True
                            elif target_aud == "active_all":
                                # Extend standard if active
                                if user.subscription_end and user.subscription_end > current_now:
                                    res = await SubscriptionService.extend_subscription(
                                        session,
                                        tg_id,
                                        days,
                                        admin_id=admin_id,
                                        reason=reason,
                                    )
                                    if res:
                                        extended = True
                                # Extend White Internet if active
                                sub = await white_internet_repo.get_subscription_by_user_id(session, uid)
                                if (
                                    sub
                                    and not getattr(sub, "is_trial", False)
                                    and sub.status == WhiteInternetStatus.ACTIVE
                                    and sub.expires_at
                                    and sub.expires_at > current_now
                                ):
                                    res_sub = await white_internet_repo.extend_subscription_atomic(session, sub.id, days)
                                    if res_sub:
                                        extended = True
                            elif target_aud.startswith("server_"):
                                if not server:
                                    continue
                                if is_white_internet_origin_server(server):
                                    sub = await white_internet_repo.get_subscription_by_user_id(session, uid)
                                    if (
                                        sub
                                        and not getattr(sub, "is_trial", False)
                                        and sub.status == WhiteInternetStatus.ACTIVE
                                        and sub.expires_at
                                        and sub.expires_at > current_now
                                    ):
                                        res_sub = await white_internet_repo.extend_subscription_atomic(session, sub.id, days)
                                        if res_sub:
                                            extended = True
                                elif is_awg_capable_server(server) or is_vless_capable_server(server):
                                    if user.subscription_end and user.subscription_end > current_now:
                                        res = await SubscriptionService.extend_subscription(
                                            session,
                                            tg_id,
                                            days,
                                            admin_id=admin_id,
                                            reason=reason,
                                        )
                                        if res:
                                            extended = True
                                else:
                                    logger.error(
                                        "Server %s has unhandled capabilities. Fail-closed: skipped user %s.",
                                        server.id,
                                        uid,
                                    )

                            if extended:
                                success_count += 1
                                extended_in_batch.append((uid, tg_id))
                    except Exception as exc:
                        fail_count += 1
                        logger.error("Failed mass extension for user %s: %s", uid, exc, exc_info=True)

            blocked_uids = []
            dismiss_builder = InlineKeyboardBuilder()
            dismiss_builder.button(text=texts.BTN_DISMISS, callback_data="dismiss_notification")
            notification_text = texts.ADMIN_USER_SUB_EXTENDED_NOTIFICATION.format(
                days_text=days_text,
                safe_reason=safe(reason),
            )

            for uid, tg_id in extended_in_batch:
                if tg_id and tg_id != admin_id:
                    try:
                        await global_send_limiter.acquire()
                        await bot.send_message(
                            tg_id,
                            notification_text,
                            reply_markup=dismiss_builder.as_markup(),
                            parse_mode="HTML",
                        )
                    except TelegramRetryAfter as e:
                        await asyncio.sleep(e.retry_after + 1)
                        try:
                            await global_send_limiter.acquire()
                            await bot.send_message(
                                tg_id,
                                notification_text,
                                reply_markup=dismiss_builder.as_markup(),
                                parse_mode="HTML",
                            )
                        except TelegramForbiddenError:
                            blocked_count += 1
                            blocked_uids.append(uid)
                        except Exception as retry_err:
                            logger.warning("Failed retry notify for user %s: %s", uid, retry_err)
                    except TelegramForbiddenError:
                        blocked_count += 1
                        blocked_uids.append(uid)
                    except Exception as e:
                        logger.warning("Failed to notify user %s for mass extension: %s", uid, e)

            if blocked_uids:
                async with session_scope() as session:
                    for uid in blocked_uids:
                        db_user = await session.get(User, uid)
                        if db_user:
                            db_user.is_bot_blocked = True

        if success_count > 0 or fail_count > 0:
            async with session_scope() as session:
                audit_reason = f"[{target_aud}] {reason}" if target_aud else reason
                await AuditService.log_action(
                    session,
                    admin_id,
                    AdminAuditAction.MASS_SUB_EXTEND,
                    "User",
                    0,
                    {
                        "batch_id": batch_id,
                        "target_aud": target_aud,
                        "days": days,
                        "success_count": success_count,
                        "fail_count": fail_count,
                        "reason": audit_reason,
                    },
                )

        try:
            header = format_admin_breadcrumbs(texts.BTN_MASS_EXTEND, texts.ADMIN_USERS_MASS_BONUS_ITOGI)
            builder = InlineKeyboardBuilder()
            builder.button(text=texts.ADMIN_USERS_MASS_EXTEND_BTN_NEW, callback_data="admin_mass_extend")
            builder.button(text=texts.ADMIN_USERS_MASS_BONUS_V_ADMIN_MENYU, callback_data="admin_menu")
            builder.adjust(1)

            await render_hub(
                bot,
                admin_id,
                texts.ADMIN_USERS_MASS_EXTEND_COMPLETED_TEXT.format(
                    header=header,
                    success_count=success_count,
                    days_text=days_text,
                    fail_count=fail_count,
                    blocked_count=blocked_count,
                ),
                reply_markup=builder.as_markup(),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error("Failed to notify admin of mass extension completion: %s", e)
    finally:
        _mass_extend_in_progress.discard(admin_id)
