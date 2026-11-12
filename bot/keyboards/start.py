"""Keyboards and Rich UI builders for Mini App entry point."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputRichBlockButtons,
    InputRichBlockParagraph,
    InputRichMessage,
    RichMessageButton,
    WebAppInfo,
)

from bot import texts


def build_start_rich_message(name: str, miniapp_url: str) -> InputRichMessage:
    """Build single-screen Rich UI hub entry with native Mini App Connect button."""
    greeting_text = texts.START_HUB_MESSAGE.format(name=name)
    return InputRichMessage(
        blocks=[
            InputRichBlockParagraph(text=greeting_text),
            InputRichBlockButtons(
                buttons=[
                    RichMessageButton(
                        text=texts.BTN_MINIAPP_CONNECT,
                        web_app=WebAppInfo(url=miniapp_url),
                    )
                ]
            ),
        ]
    )


def build_start_inline_keyboard(miniapp_url: str) -> InlineKeyboardMarkup:
    """Fallback inline keyboard with Mini App Connect button."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_MINIAPP_CONNECT,
                    web_app=WebAppInfo(url=miniapp_url),
                )
            ]
        ]
    )
