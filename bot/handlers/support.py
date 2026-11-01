from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts
from config.settings import get_settings
from utils.telegram import render_hub

router = Router()


def _support_keyboard(username: str, telegram_id: int | None = None):
    builder = InlineKeyboardBuilder()

    from utils.support import get_support_url

    text = (
        texts.SUPPORT_GREETING_TEMPLATE.format(telegram_id=telegram_id)
        if telegram_id
        else None
    )
    support_url = get_support_url(text=text, username=username)

    builder.button(
        text=texts.BTN_CONTACT_SUPPORT,
        url=support_url,
    )

    builder.button(
        text=texts.BTN_HELP_INSTRUCTIONS,
        callback_data="support_help",
    )

    builder.button(
        text=texts.SUPPORT,
        callback_data="faq",
    )

    builder.button(
        text=texts.BTN_TOS,
        url=texts.TOS_AGREEMENT_URL,
    )

    builder.button(
        text=texts.BTN_PRIVACY_POLICY,
        url=texts.PRIVACY_POLICY_URL,
    )

    builder.button(
        text=texts.BTN_MAIN_MENU_NAV,
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


def _extract_device_id(data: str) -> int | None:
    if ":" in data and "device_" in data:
        parts = data.split(":")
        for p in parts:
            if p.startswith("device_") and p.replace("device_", "").isdigit():
                return int(p.replace("device_", ""))
    return None


@router.callback_query(F.data.startswith("support_help"))
async def show_support_help(callback: CallbackQuery):
    await callback.answer(show_alert=False)
    device_id = _extract_device_id(callback.data)
    suffix = f":device_{device_id}" if device_id else ""

    text = texts.SUPPORT_HELP_ROOT_TEXT

    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_INSTRUCTION_INCY,
        callback_data=f"help_incy{suffix}",
    )
    builder.button(
        text=texts.BTN_BACK,
        callback_data=f"manage_device:{device_id}" if device_id else "menu_support",
    )
    builder.button(
        text=texts.BTN_MAIN_MENU_NAV,
        callback_data="back_to_main_menu",
    )
    builder.adjust(1, 2)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        builder.as_markup(),
    )


@router.callback_query(F.data.startswith("help_incy"))
async def show_help_incy(callback: CallbackQuery):
    await callback.answer(show_alert=False)
    device_id = _extract_device_id(callback.data)
    back_cb = f"support_help:device_{device_id}" if device_id else "support_help"

    text = texts.SUPPORT_INCY_INSTRUCTION_TEXT

    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_CONNECTIONS,
        callback_data="menu_connections",
    )
    builder.button(
        text=texts.BTN_WHITE_INTERNET,
        callback_data="white_internet",
    )
    builder.button(
        text=texts.BTN_BACK,
        callback_data=back_cb,
    )
    builder.button(
        text=texts.BTN_MAIN_MENU_NAV,
        callback_data="back_to_main_menu",
    )
    builder.adjust(2, 2)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        builder.as_markup(),
    )


@router.callback_query(
    F.data.startswith("help_download")
    | F.data.startswith("help_ios")
    | F.data.startswith("help_windows")
    | F.data.startswith("help_split")
)
async def decommissioned_amnezia_help(callback: CallbackQuery):
    await callback.answer(
        texts.ALERT_AMNEZIA_DECOMMISSIONED,
        show_alert=True,
    )


@router.callback_query(F.data == "faq")
async def show_faq(callback: CallbackQuery):
    await callback.answer(show_alert=False)

    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_BACK, callback_data="menu_support")
    builder.button(text=texts.BTN_MAIN_MENU_NAV, callback_data="back_to_main_menu")
    builder.adjust(2)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.FAQ_TEXT,
        builder.as_markup(),
    )
