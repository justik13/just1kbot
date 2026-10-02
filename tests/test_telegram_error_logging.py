"""Tests for centralized Telegram error log alerting handler."""

from __future__ import annotations

import asyncio
import logging
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from utils.telegram_logging import (
    TelegramErrorLogHandler,
    install_telegram_error_logger,
)


class TestTelegramErrorLogHandler(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.handler = TelegramErrorLogHandler(
            level=logging.ERROR,
            debounce_ttl=300.0,
            burst_limit=5,
            burst_window=60.0,
        )
        self.handler._force_active = True
        self.test_logger = logging.getLogger(f"test.error.logger.{id(self)}")
        self.test_logger.setLevel(logging.DEBUG)
        self.test_logger.addHandler(self.handler)
        self.fake_bot = MagicMock()
        TelegramErrorLogHandler.set_bot(self.fake_bot)

    def tearDown(self):
        self.test_logger.removeHandler(self.handler)
        self.handler.clear_cache()
        TelegramErrorLogHandler.set_bot(None)

    async def test_logger_error_triggers_telegram_alert_to_admins(self):
        with patch("config.settings.get_settings") as mock_settings, patch(
            "utils.telegram.safe_send_message", new_callable=AsyncMock
        ) as mock_send:
            mock_settings.return_value = MagicMock(ADMIN_IDS=[111, 222])

            self.test_logger.error("Node probe failed for target: %s", "srv-1")
            # Allow background asyncio task to execute
            await asyncio.sleep(0.01)

            self.assertEqual(mock_send.await_count, 2)
            first_kwargs = mock_send.await_args_list[0].kwargs
            self.assertEqual(first_kwargs["chat_id"], 111)
            text = first_kwargs["text"]
            self.assertIn("Ошибка в логах системы", text)
            self.assertIn("ERROR", text)
            self.assertIn("Node probe failed for target: srv-1", text)
            self.assertIn(self.test_logger.name, text)

    async def test_deduplication_suppresses_rapid_duplicate_errors(self):
        with patch("config.settings.get_settings") as mock_settings, patch(
            "utils.telegram.safe_send_message", new_callable=AsyncMock
        ) as mock_send:
            mock_settings.return_value = MagicMock(ADMIN_IDS=[111])

            # Call 1: triggers alert
            self.test_logger.error("Repeated database timeout error")
            await asyncio.sleep(0.01)
            self.assertEqual(mock_send.await_count, 1)

            # Call 2: same signature, must be suppressed
            self.test_logger.error("Repeated database timeout error")
            await asyncio.sleep(0.01)
            self.assertEqual(mock_send.await_count, 1)

    async def test_anti_recursion_ignores_telegram_and_aiogram_loggers(self):
        aiogram_logger = logging.getLogger("aiogram.event")
        aiogram_logger.setLevel(logging.DEBUG)
        aiogram_logger.addHandler(self.handler)

        try:
            with patch("config.settings.get_settings") as mock_settings, patch(
                "utils.telegram.safe_send_message", new_callable=AsyncMock
            ) as mock_send:
                mock_settings.return_value = MagicMock(ADMIN_IDS=[111])

                aiogram_logger.error("Telegram network failure 502")
                await asyncio.sleep(0.01)

                mock_send.assert_not_called()
        finally:
            aiogram_logger.removeHandler(self.handler)

    async def test_sensitive_credentials_are_masked(self):
        with patch("config.settings.get_settings") as mock_settings, patch(
            "utils.telegram.safe_send_message", new_callable=AsyncMock
        ) as mock_send:
            mock_settings.return_value = MagicMock(ADMIN_IDS=[111])

            secret_key = "test_12345678901234567890123456"
            self.test_logger.error("Failed with secret: api_key=%s", secret_key)
            await asyncio.sleep(0.01)

            self.assertEqual(mock_send.await_count, 1)
            text = mock_send.await_args_list[0].kwargs["text"]
            self.assertNotIn(secret_key, text)
            self.assertIn("[REDACTED]", text)

    async def test_traceback_is_formatted_and_sanitized(self):
        with patch("config.settings.get_settings") as mock_settings, patch(
            "utils.telegram.safe_send_message", new_callable=AsyncMock
        ) as mock_send:
            mock_settings.return_value = MagicMock(ADMIN_IDS=[111])

            try:
                raise ValueError("secret_internal_diagnostic_state")
            except ValueError:
                self.test_logger.exception("Unexpected calculation crash")

            await asyncio.sleep(0.01)

            self.assertEqual(mock_send.await_count, 1)
            text = mock_send.await_args_list[0].kwargs["text"]
            self.assertIn("Стек вызова", text)
            self.assertIn("ValueError", text)
            self.assertIn("Unexpected calculation crash", text)

    async def test_warnings_and_info_are_ignored(self):
        with patch("config.settings.get_settings") as mock_settings, patch(
            "utils.telegram.safe_send_message", new_callable=AsyncMock
        ) as mock_send:
            mock_settings.return_value = MagicMock(ADMIN_IDS=[111])

            self.test_logger.warning("Normal network retry warning")
            self.test_logger.info("Informational event")
            await asyncio.sleep(0.01)

            mock_send.assert_not_called()

    async def test_noop_when_bot_is_none(self):
        TelegramErrorLogHandler.set_bot(None)
        with patch("utils.telegram.safe_send_message", new_callable=AsyncMock) as mock_send:
            self.test_logger.error("Error without configured bot")
            await asyncio.sleep(0.01)

            mock_send.assert_not_called()

    async def test_install_telegram_error_logger_idempotent(self):
        root = logging.getLogger(f"test.root.{id(self)}")
        h1 = install_telegram_error_logger(root, bot=self.fake_bot)
        h2 = install_telegram_error_logger(root, bot=self.fake_bot)

        self.assertIs(h1, h2)
        handlers_count = sum(isinstance(h, TelegramErrorLogHandler) for h in root.handlers)
        self.assertEqual(handlers_count, 1)
        root.removeHandler(h1)
