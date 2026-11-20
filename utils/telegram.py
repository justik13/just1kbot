import asyncio
import html
import logging
import re
from typing import Any

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import (
    CallbackQuery,
    DisabledButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
)

from utils.text_limits import split_text_by_lines

logger = logging.getLogger(__name__)

# ==============================================================================
# Telegram Message Effect IDs (Telegram Bot API 7.4+)
# Verified via live API scan (696 tested against Telegram servers, exactly 6 supported):
# ==============================================================================
EFFECT_LIKE = "5107584321108051014"        # 👍 Like / Thumbs Up (system confirmations, rename)
EFFECT_FIRE = "5104841245755180586"        # 🔥 Fire (device creation, referral rewards)
EFFECT_CONFETTI = "5046509860389126442"    # 🎉 Confetti (balance topup, plan purchase, renewal)


def safe(value: str | None) -> str:
    if value is None:
        return "—"
    return html.escape(str(value))


async def _send_with_resilience(op, *, chat_id: int, context: str = "send"):
    """
    Execute a Telegram send operation with duplicate-safe retry semantics.

    - TelegramRetryAfter: the request was REJECTED (not delivered) and the
      server told us exactly when to retry -> wait and retry once more.
    - TelegramNetworkError / ambiguous timeout: delivery state is UNKNOWN
      (the request may have been processed before the connection broke).
      Automatic resending would risk duplicate messages, so we log a
      marker and re-raise without resending.
    """
    try:
        return await op()
    except TelegramRetryAfter as e:
        await asyncio.sleep(float(e.retry_after) + 0.5)
        return await op()
    except TelegramNetworkError as exc:
        logger.warning(
            "telegram_send_uncertain chat=%s context=%s: network error, not resending: %s",
            chat_id,
            context,
            exc,
        )
        raise
    except asyncio.TimeoutError:
        logger.warning(
            "telegram_send_uncertain chat=%s context=%s: ambiguous timeout, not resending",
            chat_id,
            context,
        )
        raise


def _is_message_effect_error(exc: Exception) -> bool:
    """Return True if TelegramBadRequest was caused by an unsupported or invalid message_effect_id."""
    err = str(exc).lower()
    return any(
        marker in err
        for marker in (
            "message_effect_id_invalid",
            "effect_id_invalid",
            "effect_chat_invalid",
            "message_effect_invalid",
            "message effect invalid",
            "message effect id is invalid",
            "can't be sent with effect",
        )
    )


