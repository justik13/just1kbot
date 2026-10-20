from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot import texts
from config.settings import get_settings
from utils.telegram import (
    make_rich_button,
    make_rich_button_row,
    make_rich_nav_row,
    render_hub,
)

router = Router()


@router.callback_query(F.data == "menu_support")
async def hub_menu_support(
    callback: CallbackQuery,
    state: FSMContext,
):
    await callback.answer(show_alert=False)
    await state.clear()

    username = get_settings().SUPPORT_USERNAME.lstrip("@")
    user_id = callback.from_user.id if callback.from_user else None

    from utils.support import get_support_url

    support_greeting = (
        texts.SUPPORT_GREETING_TEMPLATE.format(telegram_id=user_id)
        if user_id
        else None
    )
    support_url = get_support_url(text=support_greeting, username=username)

    support_btn = make_rich_button(texts.BTN_CONTACT_SUPPORT, url=support_url, style="success")
    legal_btns = [
        make_rich_button(texts.BTN_TOS, url=texts.TOS_AGREEMENT_URL),
        make_rich_button(texts.BTN_PRIVACY_POLICY, url=texts.PRIVACY_POLICY_URL),
    ]
    nav_row = make_rich_nav_row(include_menu=True)

    rich_text = (
        texts.SUPPORT_TEXT.format(support_username=f"@{username}")
        + "\n"
        + make_rich_button_row(support_btn)
        + "\n"
        + make_rich_button_row(*legal_btns)
        + "\n"
        + nav_row
    )

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        rich_text,
        reply_markup=None,
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

    btn_incy = make_rich_button(texts.BTN_INSTRUCTION_INCY, callback_data=f"help_incy{suffix}", style="primary")
    btn_download = make_rich_button(texts.BTN_DOWNLOAD_AMNEZIA, callback_data=f"help_download{suffix}", style="primary")
    btn_ios = make_rich_button(texts.BTN_INSTRUCTION_IOS, callback_data=f"help_ios{suffix}")
    btn_windows = make_rich_button(texts.BTN_INSTRUCTION_WINDOWS, callback_data=f"help_windows{suffix}")
    btn_split = make_rich_button(texts.BTN_SPLIT_TUNNELING, callback_data=f"help_split{suffix}")
    btn_docs = make_rich_button(texts.BTN_AMNEZIA_DOCS, url=texts.AMNEZIA_DOCS)

    back_cb = f"manage_device:{device_id}" if device_id else "menu_support"
    nav_row = make_rich_nav_row(back_cb)

    rich_text = (
        text
        + "\n"
        + make_rich_button_row(btn_incy)
        + "\n"
        + make_rich_button_row(btn_download)
        + "\n"
        + make_rich_button_row(btn_ios, btn_windows)
        + "\n"
        + make_rich_button_row(btn_split, btn_docs)
        + "\n"
        + nav_row
    )

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        rich_text,
        reply_markup=None,
    )


@router.callback_query(F.data.startswith("help_incy"))
async def show_help_incy(callback: CallbackQuery):
    await callback.answer(show_alert=False)
    device_id = _extract_device_id(callback.data)
    back_cb = f"support_help:device_{device_id}" if device_id else "support_help"

    text = texts.SUPPORT_INCY_INSTRUCTION_TEXT
    btn_wl = make_rich_button(texts.BTN_WHITE_INTERNET, callback_data="white_internet", style="primary")
    nav_row = make_rich_nav_row(back_cb)

    rich_text = text + "\n" + make_rich_button_row(btn_wl) + "\n" + nav_row

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        rich_text,
        reply_markup=None,
    )


@router.callback_query(F.data.startswith("help_download"))
async def show_help_download(callback: CallbackQuery):
    await callback.answer(show_alert=False)
    device_id = _extract_device_id(callback.data)
    back_cb = f"support_help:device_{device_id}" if device_id else "support_help"

    text = texts.SUPPORT_DOWNLOAD_CLIENT_TEXT
    btn_site = make_rich_button(texts.BTN_OFFICIAL_SITE_AMNEZIA, url=texts.AMNEZIA_OFFICIAL_SITE, style="primary")
    nav_row = make_rich_nav_row(back_cb)

    rich_text = text + "\n" + make_rich_button_row(btn_site) + "\n" + nav_row

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        rich_text,
        reply_markup=None,
    )


@router.callback_query(F.data.startswith("help_ios"))
async def show_help_ios(callback: CallbackQuery):
    await callback.answer(show_alert=False)
    device_id = _extract_device_id(callback.data)
    back_cb = f"support_help:device_{device_id}" if device_id else "support_help"

    text = texts.SUPPORT_IOS_INSTRUCTION_TEXT
    nav_row = make_rich_nav_row(back_cb)

    rich_text = text + "\n" + nav_row

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        rich_text,
        reply_markup=None,
    )


@router.callback_query(F.data.startswith("help_windows"))
async def show_help_windows(callback: CallbackQuery):
    await callback.answer(show_alert=False)
    device_id = _extract_device_id(callback.data)
    back_cb = f"support_help:device_{device_id}" if device_id else "support_help"

    text = texts.SUPPORT_WINDOWS_INSTRUCTION_TEXT
    nav_row = make_rich_nav_row(back_cb)

    rich_text = text + "\n" + nav_row

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        rich_text,
        reply_markup=None,
    )


@router.callback_query(F.data.startswith("help_split"))
async def show_help_split(callback: CallbackQuery):
    await callback.answer(show_alert=False)
    device_id = _extract_device_id(callback.data)
    back_cb = f"support_help:device_{device_id}" if device_id else "support_help"

    text = texts.SUPPORT_SPLIT_TUNNELING_TEXT
    nav_row = make_rich_nav_row(back_cb)

    rich_text = text + "\n" + nav_row

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        rich_text,
        reply_markup=None,
    )


@router.callback_query(F.data == "faq")
async def show_faq(callback: CallbackQuery):
    await callback.answer(show_alert=False)

    nav_row = make_rich_nav_row("menu_support")
    rich_text = texts.FAQ_TEXT + "\n" + nav_row

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        rich_text,
        reply_markup=None,
    )
