import asyncio
from datetime import datetime
import logging

from config.constants import AdminAuditAction
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from bot.states import AdminStates
from database.models import User
from database.repositories.account_ledger_repo import create_admin_adjustment
from services.audit_service import AuditService
from utils.admin import is_admin
from utils.datetime_helpers import now_utc
from bot.formatters import format_admin_breadcrumbs
from utils.telegram import render_hub, safe

router = Router()
logger = logging.getLogger(__name__)

_mass_bonus_in_progress: set[int] = set()
_active_mass_bonus_tasks: set[asyncio.Task] = set()


def _handle_mass_bonus_task_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error("Background mass bonus task failed: %s", e, exc_info=True)


def _start_mass_bonus_task(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _active_mass_bonus_tasks.add(task)
    task.add_done_callback(
        lambda t: (_active_mass_bonus_tasks.discard(t), _handle_mass_bonus_task_result(t))
    )
    return task


@router.callback_query(F.data == "admin_mass_bonus")
async def start_mass_bonus(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    await state.clear()
    header = format_admin_breadcrumbs(texts.BTN_MASS_BONUS, texts.ADMIN_USERS_MASS_BONUS_SELECT_AUDIENCE)

    text = (
        f"{header}"+
        texts.ADMIN_USERS_MASS_BONUS_MASSOVOE_GRANT_BONUS.format()+
        texts.ADMIN_USERS_MASS_BONUS_SELECT_TSELEVUYU_GRUPPU_POLZ.format()
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.ADMIN_USERS_MASS_BONUS_ALL_POLZOVATELYAM,
        callback_data="mass_bonus_aud:all",
    )
    builder.button(
        text=texts.ADMIN_USERS_MASS_BONUS_TOLKO_S_AKTIVNOY_PODPISKOY,
        callback_data="mass_bonus_aud:active",
    )
    builder.button(
        text=texts.ADMIN_USERS_MASS_EXTEND_AUD_ACTIVE_STANDARD,
        callback_data="mass_bonus_aud:standard",
    )
    builder.button(
        text=texts.ADMIN_USERS_MASS_EXTEND_AUD_ACTIVE_WI,
        callback_data="mass_bonus_aud:wi",
    )
    builder.button(
        text=texts.ADMIN_USERS_MASS_BONUS_TOLKO_BEZ_SUBSCRIPTION,
        callback_data="mass_bonus_aud:inactive",
    )
    builder.button(
        text=texts.BTN_ADMIN_MENU,
        callback_data="admin_menu",
    )
    builder.adjust(1)

    await state.update_data(active_msg_id=callback.message.message_id)

    try:
        await callback.message.edit_text(
            text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    except Exception:
        new_id = await render_hub(
            callback.bot,
            callback.message.chat.id,
            text,
            builder.as_markup(),
            parse_mode="HTML",
        )
        if new_id:
            await state.update_data(active_msg_id=new_id)

    await callback.answer(show_alert=False)


@router.callback_query(F.data.startswith("mass_bonus_aud:"))
async def select_mass_bonus_audience(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    target_aud = callback.data.split(":")[1]
    await state.update_data(target_aud=target_aud, active_msg_id=callback.message.message_id)
    await state.set_state(AdminStates.entering_mass_bonus_amount)

    header = format_admin_breadcrumbs(texts.BTN_MASS_BONUS, texts.ADMIN_USERS_MASS_BONUS_VVOD_SUMMY)
    text = (
        f"{header}"+
        texts.ADMIN_USERS_MASS_BONUS_AMOUNT_BONUS_GRANT_N.format()+
        texts.ADMIN_USERS_MASS_BONUS_ENTER_AMOUNT_BONUSOV_V_RUBLYA.format()
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_back_button("admin_mass_bonus"),
            parse_mode="HTML",
        )
    except Exception:
        new_id = await render_hub(
            callback.bot,
            callback.message.chat.id,
            text,
            get_back_button("admin_mass_bonus"),
            parse_mode="HTML",
        )
        if new_id:
            await state.update_data(active_msg_id=new_id)

    await callback.answer(show_alert=False)


@router.message(AdminStates.entering_mass_bonus_amount)
async def process_mass_bonus_amount(
    message: Message,
    state: FSMContext,
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    try:
        amount = int(message.text.strip())
        if amount <= 0 or amount > 100000:
            raise ValueError()
    except (ValueError, AttributeError):
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ADMIN_USERS_MASS_BONUS_ENTER_KORREKTNUYU_AMOUNT_NACH,
            get_back_button("admin_mass_bonus"),
            trigger_message_id=message.message_id,
        )
        return

    await state.update_data(amount=amount)
    await state.set_state(AdminStates.entering_mass_bonus_reason)

    header = format_admin_breadcrumbs(texts.BTN_MASS_BONUS, texts.ADMIN_USERS_MASS_BONUS_VVOD_PRICHINY)
    text = (
        f"{header}"+
        texts.ADMIN_USERS_MASS_BONUS_REASON_MASSOVOGO_NACHISLENIY.format(amount=amount)+
        texts.ADMIN_USERS_MASS_BONUS_ENTER_MESSAGE_FOR_POLZ.format()+
        texts.ADMIN_USERS_MASS_BONUS_NAPRIMER_KOMPENSATSIYA_ZA_SBOY.format()
    )

    new_id = await render_hub(
        message.bot,
        message.chat.id,
        text,
        get_back_button("admin_mass_bonus"),
        parse_mode="HTML",
        trigger_message_id=message.message_id,
    )
    if new_id:
        await state.update_data(active_msg_id=new_id)


def _apply_mass_bonus_audience_filter(
    stmt,
    target_aud: str,
    now: datetime | None = None,
):
    """Applies target audience filter for mass bonus preview and background execution."""
    if now is None:
        now = now_utc()
    from database.repositories.users_repo import (
        get_effective_active_condition,
        get_effective_inactive_condition,
        get_effective_wi_active_condition,
    )

    if target_aud == "all":
        return stmt
    elif target_aud == "standard":
        return stmt.where(User.subscription_end > now)
    elif target_aud == "wi":
        return stmt.where(get_effective_wi_active_condition(now))
    elif target_aud == "active":
        return stmt.where(get_effective_active_condition(now))
    elif target_aud in ("inactive", "expired"):
        return stmt.where(get_effective_inactive_condition(now))

    logger.error("Unknown mass_bonus audience %r; returning empty query", target_aud)
    return stmt.where(false())


@router.message(AdminStates.entering_mass_bonus_reason)
async def process_mass_bonus_reason(
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
            get_back_button("admin_mass_bonus"),
            trigger_message_id=message.message_id,
        )
        return

    raw_reason = message.text.strip() if message.text else ""
    if len(raw_reason) > 100:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_REASON_TOO_LONG.format(max=100),
            get_back_button("admin_mass_bonus"),
            parse_mode="HTML",
            trigger_message_id=message.message_id,
        )
        return

    reason = raw_reason if raw_reason else texts.ADMIN_USERS_MASS_BONUS_MASSOVAYA_KOMPENSATSIYA
    data = await state.get_data()
    target_aud = data.get("target_aud", "all")
    amount = data.get("amount", 0)

    # Подсчет аудитории
    now = now_utc()
    aud_label = texts.ADMIN_MASS_BONUS_AUDIENCE_LABELS.get(target_aud, target_aud)

    stmt = select(func.count(User.id)).where(User.is_deleted.is_(False), User.is_banned.is_(False))
    stmt = _apply_mass_bonus_audience_filter(stmt, target_aud, now)

    user_count = int((await session.scalar(stmt)) or 0)
    total_budget = user_count * amount

    await state.update_data(reason=reason, user_count=user_count)
    await state.set_state(AdminStates.confirming_mass_bonus)

    header = format_admin_breadcrumbs(texts.BTN_MASS_BONUS, texts.ADMIN_USERS_MASS_BONUS_CONFIRM)

    text = (
        f"{header}"+
        texts.ADMIN_USERS_MASS_BONUS_CONFIRM_MASSOVOGO_NACHI.format()+
        texts.ADMIN_USERS_MASS_BONUS_AUDIENCE.format(aud_label=aud_label)+
        texts.ADMIN_USERS_MASS_BONUS_RECIPIENTS_CHEL.format(user_count=user_count)+
        texts.ADMIN_USERS_MASS_BONUS_BONUS_KAZHDOMU.format(amount=amount)+
        texts.ADMIN_USERS_MASS_BONUS_OBSHCHIY_BYUDZHET_BONUSOV.format(total_budget=total_budget)+
        texts.ADMIN_USERS_MASS_BONUS_REASON.format(safe_reason=safe(reason))+
        texts.ADMIN_USERS_MASS_BONUS_VY_UVERENY_CHTO_KHOTITE_ZAPUST.format()
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.ADMIN_USERS_MASS_BONUS_ZAPUSTIT_GRANT,
        callback_data="confirm_mass_bonus_apply",
    )
    builder.button(
        text=texts.BTN_CANCEL,
        callback_data="admin_mass_bonus",
    )
    builder.adjust(1)

    new_id = await render_hub(
        message.bot,
        message.chat.id,
        text,
        builder.as_markup(),
        parse_mode="HTML",
        trigger_message_id=message.message_id,
    )
    if new_id:
        await state.update_data(active_msg_id=new_id)



@router.callback_query(F.data == "confirm_mass_bonus_apply")
async def apply_mass_bonus(
    callback: CallbackQuery,
    state: FSMContext,
):
    admin_id = callback.from_user.id
    if not is_admin(admin_id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    if admin_id in _mass_bonus_in_progress:
        await callback.answer(texts.ADMIN_USERS_MASS_BONUS_MASSOVOE_GRANT_UZHE_VYPO, show_alert=True)
        return

    _mass_bonus_in_progress.add(admin_id)

    data = await state.get_data()
    target_aud = data.get("target_aud", "all")
    amount = data.get("amount", 0)
    reason = data.get("reason", texts.ADMIN_MASS_BONUS_DEFAULT_REASON)
    active_msg_id = data.get("active_msg_id") or callback.message.message_id

    await state.clear()
    await callback.answer(texts.ADMIN_USERS_MASS_BONUS_MASSOVOE_GRANT_ZAPUSHCHE, show_alert=True)

    header = format_admin_breadcrumbs(texts.BTN_MASS_BONUS, texts.ADMIN_USERS_MASS_BONUS_REZULTAT)
    status_text = (
        texts.ADMIN_USERS_MASS_BONUS_MASSOVOE_GRANT_BONUSOV_P.format(header=header, amount=amount)+
        texts.ADMIN_USERS_MASS_BONUS_PO_ZAVERSHENII_OPERATIONS_VAM_P.format()
    )
    try:
        await callback.message.edit_text(
            status_text,
            parse_mode="HTML",
        )
    except Exception:
        new_id = await render_hub(
            callback.bot,
            callback.message.chat.id,
            status_text,
            parse_mode="HTML",
        )
        if new_id:
            active_msg_id = new_id

    import uuid

    # One logical apply = one unique batch. A day-deterministic batch made any
    # intentional repeat with the same parameters silently no-op, while a
    # double apply must behave like any repeated admin grant: credit again.
    # Same-run retries are still safe: the in-progress guard above blocks a
    # second apply from the same admin, and each credited user is isolated in
    # a SAVEPOINT with a unique idempotency key per (batch, user).
    batch_id = uuid.uuid4().hex[:16]
    try:
        _start_mass_bonus_task(
            _run_mass_bonus_background(
                bot=callback.bot,
                admin_id=admin_id,
                target_aud=target_aud,
                amount=amount,
                reason=reason,
                batch_id=batch_id,
                active_msg_id=active_msg_id,
            )
        )
    except Exception:
        _mass_bonus_in_progress.discard(admin_id)
        raise


async def _run_mass_bonus_background(
    bot,
    admin_id: int,
    target_aud: str,
    amount: int,
    reason: str,
    batch_id: str | int,
    active_msg_id: int | None = None,
):
    """Executes mass bonus adjustment across target audience in batches of 50.

    Financial & Delivery Invariants:
    - Financial Idempotency: Uses unique `batch_id` with `idempotency_key = f"mass_bonus_{batch_id}_{uid}_{amount}"`.
      Guarantees collision-free ledger credits and zero duplicate records within the execution batch.
    - Transaction Fault Isolation: Each user adjustment runs inside `session.begin_nested()` (SAVEPOINT).
      Single-user DB errors roll back locally without invalidating the batch or raising `PendingRollbackError`.
    - Best-Effort Post-Commit Telegram Notification: Notifications are dispatched post-commit strictly to
      newly credited users (`created=True`) in each chunk.
    """
    from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

    from database.connection import session_scope
    from utils.rate_limiter import global_send_limiter

    try:
        success_count = 0
        fail_count = 0
        blocked_count = 0

        now = now_utc()
        stmt = (
            select(User.id, User.telegram_id)
            .where(
                User.is_deleted.is_(False),
                User.is_banned.is_(False),
            )
        )
        stmt = _apply_mass_bonus_audience_filter(stmt, target_aud, now).distinct()

        async with session_scope() as session:
            result = await session.execute(stmt)
            users = result.all()

        CHUNK_SIZE = 50
        for i in range(0, len(users), CHUNK_SIZE):
            chunk = users[i : i + CHUNK_SIZE]
            credited_in_batch = []
            async with session_scope() as session:
                for uid, tg_id in chunk:
                    try:
                        async with session.begin_nested():
                            idempotency_key = f"mass_bonus_{batch_id}_{uid}_{amount}"
                            _entry, created = await create_admin_adjustment(
                                session,
                                user_id=uid,
                                signed_amount=amount,
                                idempotency_key=idempotency_key,
                                metadata={
                                    "admin_id": admin_id,
                                    "reason": reason,
                                    "batch_id": batch_id,
                                    "target_aud": target_aud,
                                },
                            )
                            if created:
                                success_count += 1
                                credited_in_batch.append((uid, tg_id))
                    except Exception as exc:
                        fail_count += 1
                        logger.error("Failed mass bonus credit for user %s: %s", uid, exc)

            blocked_uids = []
            dismiss_builder = InlineKeyboardBuilder()
            dismiss_builder.button(text=texts.BTN_DISMISS, callback_data="dismiss_notification")
            notification_text = (
                texts.ADMIN_USER_BONUS_ACCREDITED_NOTIFICATION.format(amount=amount)+
                texts.ADMIN_BONUS_REASON_LINE_FORMAT.format(safe_reason=safe(reason))
            )

            for uid, tg_id in credited_in_batch:
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
                        except Exception as e_retry:
                            logger.warning("Failed retry to notify user %s for mass bonus: %s", uid, e_retry)
                    except TelegramForbiddenError:
                        blocked_count += 1
                        blocked_uids.append(uid)
                    except Exception as e:
                        logger.warning("Failed to notify user %s for mass bonus: %s", uid, e)

            if blocked_uids:
                async with session_scope() as session:
                    for uid in blocked_uids:
                        db_user = await session.get(User, uid)
                        if db_user:
                            db_user.is_bot_blocked = True

        async with session_scope() as session:
            audit_reason = f"[{target_aud}] {reason}" if target_aud else reason
            await AuditService.log_action(
                session,
                admin_id,
                AdminAuditAction.MASS_BONUS_GRANTED,
                "User",
                0,
                texts.ADMIN_AUDIT_LOG_DETAILS_MASS_BONUS.format(
                    amount=amount, count=success_count, batch_id=batch_id, reason=audit_reason
                ),
            )

        try:
            header = format_admin_breadcrumbs(texts.BTN_MASS_BONUS, texts.ADMIN_USERS_MASS_BONUS_ITOGI)
            builder = InlineKeyboardBuilder()
            builder.button(text=texts.ADMIN_USERS_MASS_BONUS_NEW_MASS_BONUS, callback_data="admin_mass_bonus")
            builder.button(text=texts.ADMIN_USERS_MASS_BONUS_V_ADMIN_MENYU, callback_data="admin_menu")
            builder.adjust(1)

            report_text = (
                texts.ADMIN_USERS_MASS_BONUS_MASSOVOE_GRANT_BONUSOV_Z.format(header=header)+
                texts.ADMIN_USERS_MASS_BONUS_ZACHISLENO_CHEL_KAZHDOMU.format(success_count=success_count, amount=amount)+
                texts.ADMIN_USERS_MASS_BONUS_OSHIBOK.format(fail_count=fail_count)+
                texts.ADMIN_USERS_MASS_BONUS_ZABLOKIROVALI_BOTA.format(blocked_count=blocked_count)
            )

            if active_msg_id:
                try:
                    await bot.edit_message_text(
                        chat_id=admin_id,
                        message_id=active_msg_id,
                        text=report_text,
                        reply_markup=builder.as_markup(),
                        parse_mode="HTML",
                    )
                    return
                except Exception as edit_err:
                    logger.debug("Failed in-place edit for mass bonus completion, falling back to render_hub: %s", edit_err)

            await render_hub(
                bot,
                admin_id,
                report_text,
                reply_markup=builder.as_markup(),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error("Failed to notify admin of mass bonus completion: %s", e)
    finally:
        _mass_bonus_in_progress.discard(admin_id)