def sanitize_reply_markup(reply_markup: InlineKeyboardMarkup | None) -> InlineKeyboardMarkup | None:
    """Validate and sanitize an InlineKeyboardMarkup to strictly satisfy Telegram Bot API constraints.

    Guarantees:
    - copy_text: Telegram Bot API limits text to 1..256 characters. If text length is outside [1, 256]
      (e.g., oversized links/keys of 800+ chars, empty strings), copy_text is dropped and safely replaced
      with callback_data="alert:copy_too_long" to inform the user explicitly via popup alert instead of
      failing silently or causing TelegramBadRequest: BUTTON_COPY_TEXT_INVALID.
    - callback_data: Telegram Bot API limits callback_data to 1..64 bytes (UTF-8). If exceeded, neutralized
      to 'ignore' to prevent TelegramBadRequest: BUTTON_DATA_INVALID.
    - text: button label must not be empty (defaults to '—' if whitespace/empty).
    - row width: Telegram Bot API limits each row to max 8 buttons. Rows > 8 buttons are automatically partitioned.
    - total rows: Telegram Bot API limits keyboards to max 100 buttons. Excess buttons are capped.
    """
    if reply_markup is None or not getattr(reply_markup, "inline_keyboard", None):
        return reply_markup

    sanitized_rows: list[list[InlineKeyboardButton]] = []
    has_changes = False

    for row in reply_markup.inline_keyboard:
        sanitized_row: list[InlineKeyboardButton] = []
        for btn in row:
            updates: dict[str, Any] = {}

            # 1. Text label must not be empty
            btn_text = getattr(btn, "text", "") or ""
            if not btn_text.strip():
                updates["text"] = "—"

            # 2. Check disabled button: strip redundant callback_data / copy_text if button is disabled
            is_disabled = isinstance(getattr(btn, "disabled", None), DisabledButton)

            if is_disabled:
                if getattr(btn, "callback_data", None) is not None:
                    updates["callback_data"] = None
                if getattr(btn, "copy_text", None) is not None:
                    updates["copy_text"] = None

            # 3. Check copy_text: Telegram Bot API specifies 1 <= len(text) <= 256
            if getattr(btn, "copy_text", None) is not None and "copy_text" not in updates:
                cp_text = getattr(btn.copy_text, "text", "") or ""
                if not (1 <= len(cp_text) <= 256):
                    updates["copy_text"] = None
                    if not (
                        btn.url
                        or btn.web_app
                        or btn.login_url
                        or btn.switch_inline_query
                        or btn.switch_inline_query_current_chat
                        or btn.switch_inline_query_chosen_chat
                        or btn.callback_game
                        or btn.pay
                        or is_disabled
                    ):
                        updates["callback_data"] = "alert:copy_too_long"
                    logger.error(
                        "BUTTON_COPY_TEXT_OVERFLOW: Sanitized invalid copy_text in button '%s' (len=%s, must be 1..256)",
                        btn_text,
                        len(cp_text),
                    )

            # 4. Check callback_data: Telegram Bot API specifies 1 <= len(utf-8 bytes) <= 64
            if getattr(btn, "callback_data", None) is not None and "callback_data" not in updates:
                cb_str = str(btn.callback_data)
                cb_bytes = cb_str.encode("utf-8")
                if len(cb_bytes) > 64:
                    updates["callback_data"] = "ignore"
                    logger.critical(
                        "BUTTON_CALLBACK_OVERFLOW: callback_data in button '%s' exceeds 64 bytes (%s bytes: '%s'). Neutralized to 'ignore' to prevent route corruption.",
                        btn_text,
                        len(cb_bytes),
                        cb_str[:32] + "...",
                    )

            if updates:
                has_changes = True
                sanitized_btn = btn.model_copy(update=updates) if hasattr(btn, "model_copy") else btn
                sanitized_row.append(sanitized_btn)
            else:
                sanitized_row.append(btn)

        # Check row width: Partition rows exceeding 8 buttons to maintain clean Telegram UI rendering
        if len(sanitized_row) > 8:
            has_changes = True
            for i in range(0, len(sanitized_row), 8):
                sanitized_rows.append(sanitized_row[i : i + 8])
        else:
            sanitized_rows.append(sanitized_row)

    # Check total buttons: cap at 100 buttons to satisfy Telegram Bot API maximum
    total_buttons = sum(len(row) for row in sanitized_rows)
    if total_buttons > 100:
        has_changes = True
        logger.error(
            "KEYBOARD_BUTTON_OVERFLOW: Sanitized keyboard has %s buttons (capped at 100)",
            total_buttons,
        )
        capped_rows: list[list[InlineKeyboardButton]] = []
        buttons_kept = 0
        for row in sanitized_rows:
            if buttons_kept + len(row) <= 100:
                capped_rows.append(row)
                buttons_kept += len(row)
            else:
                remaining = 100 - buttons_kept
                if remaining > 0:
                    capped_rows.append(row[:remaining])
                break
        sanitized_rows = capped_rows

    if not has_changes:
        return reply_markup

    return InlineKeyboardMarkup(inline_keyboard=sanitized_rows)


