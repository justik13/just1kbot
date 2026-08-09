"""Mass bonus compensation workflow for admin panel."""
import logging
from aiogram import Router, F

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from bot.states import AdminStates
from database.models import User
from database.repositories.account_ledger_repo import create_admin_adjustment
from services.audit_service import AuditService
from utils.admin import is_admin
from utils.datetime_helpers import now_utc
from utils.formatters import format_admin_breadcrumbs
from utils.telegram import render_hub, safe

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "admin_mass_bonus")
async def start_mass_bonus(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    await state.clear()
    header = format_admin_breadcrumbs("🎁 Массовый бонус", "Выбор аудитории")

    text = (
        f"{header}"
        f"🎁 <b>Массовое начисление бонусного баланса</b>\n\n"
        f"Выберите целевую группу пользователей для получения компенсации/бонусов:"
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text="👥 Всем пользователям",
        callback_data="mass_bonus_aud:all",
    )
    builder.button(
        text="⚡ Только с активной подпиской",
        callback_data="mass_bonus_aud:active",
    )
    builder.button(
        text="⏳ Только без подписки",
        callback_data="mass_bonus_aud:expired",
    )
    builder.button(
        text="🔙 В админ-меню",
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
    await state.update_data(target_aud=target_aud)
    await state.set_state(AdminStates.entering_mass_bonus_amount)

    header = format_admin_breadcrumbs("🎁 Массовый бонус", "Ввод суммы")
    text = (
        f"{header}"
        f"💰 <b>Сумма бонусного начисления на каждого пользователя:</b>\n\n"
        f"Введите сумму бонусов в рублях (целое число, например <code>100</code>):"
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_back_button("admin_mass_bonus"),
            parse_mode="HTML",
        )
    except Exception:
        pass

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
            "⚠️ Введите корректную сумму начисления от 1 до 100 000 ₽",
            get_back_button("admin_mass_bonus"),
        )
        return

    await state.update_data(amount=amount)
    await state.set_state(AdminStates.entering_mass_bonus_reason)

    header = format_admin_breadcrumbs("🎁 Массовый бонус", "Ввод причины")
    text = (
        f"{header}"
        f"📝 <b>Причина массового начисления (+{amount} ₽):</b>\n\n"
        f"Введите сообщение для пользователей и лога аудита\n"
        f"(например: <i>Компенсация за сбой на серверах 09.08</i>):"
    )

    await render_hub(
        message.bot,
        message.chat.id,
        text,
        get_back_button("admin_mass_bonus"),
        parse_mode="HTML",
    )


@router.message(AdminStates.entering_mass_bonus_reason)
async def process_mass_bonus_reason(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    reason = message.text.strip() if message.text else "Массовая компенсация"
    data = await state.get_data()
    target_aud = data.get("target_aud", "all")
    amount = data.get("amount", 0)

    # Подсчет аудитории
    now = now_utc()
    stmt = select(func.count(User.id)).where(User.is_deleted.is_(False), User.is_banned.is_(False))
    if target_aud == "active":
        stmt = stmt.where(User.subscription_end > now)
    elif target_aud == "expired":
        stmt = stmt.where(User.subscription_end <= now)

    user_count = int((await session.scalar(stmt)) or 0)
    total_budget = user_count * amount

    await state.update_data(reason=reason, user_count=user_count)
    await state.set_state(AdminStates.confirming_mass_bonus)

    header = format_admin_breadcrumbs("🎁 Массовый бонус", "Подтверждение")
    aud_label = {"all": "Всем пользователям", "active": "Только с активной подпиской", "expired": "Только без подписки"}.get(target_aud, target_aud)

    text = (
        f"{header}"
        f"⚠️ <b>Подтверждение массового начисления бонусов:</b>\n\n"
        f"• Аудитория: <b>{aud_label}</b>\n"
        f"• Получателей: <b>{user_count} чел.</b>\n"
        f"• Бонус каждому: <b>+{amount} ₽</b>\n"
        f"• Общий бюджет бонусов: <b>{total_budget} ₽</b>\n"
        f"• Причина: <i>{safe(reason)}</i>\n\n"
        f"Вы уверены, что хотите запустить начисление?"
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text="🚀 Запустить начисление",
        callback_data="confirm_mass_bonus_apply",
    )
    builder.button(
        text="❌ Отмена",
        callback_data="admin_mass_bonus",
    )
    builder.adjust(1)

    await render_hub(
        message.bot,
        message.chat.id,
        text,
        builder.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "confirm_mass_bonus_apply")
async def apply_mass_bonus(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    data = await state.get_data()
    target_aud = data.get("target_aud", "all")
    amount = data.get("amount", 0)
    reason = data.get("reason", "Массовый бонус")

    await state.clear()
    await callback.answer("🚀 Начисление запущено!", show_alert=True)

    header = format_admin_breadcrumbs("🎁 Массовый бонус", "Результат")
    await callback.message.edit_text(
        f"{header}⏳ <b>Выполняется массовое начисление бонусов по {amount} ₽...</b>",
        parse_mode="HTML",
    )

    # Выборка юзеров
    now = now_utc()
    stmt = select(User.id, User.telegram_id).where(User.is_deleted.is_(False), User.is_banned.is_(False))
    if target_aud == "active":
        stmt = stmt.where(User.subscription_end > now)
    elif target_aud == "expired":
        stmt = stmt.where(User.subscription_end <= now)

    users = (await session.execute(stmt)).all()

    success_count = 0
    for uid, tg_id in users:
        try:
            idempotency_key = f"mass_bonus_{callback.from_user.id}_{uid}_{amount}"
            await create_admin_adjustment(
                session,
                user_id=uid,
                signed_amount=amount,
                idempotency_key=idempotency_key,
                metadata={"admin_id": callback.from_user.id, "reason": reason, "mass": True},
            )
            success_count += 1
            # Попытка уведомления
            try:
                await callback.bot.send_message(
                    tg_id,
                    f"🎁 <b>Вам начислен бонусный баланс: +{amount} ₽!</b>\n"
                    f"Причина: <i>{safe(reason)}</i>",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        except Exception as exc:
            logger.error("Failed mass bonus for user %s: %s", uid, exc)

    await AuditService.log_action(
        session,
        callback.from_user.id,
        "MASS_BONUS_GRANTED",
        "User",
        0,
        f"Granted +{amount} RUB bonus to {success_count} users. Reason: {reason}",
    )

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        f"{header}✅ <b>Массовое начисление завершено!</b>\n\nУспешно обработано: <b>{success_count} пользователей</b>.",
        get_back_button("admin_menu"),
        parse_mode="HTML",
    )
