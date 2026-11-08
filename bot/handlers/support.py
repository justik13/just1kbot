"""Support and Information handler for Just1k 2.0 Telegram Bot Hub."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts
from config.settings import get_settings
from utils.telegram import render_hub

router = Router()


def _info_keyboard(username: str, telegram_id: int | None = None):
    builder = InlineKeyboardBuilder()

    from utils.support import get_support_url

    greeting = (
        texts.SUPPORT_GREETING_TEMPLATE.format(telegram_id=telegram_id)
        if telegram_id
        else None
    )
    support_url = get_support_url(text=greeting, username=username)

    # 1. Row: Написать в поддержку
    builder.button(
        text=texts.BTN_CONTACT_SUPPORT,
        url=support_url,
    )

    # 2. Row: Инструкции | Частые вопросы
    builder.button(
        text=texts.BTN_HELP_INSTRUCTIONS,
        callback_data="support_help",
    )
    builder.button(
        text="❓ Вопросы и ответы",
        callback_data="info_faq",
    )

    # 3. Row: Юридические документы
    builder.button(
        text="📜 Юр. документы",
        callback_data="info_legal",
    )

    # 4. Row: Главное меню
    builder.button(
        text=texts.BTN_MAIN_MENU_NAV,
        callback_data="back_to_main_menu",
    )

    builder.adjust(1, 2, 1, 1)
    return builder.as_markup()


@router.callback_query(F.data.in_({"menu_support", "menu_info"}))
async def hub_menu_info(
    callback: CallbackQuery,
    state: FSMContext,
):
    await callback.answer(show_alert=False)
    await state.clear()

    username = get_settings().SUPPORT_USERNAME.lstrip("@")
    user_id = callback.from_user.id if callback.from_user else None

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.SUPPORT_TEXT,
        _info_keyboard(username, telegram_id=user_id),
    )


@router.callback_query(F.data == "info_faq")
async def show_faq(callback: CallbackQuery):
    await callback.answer(show_alert=False)
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_BACK,
        callback_data="menu_info",
    )
    builder.adjust(1)
    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.FAQ_TEXT,
        builder.as_markup(),
    )


@router.callback_query(F.data == "info_legal")
async def show_legal_docs(callback: CallbackQuery):
    await callback.answer(show_alert=False)
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_TOS,
        url=texts.TOS_AGREEMENT_URL,
    )
    builder.button(
        text=texts.BTN_PRIVACY_POLICY,
        url=texts.PRIVACY_POLICY_URL,
    )
    builder.button(
        text=texts.BTN_BACK,
        callback_data="menu_info",
    )
    builder.adjust(1, 1, 1)

    text = (
        "<b>Юридическая информация и документы</b>\n\n"
        "Ознакомьтесь с официальными документами нашего сервиса:"
    )
    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        builder.as_markup(),
    )


@router.callback_query(F.data.startswith("support_help"))
async def show_support_help(callback: CallbackQuery):
    await callback.answer(show_alert=False)
    text = texts.SUPPORT_HELP_ROOT_TEXT

    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_INSTRUCTION_INCY,
        callback_data="help_incy",
    )
    builder.button(
        text=texts.BTN_BACK,
        callback_data="menu_info",
    )
    builder.adjust(1, 1)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        builder.as_markup(),
    )


@router.callback_query(F.data.startswith("help_incy"))
async def show_help_incy(callback: CallbackQuery):
    await callback.answer(show_alert=False)
    text = texts.SUPPORT_INCY_INSTRUCTION_TEXT

    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_BACK,
        callback_data="support_help",
    )
    builder.button(
        text=texts.BTN_MAIN_MENU_NAV,
        callback_data="back_to_main_menu",
    )
    builder.adjust(1, 1)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        builder.as_markup(),
    )
