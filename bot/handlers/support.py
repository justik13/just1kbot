from aiogram import Router, F
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
    text_param = quote(f"Здравствуйте! Мой ID: {telegram_id}") if telegram_id else ""
    support_url = f"https://t.me/{username}?text={text_param}" if text_param else f"https://t.me/{username}"

    builder.button(
        text=texts.UI_BOT_HANDLERS_SUPPORT_L18_1.format(value_0=username),
        url=support_url,
    )

    builder.button(
        text="ℹ️ Помощь и Инструкции",
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
    await callback.answer(show_alert=False)

    text = (
        "📖 <b>Инструкции и справка AmneziaVPN</b>\n\n"
        "В этом разделе вы найдёте руководства по подключению, скачиванию клиента и настройке сервиса.\n\n"
        "⚠️ <b>Правила и особенности работы:</b>\n"
        "• <b>Не рекомендуется использовать торренты/P2P.</b>\n"
        "• <b>Часть сайтов/сервисов может быть недоступна по решение провайдеров.</b>\n\n"
        "Выберите нужную тему ниже:"
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text="📥 Скачать клиент Amnezia",
        callback_data="help_download",
    )
    builder.button(
        text="🍏 Инструкция iOS (для РФ)",
        callback_data="help_ios",
    )
    builder.button(
        text="💻 Инструкции Windows",
        callback_data="help_windows",
    )
    builder.button(
        text="🔀 Раздельное Туннелирование",
        callback_data="help_split",
    )
    builder.button(
        text="📚 Документация Amnezia",
        url=AMNEZIA_DOCS,
    )
    builder.button(
        text="← Назад в поддержку",
        callback_data="menu_support",
    )
    builder.adjust(1, 1, 1, 1, 1, 1)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        builder.as_markup(),
    )


@router.callback_query(F.data == "help_download")
async def show_help_download(callback: CallbackQuery):
    await callback.answer(show_alert=False)

    text = (
        "📥 <b>Скачать клиент Amnezia для подключения</b>\n\n"
        "Официальные ссылки для загрузки приложения AmneziaVPN для Windows, Android, iOS, macOS и Linux:\n\n"
        "• <b>Прямая ссылка (Зеркало)</b> — загрузка последней сборки клиенту\n"
        "• <b>GitHub Releases</b> — релизы и бинарные файлы всех версий\n"
        "• <b>Официальный сайт Amnezia</b> — подробная информация"
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text="🌐 Скачать клиент (Зеркало)",
        url=AMNEZIA_DOWNLOAD_MIRROR,
    )
    builder.button(
        text="📦 Последняя версия (GitHub)",
        url=AMNEZIA_GITHUB_LATEST,
    )
    builder.button(
        text="🏠 Официальный сайт Amnezia",
        url=AMNEZIA_OFFICIAL_SITE,
    )
    builder.button(
        text="← Назад",
        callback_data="support_help",
    )
    builder.adjust(1, 1, 1, 1)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        builder.as_markup(),
    )


@router.callback_query(F.data == "help_ios")
async def show_help_ios(callback: CallbackQuery):
    await callback.answer(show_alert=False)

    text = (
        "🍏 <b>Установка AmneziaVPN на iOS для жителей России</b>\n\n"
        "Подробное пошаговое руководство по скачиванию и первичной настройке приложения AmneziaVPN на iPhone и iPad в условиях региональных ограничений App Store."
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text="📖 Открыть инструкцию iOS",
        url=AMNEZIA_IOS_RU,
    )
    builder.button(
        text="← Назад",
        callback_data="support_help",
    )
    builder.adjust(1, 1)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        builder.as_markup(),
    )


@router.callback_query(F.data == "help_windows")
async def show_help_windows(callback: CallbackQuery):
    await callback.answer(show_alert=False)

    text = (
        "💻 <b>Инструкции для Windows</b>\n\n"
        "Руководства по установке и обновлению приложения AmneziaVPN на ПК под управлением Windows:"
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text="📥 Установка AmneziaVPN на Windows",
        url=AMNEZIA_WIN_INSTALL,
    )
    builder.button(
        text="🔄 Обновление AmneziaVPN на Windows",
        url=AMNEZIA_WIN_UPDATE,
    )
    builder.button(
        text="← Назад",
        callback_data="support_help",
    )
    builder.adjust(1, 1, 1)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        builder.as_markup(),
    )


@router.callback_query(F.data == "help_split")
async def show_help_split(callback: CallbackQuery):
    await callback.answer(show_alert=False)

    text = (
        "🔀 <b>Инструкция для Раздельного Туннелирования</b>\n\n"
        "Раздельное туннелирование (Split Tunneling) позволяет направить через VPN-соединение только выбранные сайты или приложения, сохраняя прямое быстрое подключение для всех остальных ресурсов."
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text="📖 Инструкция по Раздельному Туннелированию",
        url=AMNEZIA_SPLIT_TUNNELING,
    )
    builder.button(
        text="← Назад",
        callback_data="support_help",
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