from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts
from bot.keyboards import get_back_button
from config.settings import get_settings
from utils.telegram import render_hub

router = Router()


AMNEZIA_DOWNLOAD_MIRROR = "https://storage.googleapis.com/amnezia/amnezia.org?m-path=/downloads"
AMNEZIA_GITHUB_LATEST = "https://github.com/amnezia-vpn/amnezia-client/releases/latest"
AMNEZIA_OFFICIAL_SITE = "https://storage.googleapis.com/amnezia/amnezia.org"
AMNEZIA_DOCS = "https://storage.googleapis.com/amnezia/docs?m-path=/"
AMNEZIA_SPLIT_TUNNELING = "https://storage.googleapis.com/amnezia/docs?m-path=/documentation/instructions/vpn-split-tunneling/"
AMNEZIA_IOS_RU = "https://storage.googleapis.com/amnezia/docs?m-path=/documentation/instructions/installing-amneziavpn-on-ios/"
AMNEZIA_WIN_INSTALL = "https://storage.googleapis.com/amnezia/docs?m-path=/documentation/instructions/installing-app-on-windows/"
AMNEZIA_WIN_UPDATE = "https://storage.googleapis.com/amnezia/docs?m-path=/documentation/instructions/application-update-on-windows/"


def _support_keyboard(username: str, telegram_id: int | None = None):
    builder = InlineKeyboardBuilder()

    from urllib.parse import quote
    text_param = quote(texts.SUPPORT_GREETING_TEMPLATE.format(telegram_id=telegram_id)) if telegram_id else ""
    support_url = f"https://t.me/{username}?text={text_param}" if text_param else f"https://t.me/{username}"

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
        text=texts.BTN_MAIN_MENU,
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
        text=texts.BTN_DOWNLOAD_AMNEZIA,
        callback_data=f"help_download{suffix}",
    )
    builder.button(
        text=texts.BTN_INSTRUCTION_IOS,
        callback_data=f"help_ios{suffix}",
    )
    builder.button(
        text=texts.BTN_INSTRUCTION_WINDOWS,
        callback_data=f"help_windows{suffix}",
    )
    builder.button(
        text=texts.BTN_SPLIT_TUNNELING,
        callback_data=f"help_split{suffix}",
    )
    builder.button(
        text=texts.BTN_AMNEZIA_DOCS,
        url=AMNEZIA_DOCS,
    )
    builder.button(
        text=texts.BTN_BACK,
        callback_data=f"manage_device:{device_id}" if device_id else "menu_support",
    )
    builder.adjust(1, 1, 1, 1, 1, 1)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        builder.as_markup(),
    )


@router.callback_query(F.data.startswith("help_download"))
async def show_help_download(callback: CallbackQuery):
    await callback.answer(show_alert=False)
    device_id = _extract_device_id(callback.data)
    back_cb = f"support_help:device_{device_id}" if device_id else "support_help"

    text = texts.SUPPORT_DOWNLOAD_CLIENT_TEXT

    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_DOWNLOAD_MIRROR,
        url=AMNEZIA_DOWNLOAD_MIRROR,
    )
    builder.button(
        text=texts.BTN_LATEST_VERSION_GITHUB,
        url=AMNEZIA_GITHUB_LATEST,
    )
    builder.button(
        text=texts.BTN_OFFICIAL_SITE_AMNEZIA,
        url=AMNEZIA_OFFICIAL_SITE,
    )
    builder.button(
        text=texts.BTN_BACK,
        callback_data=back_cb,
    )
    builder.adjust(1, 1, 1, 1)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        builder.as_markup(),
    )


@router.callback_query(F.data.startswith("help_ios"))
async def show_help_ios(callback: CallbackQuery):
    await callback.answer(show_alert=False)
    device_id = _extract_device_id(callback.data)
    back_cb = f"support_help:device_{device_id}" if device_id else "support_help"

    text = texts.SUPPORT_IOS_INSTRUCTION_TEXT

    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_OPEN_IOS_INSTRUCTION,
        url=AMNEZIA_IOS_RU,
    )
    builder.button(
        text=texts.BTN_BACK,
        callback_data=back_cb,
    )
    builder.adjust(1, 1)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        builder.as_markup(),
    )


@router.callback_query(F.data.startswith("help_windows"))
async def show_help_windows(callback: CallbackQuery):
    await callback.answer(show_alert=False)
    device_id = _extract_device_id(callback.data)
    back_cb = f"support_help:device_{device_id}" if device_id else "support_help"

    text = texts.SUPPORT_WINDOWS_INSTRUCTION_TEXT

    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_INSTALL_WINDOWS,
        url=AMNEZIA_WIN_INSTALL,
    )
    builder.button(
        text=texts.BTN_UPDATE_WINDOWS,
        url=AMNEZIA_WIN_UPDATE,
    )
    builder.button(
        text=texts.BTN_BACK,
        callback_data=back_cb,
    )
    builder.adjust(1, 1, 1)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        builder.as_markup(),
    )


@router.callback_query(F.data.startswith("help_split"))
async def show_help_split(callback: CallbackQuery):
    await callback.answer(show_alert=False)
    device_id = _extract_device_id(callback.data)
    back_cb = f"support_help:device_{device_id}" if device_id else "support_help"

    text = texts.SUPPORT_SPLIT_TUNNELING_TEXT

    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.BTN_OPEN_SPLIT_TUNNEL_INSTRUCTION,
        url=AMNEZIA_SPLIT_TUNNELING,
    )
    builder.button(
        text=texts.BTN_BACK,
        callback_data=back_cb,
    )
    builder.adjust(1, 1)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        builder.as_markup(),
    )


@router.callback_query(F.data == "faq")
async def show_faq(callback: CallbackQuery):
    await callback.answer(show_alert=False)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.FAQ_TEXT,
        get_back_button("menu_support"),
    )