def strip_html_tags(text: str) -> str:
    """Convert HTML-formatted text to clean plain text for fallback delivery."""
    text = re.sub(
        r'<a\s+[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        r"\2 (\1)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p(?:\s+[^>]*?)?>", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"</?(?:b|strong|i|em|u|ins|s|strike|del|span|tg-spoiler|tg-emoji|tg-time|code|pre|blockquote|expandable-blockquote)(?:\s+[^>]*?)?>",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return html.unescape(text)


def _is_parse_error(exc: Exception) -> bool:
    """Return True if TelegramBadRequest was caused by HTML or Markdown entity parsing failure."""
    err = str(exc).lower()
    return "can't parse" in err or "parse entities" in err or "parse error" in err


async def safe_send_message(
    bot,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "HTML",
    disable_web_page_preview: bool = True,
    message_effect_id: str | None = None,
    raise_forbidden: bool = False,
) -> int | None:
    """Send a Telegram message with resilience and automatic plain-text fallback on parse errors."""
    link_preview_opts = LinkPreviewOptions(is_disabled=True) if disable_web_page_preview else None
    reply_markup = sanitize_reply_markup(reply_markup)
    text_parts = split_text_by_lines(text, limit=4096) or ["—"]
    sent_id = None

    try:
        for index, part in enumerate(text_parts):
            is_last = index == len(text_parts) - 1
            markup = reply_markup if is_last else None
            effect = message_effect_id if index == 0 else None

            async def _do_send(
                p_text: str,
                p_mode: str | None,
                p_eff: str | None,
                p_markup: InlineKeyboardMarkup | None = markup,
            ):
                return await _send_with_resilience(
                    lambda: bot.send_message(
                        chat_id=chat_id,
                        text=p_text,
                        reply_markup=p_markup,
                        parse_mode=p_mode,
                        link_preview_options=link_preview_opts,
                        message_effect_id=p_eff,
                    ),
                    chat_id=chat_id,
                    context="safe_send_message",
                )

            def _extract_msg_id(m) -> int | None:
                if m is None or isinstance(m, bool):
                    return None
                if isinstance(m, int):
                    return m
                mid = getattr(m, "message_id", None)
                if isinstance(mid, int) and not isinstance(mid, bool):
                    return mid
                return None

            try:
                msg = await _do_send(part, parse_mode, effect, markup)
                sent_id = _extract_msg_id(msg)
            except TelegramBadRequest as exc:
                if effect and _is_message_effect_error(exc):
                    try:
                        msg = await _do_send(part, parse_mode, None, markup)
                        sent_id = _extract_msg_id(msg)
                    except TelegramBadRequest as inner_exc:
                        if _is_parse_error(inner_exc):
                            logger.warning(
                                "Parse error in safe_send_message retry for chat %s; falling back to plain text",
                                chat_id,
                            )
                            plain = (strip_html_tags(part) if parse_mode == "HTML" else part)[:4096]
                            msg = await _do_send(plain, None, None, markup)
                            sent_id = _extract_msg_id(msg)
                        else:
                            raise
                elif _is_parse_error(exc):
                    logger.warning(
                        "Parse error in safe_send_message for chat %s; falling back to plain text",
                        chat_id,
                    )
                    plain = (strip_html_tags(part) if parse_mode == "HTML" else part)[:4096]
                    try:
                        msg = await _do_send(plain, None, effect, markup)
                        sent_id = _extract_msg_id(msg)
                    except TelegramBadRequest as effect_exc:
                        if effect and _is_message_effect_error(effect_exc):
                            msg = await _do_send(plain, None, None, markup)
                            sent_id = _extract_msg_id(msg)
                        else:
                            raise
                else:
                    raise
    except TelegramForbiddenError:
        logger.info("Cannot send message: bot was blocked by chat %s", chat_id)
        if raise_forbidden:
            raise
        return None

    return sent_id


async def safe_callback_answer(
    callback: CallbackQuery | Any,
    text: str | None = None,
    *,
    show_alert: bool = False,
    url: str | None = None,
    cache_time: int = 0,
) -> bool:
    """Safely answer a Telegram CallbackQuery without crashing on expired or duplicate queries."""
    if callback is None:
        return False
    try:
        kwargs: dict[str, Any] = {}
        if show_alert:
            kwargs["show_alert"] = show_alert
        if url is not None:
            kwargs["url"] = url
        if cache_time:
            kwargs["cache_time"] = cache_time
        if text is not None:
            await callback.answer(text, **kwargs)
        else:
            await callback.answer(**kwargs)
        return True
    except TelegramBadRequest as exc:
        err = str(exc).lower()
        if (
            "query is too old" in err
            or "query id is invalid" in err
            or "query is already answered" in err
        ):
            logger.debug("safe_callback_answer ignored expected error: %s", exc)
            return False
        logger.warning("safe_callback_answer TelegramBadRequest: %s", exc)
        return False
    except Exception as exc:
        logger.warning("safe_callback_answer failed: %s", exc)
        return False


async def resolve_bot_username(bot: Any = None) -> str | None:
    """Resolve the Telegram bot username without unnecessary network calls."""
    try:
        from config.settings import get_settings
        username = (get_settings().BOT_USERNAME or "").lstrip("@")
        if username:
            return username
    except Exception:
        pass

    if bot is None:
        return None

    me = getattr(bot, "_me", None)
    if me and getattr(me, "username", None):
        return me.username

    bot_me = getattr(bot, "me", None)
    if bot_me is not None:
        try:
            if callable(bot_me):
                res = await bot_me()
                if res and getattr(res, "username", None):
                    return res.username
            elif getattr(bot_me, "username", None):
                return bot_me.username
        except Exception:
            pass

    get_me = getattr(bot, "get_me", None)
    if callable(get_me):
        try:
            res = await get_me()
            if res and getattr(res, "username", None):
                return res.username
        except Exception:
            pass

    return None
