"""Unit tests for INCY admin configuration routes in Telegram bot."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, User

from bot import texts
from bot.handlers.admin.servers.incy_routes import (
    _get_server_incy_details,
    process_server_incy_param_input,
    process_server_incy_relay_badge_input,
    process_server_incy_relay_name_input,
    reset_server_incy_to_defaults,
    show_server_incy_card,
    show_server_incy_relay_card,
    show_server_incy_relays,
    start_edit_relay_specific_name,
    start_edit_server_incy_param,
    toggle_server_incy_origin_visibility,
    toggle_server_incy_relay_visibility,
)
from bot.states import AdminStates
from config.constants import XRAY_PROTOCOL
from database.models import Server


class TestAdminServerIncyRoutes(unittest.IsolatedAsyncioTestCase):
    """Tests for the INCY settings admin UI in bot."""

    async def asyncSetUp(self):
        self.storage = MemoryStorage()
        self.admin_user = User(id=111, is_bot=False, first_name="Admin")
        self.chat = Chat(id=111, type="private")
        self.key = StorageKey(bot_id=1, chat_id=111, user_id=111)
        self.state = FSMContext(storage=self.storage, key=self.key)
        self.mock_session = AsyncMock()

    async def test_show_server_incy_card_unauthorized(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = User(id=999, is_bot=False, first_name="Hacker")
        cb.data = "admin_server_incy:1"
        cb.answer = AsyncMock()

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=False):
            await show_server_incy_card(cb, self.state, self.mock_session)
            cb.answer.assert_awaited_once()

    async def test_show_server_incy_card_success(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = self.admin_user
        cb.data = "admin_server_incy:1"
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.edit_text = AsyncMock()

        server = Server(
            id=1,
            name="Origin-RU",
            protocol=XRAY_PROTOCOL,
            is_active=True,
            extra_data={"profile_title": "✦ Custom Title"},
        )

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.get_server_by_id", return_value=server):
                await show_server_incy_card(cb, self.state, self.mock_session)
                cb.message.edit_text.assert_awaited_once()
                call_args = cb.message.edit_text.call_args[0][0]
                self.assertIn("✦ Custom Title", call_args)

    async def test_start_edit_server_incy_param_sets_state(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = self.admin_user
        cb.data = "admin_server_incy_edit:1:title"
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.edit_text = AsyncMock()

        server = Server(
            id=1,
            name="Origin-RU",
            protocol=XRAY_PROTOCOL,
            is_active=True,
            extra_data={},
        )

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.get_server_by_id", return_value=server):
                await start_edit_server_incy_param(cb, self.state, self.mock_session)
                current_state = await self.state.get_state()
                self.assertEqual(current_state, AdminStates.editing_server_incy_param)
                data = await self.state.get_data()
                self.assertEqual(data["server_id"], 1)
                self.assertEqual(data["incy_param"], "title")

    async def test_process_server_incy_param_input_saves_value(self):
        await self.state.set_state(AdminStates.editing_server_incy_param)
        await self.state.update_data(server_id=1, incy_param="title")

        msg = MagicMock(spec=Message)
        msg.from_user = self.admin_user
        msg.text = "★ Just1k Prime"
        msg.answer = AsyncMock()

        server = Server(
            id=1,
            name="Origin-RU",
            protocol=XRAY_PROTOCOL,
            is_active=True,
            extra_data={},
        )

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.get_server_by_id", return_value=server):
                with patch("bot.handlers.admin.servers.incy_routes.update_server", new_callable=AsyncMock) as mock_update:
                    await process_server_incy_param_input(msg, self.state, self.mock_session)
                    mock_update.assert_awaited_once_with(
                        self.mock_session,
                        server,
                        extra_data={"profile_title": "★ Just1k Prime"},
                    )
                    state_after = await self.state.get_state()
                    self.assertIsNone(state_after)
                    msg.answer.assert_awaited_once()

    async def test_process_server_incy_param_input_clear(self):
        await self.state.set_state(AdminStates.editing_server_incy_param)
        await self.state.update_data(server_id=1, incy_param="desc")

        msg = MagicMock(spec=Message)
        msg.from_user = self.admin_user
        msg.text = "/clear"
        msg.answer = AsyncMock()

        server = Server(
            id=1,
            name="Origin-RU",
            protocol=XRAY_PROTOCOL,
            is_active=True,
            extra_data={"profile_description": "Old desc"},
        )

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.get_server_by_id", return_value=server):
                with patch("bot.handlers.admin.servers.incy_routes.update_server", new_callable=AsyncMock) as mock_update:
                    await process_server_incy_param_input(msg, self.state, self.mock_session)
                    mock_update.assert_awaited_once_with(
                        self.mock_session,
                        server,
                        extra_data={"profile_description": ""},
                    )

    async def test_show_server_incy_relays(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = self.admin_user
        cb.data = "admin_server_incy_relays:1"
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.edit_text = AsyncMock()

        server = Server(
            id=1,
            name="Origin-RU",
            protocol=XRAY_PROTOCOL,
            is_active=True,
            extra_data={
                "relays": [{"code": "de", "name": "Германия"}],
                "relay_badges": {"de": "⚡ Быстрый"},
            },
        )

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.get_server_by_id", return_value=server):
                await show_server_incy_relays(cb, self.state, self.mock_session)
                cb.message.edit_text.assert_awaited_once()

    async def test_edit_and_save_relay_specific_badge(self):
        await self.state.set_state(AdminStates.editing_server_incy_relay_badge)
        await self.state.update_data(server_id=1, relay_code="de")

        msg = MagicMock(spec=Message)
        msg.from_user = self.admin_user
        msg.text = "⚡ YouTube БЕЗ рекламы"
        msg.answer = AsyncMock()

        server = Server(
            id=1,
            name="Origin-RU",
            protocol=XRAY_PROTOCOL,
            is_active=True,
            extra_data={"relays": [{"code": "de", "name": "Германия"}]},
        )

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.get_server_by_id", return_value=server):
                with patch("bot.handlers.admin.servers.incy_routes.update_server", new_callable=AsyncMock) as mock_update:
                    await process_server_incy_relay_badge_input(msg, self.state, self.mock_session)
                    mock_update.assert_awaited_once()
                    self.assertIs(mock_update.call_args[0][1], server)
                    saved_extra = mock_update.call_args[1]["extra_data"]
                    self.assertEqual(saved_extra["relay_badges"]["de"], "⚡ YouTube БЕЗ рекламы")

    async def test_show_server_incy_relay_card(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = self.admin_user
        cb.data = "admin_server_incy_relay_view:1:de"
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.edit_text = AsyncMock()

        server = Server(
            id=1,
            name="Origin-RU",
            protocol=XRAY_PROTOCOL,
            is_active=True,
            extra_data={
                "relays": [{"code": "de", "name": "Германия"}],
                "relay_names": {"de": "🇩🇪 Франкфурт"},
                "relay_badges": {"de": "⚡ Пинг 20ms"},
            },
        )

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.get_server_by_id", return_value=server):
                await show_server_incy_relay_card(cb, self.state, self.mock_session)
                cb.message.edit_text.assert_awaited_once()
                call_args = cb.message.edit_text.call_args[0][0]
                self.assertIn("🇩🇪 Франкфурт", call_args)
                self.assertIn("⚡ Пинг 20ms", call_args)
                self.assertIn("de", call_args)

    async def test_edit_and_save_relay_specific_name(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = self.admin_user
        cb.data = "admin_server_incy_relay_edit_name:1:de"
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.edit_text = AsyncMock()

        server = Server(
            id=1,
            name="Origin-RU",
            protocol=XRAY_PROTOCOL,
            is_active=True,
            extra_data={"relays": [{"code": "de", "name": "Германия"}]},
        )

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.get_server_by_id", return_value=server):
                await start_edit_relay_specific_name(cb, self.state, self.mock_session)
                current_state = await self.state.get_state()
                self.assertEqual(current_state, AdminStates.editing_server_incy_relay_name)
                data = await self.state.get_data()
                self.assertEqual(data["server_id"], 1)
                self.assertEqual(data["relay_code"], "de")

        msg = MagicMock(spec=Message)
        msg.from_user = self.admin_user
        msg.text = "🇩🇪 Франкфурт Скоростной"
        msg.answer = AsyncMock()

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.get_server_by_id", return_value=server):
                with patch("bot.handlers.admin.servers.incy_routes.update_server", new_callable=AsyncMock) as mock_update:
                    await process_server_incy_relay_name_input(msg, self.state, self.mock_session)
                    mock_update.assert_awaited_once()
                    self.assertIs(mock_update.call_args[0][1], server)
                    saved_extra = mock_update.call_args[1]["extra_data"]
                    self.assertEqual(saved_extra["relay_names"]["de"], "🇩🇪 Франкфурт Скоростной")

    async def test_reset_server_incy_to_defaults(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = self.admin_user
        cb.data = "admin_server_incy_reset:1"
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.edit_text = AsyncMock()

        server = Server(
            id=1,
            name="Origin-RU",
            protocol=XRAY_PROTOCOL,
            is_active=True,
            extra_data={
                "profile_title": "Custom",
                "origin_tag": "Custom Origin",
                "origin_badge": "Custom Badge",
                "relay_names": {"de": "Custom DE"},
                "relay_badges": {"de": "Custom Badge"},
                "relays": [{"code": "de", "name": "Германия"}],
            },
        )

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.get_server_by_id", return_value=server):
                with patch("bot.handlers.admin.servers.incy_routes.update_server", new_callable=AsyncMock) as mock_update:
                    await reset_server_incy_to_defaults(cb, self.state, self.mock_session)
                    mock_update.assert_awaited_once()
                    self.assertIs(mock_update.call_args[0][1], server)
                    saved_extra = mock_update.call_args[1]["extra_data"]
                    self.assertNotIn("profile_title", saved_extra)
                    self.assertNotIn("origin_tag", saved_extra)
                    self.assertNotIn("origin_badge", saved_extra)
                    self.assertNotIn("relay_names", saved_extra)
                    self.assertNotIn("relay_badges", saved_extra)
                    self.assertIn("relays", saved_extra)  # relays preserved
                    cb.answer.assert_awaited_once()

    async def test_toggle_server_incy_origin_visibility(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = self.admin_user
        cb.data = "admin_server_incy_toggle_origin:1"
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.edit_text = AsyncMock()

        server = Server(
            id=1,
            name="Origin-RU",
            protocol=XRAY_PROTOCOL,
            is_active=True,
            extra_data={"origin_hidden": False},
        )

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.get_server_by_id", return_value=server):
                with patch("bot.handlers.admin.servers.incy_routes.update_server", new_callable=AsyncMock) as mock_update:
                    await toggle_server_incy_origin_visibility(cb, self.mock_session)
                    mock_update.assert_awaited_once()
                    saved_extra = mock_update.call_args[1]["extra_data"]
                    self.assertTrue(saved_extra["origin_hidden"])

    async def test_toggle_server_incy_relay_visibility(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = self.admin_user
        cb.data = "admin_server_incy_relay_toggle:1:de"
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.edit_text = AsyncMock()

        server = Server(
            id=1,
            name="Origin-RU",
            protocol=XRAY_PROTOCOL,
            is_active=True,
            extra_data={"relays": [{"code": "de", "name": "Германия"}], "relay_hidden": []},
        )

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.get_server_by_id", return_value=server):
                with patch("bot.handlers.admin.servers.incy_routes.update_server", new_callable=AsyncMock) as mock_update:
                    await toggle_server_incy_relay_visibility(cb, self.mock_session)
                    mock_update.assert_awaited_once()
                    saved_extra = mock_update.call_args[1]["extra_data"]
                    self.assertIn("de", saved_extra["relay_hidden"])

    async def test_process_server_incy_announce_input(self):
        await self.state.set_state(AdminStates.editing_server_incy_param)
        await self.state.update_data(server_id=1, incy_param="announce")

        msg = MagicMock(spec=Message)
        msg.from_user = self.admin_user
        msg.text = "⚡ Новые скоростные узлы добавлены!"
        msg.answer = AsyncMock()

        server = Server(
            id=1,
            name="Origin-RU",
            protocol=XRAY_PROTOCOL,
            is_active=True,
            extra_data={},
        )

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.get_server_by_id", return_value=server):
                with patch("bot.handlers.admin.servers.incy_routes.update_server", new_callable=AsyncMock) as mock_update:
                    await process_server_incy_param_input(msg, self.state, self.mock_session)
                    mock_update.assert_awaited_once()
                    saved_extra = mock_update.call_args[1]["extra_data"]
                    self.assertEqual(saved_extra["announce"], "⚡ Новые скоростные узлы добавлены!")

    async def test_process_server_incy_announce_url_validation(self):
        await self.state.set_state(AdminStates.editing_server_incy_param)
        await self.state.update_data(server_id=1, incy_param="announce_url")

        server = Server(
            id=1,
            name="Origin-RU",
            protocol=XRAY_PROTOCOL,
            is_active=True,
            extra_data={},
        )

        # 1. Invalid URL schema
        msg_invalid = MagicMock(spec=Message)
        msg_invalid.from_user = self.admin_user
        msg_invalid.text = "javascript:alert(1)"
        msg_invalid.answer = AsyncMock()

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.get_server_by_id", return_value=server):
                with patch("bot.handlers.admin.servers.incy_routes.update_server", new_callable=AsyncMock) as mock_update:
                    await process_server_incy_param_input(msg_invalid, self.state, self.mock_session)
                    msg_invalid.answer.assert_awaited_once_with(texts.ADMIN_SERVER_INCY_ERR_INVALID_URL)
                    mock_update.assert_not_awaited()

        # 2. Valid URL
        msg_valid = MagicMock(spec=Message)
        msg_valid.from_user = self.admin_user
        msg_valid.text = "https://t.me/just1k_channel"
        msg_valid.answer = AsyncMock()

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.get_server_by_id", return_value=server):
                with patch("bot.handlers.admin.servers.incy_routes.update_server", new_callable=AsyncMock) as mock_update:
                    await process_server_incy_param_input(msg_valid, self.state, self.mock_session)
                    mock_update.assert_awaited_once()
                    saved_extra = mock_update.call_args[1]["extra_data"]
                    self.assertEqual(saved_extra["announce_url"], "https://t.me/just1k_channel")

    async def test_process_server_incy_title_truncates_to_25_chars(self):
        await self.state.set_state(AdminStates.editing_server_incy_param)
        await self.state.update_data(server_id=1, incy_param="title")

        msg = MagicMock(spec=Message)
        msg.from_user = self.admin_user
        msg.text = "A" * 40
        msg.answer = AsyncMock()

        server = Server(
            id=1,
            name="Origin-RU",
            protocol=XRAY_PROTOCOL,
            is_active=True,
            extra_data={},
        )

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.get_server_by_id", return_value=server):
                with patch("bot.handlers.admin.servers.incy_routes.update_server", new_callable=AsyncMock) as mock_update:
                    await process_server_incy_param_input(msg, self.state, self.mock_session)
                    mock_update.assert_awaited_once()
                    saved_extra = mock_update.call_args[1]["extra_data"]
                    self.assertEqual(len(saved_extra["profile_title"]), 25)
                    self.assertEqual(saved_extra["profile_title"], "A" * 25)

    async def test_process_server_incy_origin_badge_none_disables(self):
        await self.state.set_state(AdminStates.editing_server_incy_param)
        await self.state.update_data(server_id=1, incy_param="origin_badge")

        msg = MagicMock(spec=Message)
        msg.from_user = self.admin_user
        msg.text = "none"
        msg.answer = AsyncMock()

        server = Server(
            id=1,
            name="Origin-RU",
            protocol=XRAY_PROTOCOL,
            is_active=True,
            extra_data={},
        )

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.get_server_by_id", return_value=server):
                with patch("bot.handlers.admin.servers.incy_routes.update_server", new_callable=AsyncMock) as mock_update:
                    await process_server_incy_param_input(msg, self.state, self.mock_session)
                    mock_update.assert_awaited_once()
                    saved_extra = mock_update.call_args[1]["extra_data"]
                    self.assertEqual(saved_extra["origin_badge"], "none")

        # Details formatter should show disabled
        server.extra_data = {"origin_badge": "none"}
        details = _get_server_incy_details(server)
        self.assertEqual(details["origin_badge"], texts.ADMIN_SERVER_INCY_VALUE_DISABLED)

    async def test_process_server_incy_relay_badge_none_disables(self):
        await self.state.set_state(AdminStates.editing_server_incy_relay_badge)
        await self.state.update_data(server_id=1, relay_code="de")

        msg = MagicMock(spec=Message)
        msg.from_user = self.admin_user
        msg.text = "none"
        msg.answer = AsyncMock()

        server = Server(
            id=1,
            name="Origin-RU",
            protocol=XRAY_PROTOCOL,
            is_active=True,
            extra_data={"relays": [{"code": "de", "name": "Германия"}]},
        )

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.get_server_by_id", return_value=server):
                with patch("bot.handlers.admin.servers.incy_routes.update_server", new_callable=AsyncMock) as mock_update:
                    await process_server_incy_relay_badge_input(msg, self.state, self.mock_session)
                    mock_update.assert_awaited_once()
                    saved_extra = mock_update.call_args[1]["extra_data"]
                    self.assertEqual(saved_extra["relay_badges"]["de"], "none")
