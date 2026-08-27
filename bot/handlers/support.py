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
    text_param = quote(texts.UI_U_ZDRAVSTVUYTE_MOY_ID_28.format(telegram_id=telegram_id)) if telegram_id else ""
    support_url = f"https://t.me/{username}?text={text_param}" if text_param else f"https://t.me/{username}"

    builder.button(
        text=texts.UI_U_NAPISAT_V_PODDERZHKU_32,
        url=support_url,
    )

    builder.button(
        text=texts.UI_U_POMOSHCH_I_INSTRUKTSII_37,
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

    text = (
        texts.UI_U_INSTRUKTSII_I_SPRAVKA_AMNEZIAV_101+
        texts.UI_U_V_ETOM_RAZDELE_VY_NAYDETE_RUKO_102+
        texts.UI_U_PRAVILA_I_OSOBENNOSTI_RABOTY_103+
        texts.UI_U_NE_REKOMENDUETSYA_ISPOLZOVAT_T_104+
        texts.UI_U_CHAST_SAYTOV_SERVISOV_MOZHET_B_105+
        texts.UI_U_VYBERITE_NUZHNUYU_TEMU_NIZHE_106
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_U_SKACHAT_KLIENT_AMNEZIA_111,
        callback_data=f"help_download{suffix}",
    )
    builder.button(
        text=texts.UI_U_INSTRUKTSIYA_IOS_DLYA_RF_115,
        callback_data=f"help_ios{suffix}",
    )
    builder.button(
        text=texts.UI_U_INSTRUKTSII_WINDOWS_119,
        callback_data=f"help_windows{suffix}",
    )
    builder.button(
        text=texts.UI_U_RAZDELNOE_TUNNELIROVANIE_123,
        callback_data=f"help_split{suffix}",
    )
    builder.button(
        text=texts.UI_U_DOKUMENTATSIYA_AMNEZIA_127,
        url=AMNEZIA_DOCS,
    )
    if device_id:
        builder.button(
            text=texts.UI_U_NAZAD_V_USTROYSTVO_132,
            callback_data=f"manage_device:{device_id}",
        )
    else:
        builder.button(
            text=texts.UI_U_NAZAD_V_PODDERZHKU_137,
            callback_data="menu_support",
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

    text = (
        texts.UI_U_SKACHAT_KLIENT_AMNEZIA_DLYA_PO_157+
        texts.UI_U_OFITSIALNYE_SSYLKI_DLYA_ZAGRUZ_158+
        texts.UI_U_PRYAMAYA_SSYLKA_ZERKALO_ZAGRUZ_159+
        texts.UI_U_GITHUB_RELEASES_RELIZY_I_BINAR_160+
        texts.UI_U_OFITSIALNYY_SAYT_AMNEZIA_PODRO_161
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_U_SKACHAT_KLIENT_ZERKALO_166,
        url=AMNEZIA_DOWNLOAD_MIRROR,
    )
    builder.button(
        text=texts.UI_U_POSLEDNYAYA_VERSIYA_GITHUB_170,
        url=AMNEZIA_GITHUB_LATEST,
    )
    builder.button(
        text=texts.UI_U_OFITSIALNYY_SAYT_AMNEZIA_174,
        url=AMNEZIA_OFFICIAL_SITE,
    )
    builder.button(
        text=texts.UI_U_NAZAD_178,
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

    text = (
        texts.UI_U_USTANOVKA_AMNEZIAVPN_NA_IOS_DL_198+
        texts.UI_U_PODROBNOE_POSHAGOVOE_RUKOVODST_199
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_U_OTKRYT_INSTRUKTSIYU_IOS_204,
        url=AMNEZIA_IOS_RU,
    )
    builder.button(
        text=texts.UI_U_NAZAD_208,
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

    text = (
        texts.UI_U_INSTRUKTSII_DLYA_WINDOWS_228+
        texts.UI_U_RUKOVODSTVA_PO_USTANOVKE_I_OBN_229
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_U_USTANOVKA_AMNEZIAVPN_NA_WINDOW_234,
        url=AMNEZIA_WIN_INSTALL,
    )
    builder.button(
        text=texts.UI_U_OBNOVLENIE_AMNEZIAVPN_NA_WINDO_238,
        url=AMNEZIA_WIN_UPDATE,
    )
    builder.button(
        text=texts.UI_U_NAZAD_242,
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

    text = (
        texts.UI_U_INSTRUKTSIYA_DLYA_RAZDELNOGO_T_262+
        texts.UI_U_RAZDELNOE_TUNNELIROVANIE_SPLIT_263
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_U_INSTRUKTSIYA_PO_RAZDELNOMU_TUN_268,
        url=AMNEZIA_SPLIT_TUNNELING,
    )
    builder.button(
        text=texts.UI_U_NAZAD_272,
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
