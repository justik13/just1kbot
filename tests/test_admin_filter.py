"""Unit tests for centralized AdminFilter."""

import unittest
from unittest.mock import MagicMock, patch

from aiogram.types import CallbackQuery, Message

from bot.filters import AdminFilter


class TestAdminFilter(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.filter = AdminFilter()

    async def test_admin_message_allowed(self):
        msg = MagicMock(spec=Message)
        msg.from_user = MagicMock(id=12345)

        with patch("bot.filters.is_admin", return_value=True) as mock_is_admin:
            result = await self.filter(msg)
            self.assertTrue(result)
            mock_is_admin.assert_called_once_with(12345)

    async def test_non_admin_message_rejected(self):
        msg = MagicMock(spec=Message)
        msg.from_user = MagicMock(id=99999)

        with patch("bot.filters.is_admin", return_value=False) as mock_is_admin:
            result = await self.filter(msg)
            self.assertFalse(result)
            mock_is_admin.assert_called_once_with(99999)

    async def test_admin_callback_allowed(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = MagicMock(id=12345)

        with patch("bot.filters.is_admin", return_value=True) as mock_is_admin:
            result = await self.filter(cb)
            self.assertTrue(result)
            mock_is_admin.assert_called_once_with(12345)

    async def test_non_admin_callback_rejected(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = MagicMock(id=99999)

        with patch("bot.filters.is_admin", return_value=False) as mock_is_admin:
            result = await self.filter(cb)
            self.assertFalse(result)
            mock_is_admin.assert_called_once_with(99999)

    async def test_event_without_user_rejected(self):
        event = MagicMock(spec=Message)
        event.from_user = None

        result = await self.filter(event)
        self.assertFalse(result)

    async def test_unsupported_event_type_rejected(self):
        class OtherEvent:
            pass

        result = await self.filter(OtherEvent())
        self.assertFalse(result)
