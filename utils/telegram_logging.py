"""Centralized Telegram Error Log Handler for operator alerting."""

from __future__ import annotations

import asyncio
import html
import logging
import os
import time
import traceback
from collections import deque
from typing import TYPE_CHECKING

from bot.texts.runtime.alerts import (
    ALERT_LOG_REQUEST_ID_LINE,
    ALERT_LOG_TRACEBACK_BLOCK,
    ALERT_SYSTEM_ERROR_LOG,
)
from utils.logging_security import sanitize_text

if TYPE_CHECKING:
    from aiogram import Bot

_IGNORED_LOGGER_PREFIXES: tuple[str, ...] = (
    "aiogram",
    "aiohttp",
    "utils.telegram",
    "utils.telegram_logging",
)


class TelegramErrorLogHandler(logging.Handler):
    """Logging handler that dispatches ERROR and CRITICAL logs to admin Telegram chats."""

    _bot: Bot | None = None
    _installed_instance: TelegramErrorLogHandler | None = None

    def __init__(
        self,
        level: int = logging.ERROR,
        *,
        debounce_ttl: float = 600.0,
        burst_limit: int = 5,
        burst_window: float = 60.0,
    ) -> None:
        super().__init__(level=level)
        self._debounce_ttl = debounce_ttl
        self._burst_limit = burst_limit
        self._burst_window = burst_window
        self._recent_signatures: dict[tuple[str, str, str], float] = {}
        self._dispatch_timestamps: deque[float] = deque()
        self._background_tasks: set[asyncio.Task] = set()
        self._force_active = False

    @classmethod
    def set_bot(cls, bot: Bot | None) -> None:
        cls._bot = bot

    @classmethod
    def get_bot(cls) -> Bot | None:
        return cls._bot

    def clear_cache(self) -> None:
        self._recent_signatures.clear()
        self._dispatch_timestamps.clear()

    def _is_ci_mode(self) -> bool:
        if self._force_active:
            return False
        return os.getenv("CI_TEST_MODE", "false").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def _should_suppress_signature(
        self,
        signature: tuple[str, str, str],
        now: float,
    ) -> bool:
        # Prune expired entries
        expired_keys = [
            k
            for k, last_seen in self._recent_signatures.items()
            if now - last_seen > self._debounce_ttl
        ]
        for k in expired_keys:
            self._recent_signatures.pop(k, None)

        if signature in self._recent_signatures:
            return True

        self._recent_signatures[signature] = now
        return False

    def _is_rate_limited(self, now: float) -> bool:
        while (
            self._dispatch_timestamps
            and now - self._dispatch_timestamps[0] > self._burst_window
        ):
            self._dispatch_timestamps.popleft()

        if len(self._dispatch_timestamps) >= self._burst_limit:
            return True

        self._dispatch_timestamps.append(now)
        return False

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < self.level:
            return

        bot = self.get_bot()
        if bot is None or self._is_ci_mode():
            return

        # Anti-recursion: drop logs from telegram, http client and this logger
        name = record.name or ""
        if any(name.startswith(prefix) for prefix in _IGNORED_LOGGER_PREFIXES):
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        if loop.is_closed():
            return

        now = time.monotonic()
        msg_raw = str(record.msg or "")[:100]
        sig = (name, record.pathname or "", msg_raw)

        if self._should_suppress_signature(sig, now):
            return

        if self._is_rate_limited(now):
            return

        try:
            alert_text = self._format_alert(record)
        except Exception:
            return

        task = loop.create_task(self._async_send(bot, alert_text))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _format_alert(self, record: logging.LogRecord) -> str:
        level_str = record.levelname or "ERROR"
        module_name = html.escape(record.name or "unknown")
        filename = (
            os.path.basename(record.pathname)
            if record.pathname
            else "unknown"
        )
        location = f"{html.escape(filename)}:{record.lineno}"

        request_id = getattr(record, "request_id", None)
        if request_id and str(request_id).strip() and str(request_id) != "—":
            req_escaped = html.escape(str(request_id).strip())
            request_id_block = ALERT_LOG_REQUEST_ID_LINE.format(
                request_id=req_escaped
            )
        else:
            request_id_block = ""

        try:
            raw_message = record.getMessage()
        except Exception:
            raw_message = str(record.msg)

        sanitized_msg = sanitize_text(raw_message)
        if len(sanitized_msg) > 500:
            sanitized_msg = sanitized_msg[:500] + "..."
        msg_escaped = html.escape(sanitized_msg)

        traceback_block = ""
        if record.exc_info and record.exc_info[0]:
            try:
                tb_lines = traceback.format_exception(*record.exc_info)
                tb_text = "".join(tb_lines)
                tb_sanitized = sanitize_text(tb_text)
                if len(tb_sanitized) > 1000:
                    tb_sanitized = (
                        tb_sanitized[:300]
                        + "\n...[truncated]...\n"
                        + tb_sanitized[-700:]
                    )
                tb_escaped = html.escape(tb_sanitized)
                traceback_block = ALERT_LOG_TRACEBACK_BLOCK.format(
                    traceback=tb_escaped
                )
            except Exception:
                traceback_block = ""

        return ALERT_SYSTEM_ERROR_LOG.format(
            level=level_str,
            module=module_name,
            location=location,
            request_id_block=request_id_block,
            message=msg_escaped,
            traceback_block=traceback_block,
        )

    async def _async_send(self, bot: Bot, text: str) -> None:
        try:
            from config.settings import get_settings
            from utils.telegram import safe_send_message

            settings = get_settings()
            admin_ids = getattr(settings, "ADMIN_IDS", ())
            if not admin_ids:
                return

            for admin_id in admin_ids:
                try:
                    await safe_send_message(
                        bot,
                        chat_id=admin_id,
                        text=text,
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
        except Exception:
            pass


def install_telegram_error_logger(
    target_logger: logging.Logger | None = None,
    *,
    bot: Bot | None = None,
    debounce_ttl: float = 600.0,
    burst_limit: int = 5,
    burst_window: float = 60.0,
) -> TelegramErrorLogHandler:
    """Install the Telegram error log handler onto the target (default: root) logger."""
    logger_to_attach = (
        logging.getLogger() if target_logger is None else target_logger
    )

    for existing in logger_to_attach.handlers:
        if isinstance(existing, TelegramErrorLogHandler):
            if bot is not None:
                existing.set_bot(bot)
            return existing

    handler = TelegramErrorLogHandler(
        debounce_ttl=debounce_ttl,
        burst_limit=burst_limit,
        burst_window=burst_window,
    )
    if bot is not None:
        handler.set_bot(bot)

    logger_to_attach.addHandler(handler)
    TelegramErrorLogHandler._installed_instance = handler
    return handler
