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
    confirm_server_incy_reset,
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
        self.patcher = patch(
            "bot.handlers.admin.servers.incy_routes.render_hub", new_callable=AsyncMock
        )
        self.mock_render_hub = self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def _create_msg(self, text: str) -> MagicMock:
        msg = MagicMock(spec=Message)
        msg.from_user = self.admin_user
        msg.chat = self.chat
        msg.bot = AsyncMock()
        msg.message_id = 123
        msg.text = text
        msg.delete = AsyncMock()
        msg.answer = AsyncMock()
        return msg

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

        msg = self._create_msg("★ Just1k Prime")

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
                    self.mock_render_hub.assert_awaited_once()
                    msg.delete.assert_awaited_once()

    async def test_process_server_incy_param_input_clear(self):
        await self.state.set_state(AdminStates.editing_server_incy_param)
        await self.state.update_data(server_id=1, incy_param="desc")

        msg = self._create_msg("-")

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
                    self.mock_render_hub.assert_awaited_once()
                    msg.delete.assert_awaited_once()

    async def test_process_server_incy_param_input_desc_100_chars(self):
        await self.state.set_state(AdminStates.editing_server_incy_param)
        await self.state.update_data(server_id=1, incy_param="desc")

        long_desc = "Лимит устройств: {devices} (активно: {active}) — стабильное подключение"
        self.assertGreater(len(long_desc), 50)
        self.assertLessEqual(len(long_desc), 100)

        msg = self._create_msg(long_desc)

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
                        extra_data={"profile_description": long_desc},
                    )
                    self.mock_render_hub.assert_awaited_once()
                    msg.delete.assert_awaited_once()

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

        msg = self._create_msg("⚡ YouTube БЕЗ рекламы")

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
                    self.mock_render_hub.assert_awaited_once()
                    msg.delete.assert_awaited_once()

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

        msg = self._create_msg("🇩🇪 Франкфурт Скоростной")

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.get_server_by_id", return_value=server):
                with patch("bot.handlers.admin.servers.incy_routes.update_server", new_callable=AsyncMock) as mock_update:
                    await process_server_incy_relay_name_input(msg, self.state, self.mock_session)
                    mock_update.assert_awaited_once()
                    self.assertIs(mock_update.call_args[0][1], server)
                    saved_extra = mock_update.call_args[1]["extra_data"]
                    self.assertEqual(saved_extra["relay_names"]["de"], "🇩🇪 Франкфурт Скоростной")
                    self.mock_render_hub.assert_awaited_once()
                    msg.delete.assert_awaited_once()

    async def test_confirm_server_incy_reset(self):
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
            extra_data={"profile_title": "Custom"},
        )

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.get_server_by_id", return_value=server):
                with patch("bot.handlers.admin.servers.incy_routes.update_server", new_callable=AsyncMock) as mock_update:
                    await confirm_server_incy_reset(cb, self.state, self.mock_session)
                    mock_update.assert_not_called()
                    cb.message.edit_text.assert_called_once()
                    args, kwargs = cb.message.edit_text.call_args
                    self.assertIn("Подтверждение сброса оформления", args[0])
                    self.assertIn("Origin-RU", args[0])
                    kb = kwargs["reply_markup"]
                    self.assertEqual(kb.inline_keyboard[0][0].callback_data, "admin_server_incy_reset_apply:1")
                    self.assertEqual(kb.inline_keyboard[1][0].callback_data, "admin_server_incy:1")

    async def test_reset_server_incy_to_defaults(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = self.admin_user
        cb.data = "admin_server_incy_reset_apply:1"
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

        msg = self._create_msg("⚡ Новые скоростные узлы добавлены!")

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
                    self.mock_render_hub.assert_awaited_once()
                    msg.delete.assert_awaited_once()

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
        msg_invalid = self._create_msg("javascript:alert(1)")

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.get_server_by_id", return_value=server):
                with patch("bot.handlers.admin.servers.incy_routes.update_server", new_callable=AsyncMock) as mock_update:
                    await process_server_incy_param_input(msg_invalid, self.state, self.mock_session)
                    self.mock_render_hub.assert_awaited_once()
                    self.assertIn(texts.ADMIN_SERVER_INCY_ERR_INVALID_URL, self.mock_render_hub.call_args[0][2])
                    msg_invalid.delete.assert_awaited_once()
                    mock_update.assert_not_awaited()

        self.mock_render_hub.reset_mock()

        # 2. Valid URL
        msg_valid = self._create_msg("https://t.me/just1k_channel")

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.get_server_by_id", return_value=server):
                with patch("bot.handlers.admin.servers.incy_routes.update_server", new_callable=AsyncMock) as mock_update:
                    await process_server_incy_param_input(msg_valid, self.state, self.mock_session)
                    mock_update.assert_awaited_once()
                    saved_extra = mock_update.call_args[1]["extra_data"]
                    self.assertEqual(saved_extra["announce_url"], "https://t.me/just1k_channel")
                    self.mock_render_hub.assert_awaited_once()
                    msg_valid.delete.assert_awaited_once()

    async def test_process_server_incy_title_truncates_to_25_chars(self):
        await self.state.set_state(AdminStates.editing_server_incy_param)
        await self.state.update_data(server_id=1, incy_param="title")

        msg = self._create_msg("A" * 40)

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
                    self.mock_render_hub.assert_awaited_once()
                    msg.delete.assert_awaited_once()

    async def test_process_server_incy_origin_badge_none_disables(self):
        await self.state.set_state(AdminStates.editing_server_incy_param)
        await self.state.update_data(server_id=1, incy_param="origin_badge")

        msg = self._create_msg("none")

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
                    self.mock_render_hub.assert_awaited_once()
                    msg.delete.assert_awaited_once()

        # Details formatter should show disabled
        server.extra_data = {"origin_badge": "none"}
        details = _get_server_incy_details(server)
        self.assertEqual(details["origin_badge"], texts.ADMIN_SERVER_INCY_VALUE_DISABLED)

    async def test_process_server_incy_relay_badge_none_disables(self):
        await self.state.set_state(AdminStates.editing_server_incy_relay_badge)
        await self.state.update_data(server_id=1, relay_code="de")

        msg = self._create_msg("none")

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
                    self.mock_render_hub.assert_awaited_once()
                    msg.delete.assert_awaited_once()

    async def test_process_server_incy_param_input_slash_command_resets_state(self):
        await self.state.set_state(AdminStates.editing_server_incy_param)
        await self.state.update_data(server_id=1, incy_param="title")

        msg = self._create_msg("/start")

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
                    mock_update.assert_not_awaited()
                    state_after = await self.state.get_state()
                    self.assertIsNone(state_after)
                    msg.delete.assert_awaited_once()

    async def test_show_server_incy_card_standard_server(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = self.admin_user
        cb.data = "admin_server_incy:2"
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.edit_text = AsyncMock()

        # Standard VLESS server (not origin)
        server = Server(
            id=2,
            name="NL-Server",
            protocol="vless",
            capabilities=["vless"],
            is_active=True,
            extra_data={"vless_name": "NL Fast Node"},
        )

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.get_server_by_id", return_value=server):
                await show_server_incy_card(cb, self.state, self.mock_session)
                cb.message.edit_text.assert_awaited_once()
                call_args = cb.message.edit_text.call_args[0][0]
                kb = cb.message.edit_text.call_args[1]["reply_markup"]

                # Card text should have standard labels, NOT origin labels
                self.assertIn("Имя узла в клиенте", call_args)
                self.assertIn("Бейдж узла", call_args)
                self.assertIn("Статус в подписке", call_args)
                self.assertNotIn("Шлюз РФ", call_args)

                # Keyboard should have standard buttons, NOT origin or relays
                kb_btns = [b.text for row in kb.inline_keyboard for b in row]
                self.assertIn(texts.ADMIN_SERVER_INCY_BTN_NODE_NAME, kb_btns)
                self.assertIn(texts.ADMIN_SERVER_INCY_BTN_NODE_BADGE, kb_btns)
                self.assertNotIn(texts.ADMIN_SERVER_INCY_BTN_ORIGIN_NAME, kb_btns)
                self.assertNotIn(texts.ADMIN_SERVER_INCY_BTN_RELAYS, kb_btns)

