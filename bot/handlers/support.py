from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts
from bot.keyboards import get_back_button
from config.settings import get_settings
from utils.telegram import render_hub

router = Router()


def _support_keyboard(username: str, telegram_id: int | None = None):
    builder = InlineKeyboardBuilder()

    from urllib.parse import quote
    text_param = quote(f"Здравствуйте! Мой ID: {telegram_id}") if telegram_id else ""
    support_url = f"https://t.me/{username}?text={text_param}" if text_param else f"https://t.me/{username}"

    builder.button(
        text=texts.UI_BOT_HANDLERS_SUPPORT_L18_1.format(value_0=username),
        url=support_url,
    )

    builder.button(
        text="ℹ️ Помощь",
        callback_data="support_help",
    )

    builder.button(
        text=texts.UI_BOT_HANDLERS_SUPPORT_L23_1,
        callback_data="faq",
    )

    builder.button(
        text=texts.UI_BOT_HANDLERS_SUPPORT_L28_1,
        url=texts.TOS_AGREEMENT_URL,
    )

    builder.button(
        text=texts.UI_BOT_HANDLERS_SUPPORT_L33_1,
        url=texts.PRIVACY_POLICY_URL,
    )

    builder.button(
        text=texts.UI_BOT_HANDLERS_SUPPORT_L38_1,
        callback_data="back_to_main_menu",
    )

    builder.adjust(1, 2, 2, 1)

    return builder.as_markup()


@router.callback_query(F.data == "menu_support")
async def hub_menu_support(
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
        texts.SUPPORT_TEXT.format(support_username=f"@{username}"),
        _support_keyboard(username, telegram_id=user_id),
    )


@router.callback_query(F.data == "support_help")
async def show_support_help(callback: CallbackQuery):
    await callback.answer("Раздел «Помощь» находится в разработке 🛠", show_alert=True)


@router.callback_query(F.data == "faq")
async def show_faq(callback: CallbackQuery):
    await callback.answer(show_alert=False)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.FAQ_TEXT,
        get_back_button("menu_support"),
    )