import logging
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import Chat, Message
from aiogram.types import User as TelegramUser

from bot.handlers.white_internet import _get_effective_tariff_info
from bot.middlewares.clean_chat import CleanChatMiddleware


class TestWhiteInternetTariffFallbackIsLoud(unittest.IsolatedAsyncioTestCase):
    """A failed tariff read must never silently fall back to the env price."""

    async def test_db_failure_logs_error_and_returns_env_defaults(self):
        session = AsyncMock()
        with patch(
            "bot.handlers.white_internet.WhiteInternetService.get_or_create_white_internet_tariff",
            new=AsyncMock(side_effect=RuntimeError("db is down")),
        ), self.assertLogs(
            "bot.handlers.white_internet", level=logging.ERROR
        ) as captured:
            base_price, base_price_int, duration_days, quota = (
                await _get_effective_tariff_info(session)
            )

        self.assertTrue(
            any("falling back to env defaults" in line for line in captured.output),
            captured.output,
        )
        self.assertIsInstance(base_price, Decimal)
        self.assertEqual(base_price_int, int(base_price))
        self.assertGreater(duration_days, 0)
        self.assertGreater(quota, 0)

    async def test_successful_read_does_not_log_an_error(self):
        tariff = MagicMock(duration_days=30)
        version = MagicMock(price_rub=Decimal("555"), base_quota_bytes=1024)
        with patch(
            "bot.handlers.white_internet.WhiteInternetService.get_or_create_white_internet_tariff",
            new=AsyncMock(return_value=tariff),
        ), patch(
            "bot.handlers.white_internet.get_or_create_current_version",
            new=AsyncMock(return_value=version),
        ):
            with self.assertNoLogs("bot.handlers.white_internet", level=logging.ERROR):
                base_price, base_price_int, duration_days, quota = (
                    await _get_effective_tariff_info(AsyncMock())
                )

        self.assertEqual(base_price, Decimal("555"))
        self.assertEqual(base_price_int, 555)
        self.assertEqual(duration_days, 30)
        self.assertEqual(quota, 1024)


class TestCleanChatFailsOpenOnFsmError(unittest.IsolatedAsyncioTestCase):
    """An unreachable FSM store must not delete the user's message."""

    def _message(self):
        message = MagicMock(spec=Message)
        message.pinned_message = None
        message.new_chat_members = None
        message.left_chat_member = None
        message.new_chat_title = None
        message.new_chat_photo = None
        message.delete_chat_photo = None
        message.group_chat_created = None
        message.supergroup_chat_created = None
        message.channel_chat_created = None
        message.migrate_to_chat_id = None
        message.migrate_from_chat_id = None
        message.message_id = 4242
        message.chat = MagicMock(spec=Chat)
        message.chat.id = 99
        return message

    async def test_fsm_error_delegates_to_handler_without_deleting(self):
        middleware = CleanChatMiddleware()
        handler = AsyncMock(return_value="handled")

        state = MagicMock()
        state.get_state = AsyncMock(side_effect=RuntimeError("redis down"))

        message = self._message()
        with patch("bot.middlewares.clean_chat._ensure_worker_started") as ensure, \
             patch("bot.middlewares.clean_chat.logger") as log:
            result = await middleware(handler, message, {"state": state})

        self.assertEqual(result, "handled")
        handler.assert_awaited_once()
        ensure.assert_not_called()
        self.assertTrue(
            any(
                call.args
                and "leaving message" in str(call.args[0])
                for call in log.warning.call_args_list
            ),
            log.warning.call_args_list,
        )

    async def test_active_state_still_short_circuits(self):
        middleware = CleanChatMiddleware()
        handler = AsyncMock(return_value="handled")

        state = MagicMock()
        state.get_state = AsyncMock(return_value="SomeState")

        with patch("bot.middlewares.clean_chat._ensure_worker_started") as ensure:
            result = await middleware(handler, self._message(), {"state": state})

        self.assertEqual(result, "handled")
        ensure.assert_not_called()

    async def test_no_state_still_queues_deletion(self):
        middleware = CleanChatMiddleware()
        handler = AsyncMock(return_value="handled")

        queue = MagicMock()
        message = self._message()
        message.bot = MagicMock()
        with patch("bot.middlewares.clean_chat._ensure_worker_started"), \
             patch("bot.middlewares.clean_chat._delete_queue", queue):
            result = await middleware(handler, message, {})

        self.assertEqual(result, "handled")
        queue.put_nowait.assert_called_once_with(
            (message.bot, message.chat.id, message.message_id)
        )


if __name__ == "__main__":
    unittest.main()
