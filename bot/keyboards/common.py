"""Common inline keyboards for Just1k 2.0 Telegram Bot Hub."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardMarkup,
    InputRichBlockButtons,
    InputRichBlockDivider,
    InputRichBlockExpandableBlockQuotation,
    InputRichBlockParagraph,
    InputRichMessage,
    RichMessageButton,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts


def get_hub_keyboard(
    is_admin: bool = False,
    is_active: bool = False,
    mtproto_url: str | None = None,
    miniapp_url: str | None = None,
    real_balance: int = 0,
    bonus_balance: int = 0,
    channel_url: str | None = None,
    support_username: str | None = None,
    telegram_id: int | None = None,
) -> InlineKeyboardMarkup:
    """Build single rich hub keyboard strictly matching just1kbotv2.md."""
    builder = InlineKeyboardBuilder()

    # Row 1: Главная кнопка - Открытие Mini App
    target_miniapp_url = miniapp_url or "https://just1k.pro/app"
    builder.button(
        text=texts.BTN_HUB_CONNECT,
        web_app=WebAppInfo(url=target_miniapp_url),
    )

    # Row 2: Рефералы | Баланс
    bal_label = texts.BTN_HUB_BALANCE.format(real=real_balance, bonus=bonus_balance)
    builder.button(
        text=texts.BTN_HUB_REFERRALS,
        callback_data="menu_referral",
    )
    builder.button(
        text=bal_label,
        callback_data="menu_balance",
    )

    # Row 3: Прокси TG | TG КАНАЛ
    has_proxy = bool(mtproto_url)
    has_channel = bool(channel_url)
    if has_proxy:
        builder.button(
            text=texts.BTN_HUB_PROXY,
            url=mtproto_url,
        )
    if has_channel:
        builder.button(
            text=texts.BTN_HUB_CHANNEL,
            url=channel_url,
        )

    # Row 4: Информация | Поддержка
    builder.button(
        text=texts.BTN_HUB_INFO,
        callback_data="menu_info",
    )

    from utils.support import get_support_url

    greeting = (
        texts.SUPPORT_GREETING_TEMPLATE.format(telegram_id=telegram_id)
        if telegram_id
        else None
    )
    support_url = get_support_url(text=greeting, username=support_username)
    builder.button(
        text=texts.BTN_SUPPORT,
        url=support_url,
    )

    sizes = [1, 2]
    row3_count = (1 if has_proxy else 0) + (1 if has_channel else 0)
    if row3_count > 0:
        sizes.append(row3_count)
    sizes.append(2)

    builder.adjust(*sizes)
    return builder.as_markup()


def get_back_button(
    callback_data: str = "back_to_main_menu",
    text: str | None = None,
) -> InlineKeyboardMarkup:
    """Standard back button for single-screen hub navigation."""
    builder = InlineKeyboardBuilder()

    if text is not None:
        button_text = text
    elif callback_data == "back_to_main_menu":
        button_text = texts.BTN_MAIN_MENU_NAV
    else:
        button_text = texts.BTN_BACK

    builder.button(text=button_text, callback_data=callback_data)
    return builder.as_markup()


def build_rich_hub_message(
    name: str,
    telegram_id: int,
    status_str: str,
    valid_until_str: str,
    days_left_str: str,
    devices_count: int,
    device_limit: int,
    real_balance: int,
    bonus_balance: int,
    white_internet_line: str = "",
    bonus_line: str = "",
    inviter_line: str = "",
    mtproto_url: str | None = None,
    miniapp_url: str | None = None,
    channel_url: str | None = None,
    support_username: str | None = None,
) -> InputRichMessage:
    """Construct native Telegram Bot API 2026 Rich Message with RichBlockButtons."""
    from utils.support import get_support_url

    target_miniapp_url = miniapp_url or "https://just1k.pro/app"

    header_block = InputRichBlockParagraph(text=f"👋 Привет, <b>{name}</b>!")

    status_card_text = (
        f"📊 <b>Статус доступа:</b> {status_str}\n"
        f"⏳ <b>Срок действия:</b> {valid_until_str} ({days_left_str})\n"
        f"📱 <b>Подключено устройств:</b> {devices_count} из {device_limit}"
        f"{white_internet_line}"
    )
    quote_block = InputRichBlockExpandableBlockQuotation(text=status_card_text)

    info_card_text = (
        f"💳 <b>Баланс:</b> {real_balance} ₽{bonus_line}\n"
        f"🆔 <b>Ваш ID:</b> <code>{telegram_id}</code>{inviter_line}"
    )
    info_block = InputRichBlockParagraph(text=info_card_text)

    divider_block = InputRichBlockDivider()

    # Native Rich Buttons:
    # Row 1: Главная кнопка открытия Mini App
    btn_connect = RichMessageButton(
        text=texts.BTN_HUB_CONNECT,
        web_app=WebAppInfo(url=target_miniapp_url),
    )
    row1 = InputRichBlockButtons(buttons=[btn_connect])

    # Row 2: Рефералы | Баланс
    bal_label = texts.BTN_HUB_BALANCE.format(real=real_balance, bonus=bonus_balance)
    btn_refs = RichMessageButton(
        text=texts.BTN_HUB_REFERRALS,
        callback_data="menu_referral",
    )
    btn_bal = RichMessageButton(
        text=bal_label,
        callback_data="menu_balance",
    )
    row2 = InputRichBlockButtons(buttons=[btn_refs, btn_bal])

    blocks = [header_block, quote_block, info_block, divider_block, row1, row2]

    # Row 3: Прокси TG | TG Канал (если есть)
    row3_btns: list[RichMessageButton] = []
    if mtproto_url:
        row3_btns.append(RichMessageButton(text=texts.BTN_HUB_PROXY, url=mtproto_url))
    if channel_url:
        row3_btns.append(RichMessageButton(text=texts.BTN_HUB_CHANNEL, url=channel_url))
    if row3_btns:
        blocks.append(InputRichBlockButtons(buttons=row3_btns))

    # Row 4: Информация | Поддержка
    greeting = (
        texts.SUPPORT_GREETING_TEMPLATE.format(telegram_id=telegram_id)
        if telegram_id
        else None
    )
    support_url = get_support_url(text=greeting, username=support_username)
    btn_info = RichMessageButton(
        text=texts.BTN_HUB_INFO,
        callback_data="menu_info",
    )
    btn_support = RichMessageButton(
        text=texts.BTN_SUPPORT,
        url=support_url,
    )
    row4 = InputRichBlockButtons(buttons=[btn_info, btn_support])
    blocks.append(row4)

    return InputRichMessage(blocks=blocks)


def get_back_rich_block(
    callback_data: str = "back_to_main_menu",
    text: str | None = None,
) -> InputRichBlockButtons:
    """Standard back Rich Button for single-screen hub navigation."""
    btn_text = text or (
        texts.BTN_MAIN_MENU_NAV if callback_data == "back_to_main_menu" else texts.BTN_BACK
    )
    return InputRichBlockButtons(
        buttons=[RichMessageButton(text=btn_text, callback_data=callback_data)]
    )
