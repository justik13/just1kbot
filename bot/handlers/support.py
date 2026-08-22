from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts
from bot.keyboards import get_back_button
from config.settings import get_settings
from utils.telegram import render_hub

router = Router()


AMNEZIA_APP_STORE = "https://apps.apple.com/app/amneziavpn/id1600529900"
AMNEZIA_GOOGLE_PLAY = "https://play.google.com/store/apps/details?id=org.amnezia.vpn"
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
        text="💬 Написать в поддержку",
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
        "📖 <b>Инструкция по подключению AmneziaVPN</b>\n\n"
        "🚀 <b>Быстрый старт за 3 шага:</b>\n"
        "1. Скачайте приложение <b>AmneziaVPN</b> на ваш телефон или ПК.\n"
        "2. Скопируйте ключ подключения в разделе «📱 Мои подключения».\n"
        "3. В приложении AmneziaVPN нажмите <b>«Приступим»</b> (или <b>«+»</b> внизу справа) → нажмите кнопку <b>«Вставить»</b> → <b>«Подключиться»</b>.\n\n"
        "<blockquote expandable>✅ <b>Как понять, что всё работает:</b>\n"
        "• Индикатор в приложении станет золотистым с надписью <b>«Подключено»</b>;\n"
        "• В строке состояния вверху экрана появится значок <b>VPN</b> (или 🔑);\n"
        "• На сайте <code>2ip.ru</code> отобразится страна вашего сервера;\n"
        "• Заблокированные и замедленные сервисы (YouTube, Instagram, ChatGPT) открываются без ограничений.</blockquote>\n\n"
        "Выберите раздел подробных руководств ниже:"
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text="📥 Скачать AmneziaVPN",
        callback_data=f"help_download{suffix}",
    )
    builder.button(
        text="🔗 Приложение INCY (iOS / Android)",
        callback_data=f"help_incy{suffix}",
    )
    builder.button(
        text="🍏 DefaultVPN (для iOS)",
        callback_data=f"help_defaultvpn{suffix}",
    )
    builder.button(
        text="🍏 Инструкция iOS (для РФ)",
        callback_data=f"help_ios{suffix}",
    )
    builder.button(
        text="💻 Инструкции Windows",
        callback_data=f"help_windows{suffix}",
    )
    builder.button(
        text="🔀 Раздельное Туннелирование",
        callback_data=f"help_split{suffix}",
    )
    builder.button(
        text="📚 Документация Amnezia",
        url=AMNEZIA_DOCS,
    )
    if device_id:
        builder.button(
            text="← Назад в устройство",
            callback_data=f"manage_device:{device_id}",
        )
    else:
        builder.button(
            text="← Назад в поддержку",
            callback_data="menu_support",
        )
    builder.adjust(1, 1, 1, 1, 1, 1, 1, 1)

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

    text = (
        "🔗 <b>Приложение INCY (iOS / Android)</b>\n\n"
        "<b>INCY</b> — мобильное приложение с поддержкой протокола AmneziaWG, позволяющее подключить <b>все ваши серверы сразу</b> через одну самообновляемую подписку.\n\n"
        "📖 <b>Как настроить на телефоне:</b>\n"
        "1. Установите <b>INCY</b> из App Store или Google Play;\n"
        "2. В главном меню бота откройте «🔌 Мои подключения» → <b>«🔗 Добавить в INCY»</b>;\n"
        "3. Нажмите <b>«📱 Открыть в INCY»</b> — приложение сразу импортирует все серверы;\n"
        "4. Выберите локацию и нажмите переключатель для включения защиты.\n\n"
        "<i>💡 При создании новых устройств или смене серверов список в INCY обновляется автоматически!</i>"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="🍏 App Store (iOS)", url="https://apps.apple.com/app/incy/id6756943388")
    builder.button(text="🤖 Google Play (Android)", url="https://play.google.com/store/apps/details?id=llc.itdev.incy")
    builder.button(text="🌐 Сайт INCY", url="https://incy.cc/")
    builder.button(text="← Назад", callback_data=back_cb)
    builder.adjust(1, 1, 1, 1)

    await render_hub(callback.bot, callback.message.chat.id, text, builder.as_markup())


@router.callback_query(F.data.startswith("help_defaultvpn"))
async def show_help_defaultvpn(callback: CallbackQuery):
    await callback.answer(show_alert=False)
    device_id = _extract_device_id(callback.data)
    back_cb = f"support_help:device_{device_id}" if device_id else "support_help"

    text = (
        "🍏 <b>DefaultVPN для iOS (iPhone / iPad)</b>\n\n"
        "<b>DefaultVPN</b> — легкий нативный клиент от команды Amnezia, оптимизированный под iOS 17+.\n\n"
        "📖 <b>Как настроить:</b>\n"
        "1. Установите <b>DefaultVPN</b> из App Store;\n"
        "2. Скопируйте ключ подключения <code>vpn://...</code> в карточке устройства;\n"
        "3. Откройте DefaultVPN и вставьте ключ (или импортируйте файл <code>.conf</code> из раздела «🔄 Другой способ подключения»);\n"
        "4. Включите подключение переключателем."
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="🍏 DefaultVPN в App Store", url="https://apps.apple.com/app/defaultvpn/id6744725017")
    builder.button(text="← Назад", callback_data=back_cb)
    builder.adjust(1, 1)

    await render_hub(callback.bot, callback.message.chat.id, text, builder.as_markup())


@router.callback_query(F.data.startswith("help_download"))
async def show_help_download(callback: CallbackQuery):
    await callback.answer(show_alert=False)
    device_id = _extract_device_id(callback.data)
    back_cb = f"support_help:device_{device_id}" if device_id else "support_help"

    text = (
        "📥 <b>Скачать клиент AmneziaVPN</b>\n\n"
        "Выберите вашу платформу для быстрой установки приложения:\n\n"
        "• <b>iOS (iPhone / iPad)</b> — установка из App Store\n"
        "• <b>Android</b> — Google Play или прямой APK (GitHub)\n"
        "• <b>Windows / macOS / Linux</b> — официальный установщик (Зеркало)"
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text="🍏 App Store (iPhone / iPad)",
        url=AMNEZIA_APP_STORE,
    )
    builder.button(
        text="🤖 Google Play (Android)",
        url=AMNEZIA_GOOGLE_PLAY,
    )
    builder.button(
        text="💻 Windows / Mac (Зеркало)",
        url=AMNEZIA_DOWNLOAD_MIRROR,
    )
    builder.button(
        text="📦 GitHub Releases (APK / Бинарники)",
        url=AMNEZIA_GITHUB_LATEST,
    )
    builder.button(
        text="🏠 Официальный сайт Amnezia",
        url=AMNEZIA_OFFICIAL_SITE,
    )
    builder.button(
        text="← Назад",
        callback_data=back_cb,
    )
    builder.adjust(1, 1, 1, 1, 1, 1)

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
        "🔀 <b>Инструкция для Раздельного Туннелирования</b>\n\n"
        "Раздельное туннелирование (Split Tunneling) позволяет направить через выбранное подключение только нужные сайты или приложения, сохраняя обычное подключение для остальных ресурсов."
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text="📖 Инструкция по Раздельному Туннелированию",
        url=AMNEZIA_SPLIT_TUNNELING,
    )
    builder.button(
        text="← Назад",
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
