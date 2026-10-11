"""Unit tests for the Telegram bot admin panel restructuring (Standard vs White Internet subscriptions).

Covers:
1. Admin menu category keyboards and split into std_sub and wi_sub.
2. Admin dashboard category routing handlers.
3. Servers list filtering (std vs wi) and pagination.
4. Server card back button routing.
5. Global Standard INCY settings card, parameter editing, URL validation, and reset.
6. Tariffs list filtering by service_type (awg vs white_internet), pagination, and card back button.
7. VLESS web feed reading profile title, description, announce, and announce_url from SystemSetting.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Chat, Message, User as TgUser

from bot import texts
from bot.keyboards.admin.dashboard import (
    get_admin_menu,
    get_admin_cat_std_sub_keyboard,
    get_admin_cat_wi_sub_keyboard,
)
from bot.keyboards.admin.servers import (
    get_admin_server_card_keyboard,
    get_admin_std_sub_incy_keyboard,
)
from bot.handlers.admin.dashboard import (
    show_admin_cat_std_sub,
    show_admin_cat_wi_sub,
)
from bot.handlers.admin.servers.add_routes import (
    start_add_server,
    select_add_server_protocol,
)
from bot.handlers.admin.servers.common import (
    _build_servers_list_text_and_kb,
)
from bot.handlers.admin.servers.list_routes import (
    show_servers_list,
    servers_pagination,
)
from bot.handlers.admin.servers.incy_routes import (
    show_std_sub_incy_card,
    start_edit_std_incy_param,
    process_std_incy_param_input,
    reset_std_sub_incy_to_defaults,
)
from bot.handlers.admin.tariffs import (
    _build_tariffs_list_text_and_kb,
    show_tariffs_list,
    tariffs_pagination,
    _show_tariff_card,
    process_edit_tariff_rub,
)
from bot.states import AdminStates
from database.models import Tariff


class TestAdminMenuAndCategoryKeyboards(unittest.TestCase):
    """Test dashboard keyboards and category menu structure."""

    def test_get_admin_menu_grid_and_categories(self):
        kb = get_admin_menu(maintenance_enabled=False)
        callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]

        self.assertIn("admin_cat_std_sub", callbacks)
        self.assertIn("admin_cat_wi_sub", callbacks)
        self.assertNotIn("admin_cat_infra", callbacks)

        # Expected layout: adjust(1, 2, 2, 1) -> row 0: users, row 1: std_sub + wi_sub
        self.assertEqual(len(kb.inline_keyboard[0]), 1)
        self.assertEqual(len(kb.inline_keyboard[1]), 2)
        row1_cbs = [b.callback_data for b in kb.inline_keyboard[1]]
        self.assertEqual(row1_cbs, ["admin_cat_std_sub", "admin_cat_wi_sub"])
        self.assertEqual(kb.inline_keyboard[1][0].text, texts.BTN_STD_SUB)
        self.assertEqual(kb.inline_keyboard[1][1].text, texts.BTN_WI_SUB)

    def test_get_admin_cat_std_sub_keyboard(self):
        kb = get_admin_cat_std_sub_keyboard()
        callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]

        self.assertIn("admin_servers:std", callbacks)
        self.assertIn("admin_std_incy", callbacks)
        self.assertIn("admin_tariffs:awg", callbacks)
        self.assertIn("admin_menu", callbacks)

    def test_get_admin_cat_wi_sub_keyboard(self):
        kb = get_admin_cat_wi_sub_keyboard()
        callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]

        self.assertIn("admin_servers:wi", callbacks)
        self.assertIn("admin_tariffs:white_internet", callbacks)
        self.assertIn("admin_menu", callbacks)

    def test_get_admin_std_sub_incy_keyboard(self):
        kb = get_admin_std_sub_incy_keyboard()
        callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]

        self.assertIn("admin_std_incy_edit:title", callbacks)
        self.assertIn("admin_std_incy_edit:desc", callbacks)
        self.assertIn("admin_std_incy_edit:announce", callbacks)
        self.assertIn("admin_std_incy_edit:announce_url", callbacks)
        self.assertIn("admin_std_incy_reset", callbacks)
        self.assertIn("admin_cat_std_sub", callbacks)

    def test_get_admin_server_card_keyboard_back_button(self):
        # Origin server returns to White Internet list
        kb_wi = get_admin_server_card_keyboard(server_id=1, is_active=True, is_xray=True, is_origin=True)
        wi_back = kb_wi.inline_keyboard[-1][0].callback_data
        self.assertEqual(wi_back, "admin_servers:wi")

        # Standard server returns to Standard list
        kb_std = get_admin_server_card_keyboard(server_id=2, is_active=True, is_xray=False, is_origin=False)
        std_back = kb_std.inline_keyboard[-1][0].callback_data
        self.assertEqual(std_back, "admin_servers:std")


class TestAdminCategoryHandlers(unittest.IsolatedAsyncioTestCase):
    """Test dashboard handler transitions for std_sub and wi_sub."""

    async def asyncSetUp(self):
        self.storage = MemoryStorage()
        self.admin_user = TgUser(id=111, is_bot=False, first_name="Admin")
        self.non_admin_user = TgUser(id=999, is_bot=False, first_name="User")
        self.key = StorageKey(bot_id=1, chat_id=111, user_id=111)
        self.state = FSMContext(storage=self.storage, key=self.key)

    async def test_show_admin_cat_std_sub(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = self.admin_user
        cb.data = "admin_cat_std_sub"
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.edit_text = AsyncMock()

        with patch("bot.handlers.admin.dashboard.is_admin", return_value=True):
            await show_admin_cat_std_sub(cb, self.state)
            cb.message.edit_text.assert_awaited_once()
            call_text = cb.message.edit_text.call_args[0][0]
            self.assertIn(texts.BTN_STD_SUB, call_text)
            cb.answer.assert_awaited_once_with(show_alert=False)

    async def test_show_admin_cat_wi_sub(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = self.admin_user
        cb.data = "admin_cat_wi_sub"
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.edit_text = AsyncMock()

        with patch("bot.handlers.admin.dashboard.is_admin", return_value=True):
            await show_admin_cat_wi_sub(cb, self.state)
            cb.message.edit_text.assert_awaited_once()
            call_text = cb.message.edit_text.call_args[0][0]
            self.assertIn(texts.BTN_WI_SUB, call_text)
            cb.answer.assert_awaited_once_with(show_alert=False)

    async def test_show_admin_cat_handlers_unauthorized(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = self.non_admin_user
        cb.answer = AsyncMock()

        with patch("bot.handlers.admin.dashboard.is_admin", return_value=False):
            await show_admin_cat_std_sub(cb, self.state)
            cb.answer.assert_awaited_once_with(texts.ERROR_ACCESS_DENIED, show_alert=True)

        cb.answer.reset_mock()
        with patch("bot.handlers.admin.dashboard.is_admin", return_value=False):
            await show_admin_cat_wi_sub(cb, self.state)
            cb.answer.assert_awaited_once_with(texts.ERROR_ACCESS_DENIED, show_alert=True)


class TestAdminServersFiltering(unittest.IsolatedAsyncioTestCase):
    """Test servers listing and pagination filtered by std and wi."""

    async def asyncSetUp(self):
        self.storage = MemoryStorage()
        self.admin_user = TgUser(id=111, is_bot=False, first_name="Admin")
        self.key = StorageKey(bot_id=1, chat_id=111, user_id=111)
        self.state = FSMContext(storage=self.storage, key=self.key)
        self.mock_session = AsyncMock()

    async def test_build_servers_list_text_and_kb_std(self):
        rendered, builder = await _build_servers_list_text_and_kb(
            servers=[],
            page=1,
            total_pages=1,
            total=0,
            filter_type="std",
        )
        cbs = [b.callback_data for b in builder.as_markup().inline_keyboard for b in b]
        self.assertIn("admin_server_add:std", cbs)
        self.assertIn("admin_cat_std_sub", cbs)

    async def test_build_servers_list_text_and_kb_wi(self):
        rendered, builder = await _build_servers_list_text_and_kb(
            servers=[],
            page=1,
            total_pages=1,
            total=0,
            filter_type="wi",
        )
        cbs = [b.callback_data for b in builder.as_markup().inline_keyboard for b in b]
        self.assertIn("admin_server_add:wi", cbs)
        self.assertIn("admin_cat_wi_sub", cbs)

    async def test_show_servers_list_dispatches_filter_type(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = self.admin_user
        cb.data = "admin_servers:std"
        cb.answer = AsyncMock()

        with patch("bot.handlers.admin.servers.list_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.list_routes._show_servers_list", new_callable=AsyncMock) as mock_show:
                await show_servers_list(cb, self.state, self.mock_session)
                mock_show.assert_awaited_once_with(cb, self.mock_session, page=1, filter_type="std")

    async def test_servers_pagination_parses_filter_and_page(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = self.admin_user
        cb.data = "admin_servers_page:wi:2"
        cb.answer = AsyncMock()

        with patch("bot.handlers.admin.servers.list_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.list_routes._show_servers_list", new_callable=AsyncMock) as mock_show:
                await servers_pagination(cb, self.state, self.mock_session)
                mock_show.assert_awaited_once_with(cb, self.mock_session, page=2, filter_type="wi")

    async def test_start_add_server_std(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = self.admin_user
        cb.data = "admin_server_add:std"
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.edit_text = AsyncMock()

        with patch("bot.handlers.admin.servers.add_routes.is_admin", return_value=True):
            await start_add_server(cb, self.state)
            cb.message.edit_text.assert_awaited_once()
            kb = cb.message.edit_text.call_args[1]["reply_markup"]
            cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
            self.assertIn("admin_server_add_proto:amneziawg2:std", cbs)
            self.assertIn("admin_server_add_proto:vless:std", cbs)
            self.assertNotIn("admin_server_add_proto:xray:wi", cbs)
            self.assertNotIn("admin_server_add_proto:xray", cbs)
            self.assertEqual(kb.inline_keyboard[-1][0].callback_data, "admin_servers:std")

    async def test_start_add_server_wi(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = self.admin_user
        cb.data = "admin_server_add:wi"
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.edit_text = AsyncMock()

        with patch("bot.handlers.admin.servers.add_routes.is_admin", return_value=True):
            await start_add_server(cb, self.state)
            cb.message.edit_text.assert_awaited_once()
            kb = cb.message.edit_text.call_args[1]["reply_markup"]
            cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
            self.assertIn("admin_server_add_proto:xray:wi", cbs)
            self.assertNotIn("admin_server_add_proto:amneziawg2:std", cbs)
            self.assertNotIn("admin_server_add_proto:vless:std", cbs)
            self.assertEqual(kb.inline_keyboard[-1][0].callback_data, "admin_servers:wi")

    async def test_select_add_server_protocol(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = self.admin_user
        cb.data = "admin_server_add_proto:vless:std"
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.edit_text = AsyncMock()

        with patch("bot.handlers.admin.servers.add_routes.is_admin", return_value=True):
            await select_add_server_protocol(cb, self.state)
            state = await self.state.get_state()
            self.assertEqual(state, AdminStates.adding_server.state)
            data = await self.state.get_data()
            self.assertEqual(data.get("protocol"), "vless")
            self.assertEqual(data.get("filter_type"), "std")
            kb = cb.message.edit_text.call_args[1]["reply_markup"]
            self.assertEqual(kb.inline_keyboard[0][0].callback_data, "admin_server_add:std")


class TestAdminStandardIncySettings(unittest.IsolatedAsyncioTestCase):
    """Test global INCY configuration in admin panel for standard subscriptions."""

    async def asyncSetUp(self):
        self.storage = MemoryStorage()
        self.admin_user = TgUser(id=111, is_bot=False, first_name="Admin")
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
        msg.message_id = 456
        msg.text = text
        msg.delete = AsyncMock()
        return msg

    async def test_show_std_sub_incy_card(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = self.admin_user
        cb.data = "admin_std_incy"
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.edit_text = AsyncMock()

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch(
                "bot.handlers.admin.servers.incy_routes.get_system_setting",
                side_effect=lambda s, k, d=None: "Custom Profile Title" if k == "vless_profile_title" else d,
            ):
                await show_std_sub_incy_card(cb, self.state, self.mock_session)
                cb.message.edit_text.assert_awaited_once()
                call_text = cb.message.edit_text.call_args[0][0]
                self.assertIn("Custom Profile Title", call_text)
                self.assertIn("Глобальные настройки оформления", call_text)

    async def test_start_edit_std_incy_param_sets_state(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = self.admin_user
        cb.data = "admin_std_incy_edit:title"
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.edit_text = AsyncMock()

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.get_system_setting", return_value="Current Title"):
                await start_edit_std_incy_param(cb, self.state, self.mock_session)
                state = await self.state.get_state()
                self.assertEqual(state, AdminStates.editing_std_incy_param.state)
                data = await self.state.get_data()
                self.assertEqual(data.get("field"), "title")
                cb.message.edit_text.assert_awaited_once()

    async def test_process_std_incy_param_input_title(self):
        await self.state.set_state(AdminStates.editing_std_incy_param)
        await self.state.update_data(field="title")

        msg = self._create_msg("New Brand Title")

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.set_system_setting", new_callable=AsyncMock) as mock_set:
                await process_std_incy_param_input(msg, self.state, self.mock_session)
                mock_set.assert_awaited_once_with(
                    self.mock_session, "vless_profile_title", "New Brand Title", updated_by=111
                )
                self.mock_render_hub.assert_awaited_once()
                state = await self.state.get_state()
                self.assertIsNone(state)

    async def test_process_std_incy_param_input_announce_url_invalid(self):
        await self.state.set_state(AdminStates.editing_std_incy_param)
        await self.state.update_data(field="announce_url")

        msg = self._create_msg("not-a-valid-url")

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.set_system_setting", new_callable=AsyncMock) as mock_set:
                await process_std_incy_param_input(msg, self.state, self.mock_session)
                mock_set.assert_not_awaited()
                self.mock_render_hub.assert_awaited_once()
                err_text = self.mock_render_hub.call_args[0][2]
                self.assertIn(texts.ADMIN_SERVER_INCY_ERR_INVALID_URL, err_text)
                # State should remain active on validation error
                state = await self.state.get_state()
                self.assertEqual(state, AdminStates.editing_std_incy_param.state)

    async def test_process_std_incy_param_input_empty_message_rejected(self):
        await self.state.set_state(AdminStates.editing_std_incy_param)
        await self.state.update_data(field="title")

        msg = self._create_msg("")
        msg.text = None

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.set_system_setting", new_callable=AsyncMock) as mock_set:
                await process_std_incy_param_input(msg, self.state, self.mock_session)
                mock_set.assert_not_awaited()
                self.mock_render_hub.assert_awaited_once()
                err_text = self.mock_render_hub.call_args[0][2]
                self.assertIn(texts.ERROR_TEXT_REQUIRED, err_text)
                state = await self.state.get_state()
                self.assertEqual(state, AdminStates.editing_std_incy_param.state)

    async def test_process_std_incy_param_input_announce_url_spaces_rejected(self):
        await self.state.set_state(AdminStates.editing_std_incy_param)
        await self.state.update_data(field="announce_url")

        msg = self._create_msg("https://example.com/bad url")

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.set_system_setting", new_callable=AsyncMock) as mock_set:
                await process_std_incy_param_input(msg, self.state, self.mock_session)
                mock_set.assert_not_awaited()
                self.mock_render_hub.assert_awaited_once()
                err_text = self.mock_render_hub.call_args[0][2]
                self.assertIn(texts.ADMIN_SERVER_INCY_ERR_INVALID_URL, err_text)
                state = await self.state.get_state()
                self.assertEqual(state, AdminStates.editing_std_incy_param.state)

    async def test_process_std_incy_param_input_dash_variants_reset(self):
        for dash in ("-", "—", "–"):
            await self.state.set_state(AdminStates.editing_std_incy_param)
            await self.state.update_data(field="title")
            msg = self._create_msg(dash)
            with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
                with patch("bot.handlers.admin.servers.incy_routes.set_system_setting", new_callable=AsyncMock) as mock_set:
                    await process_std_incy_param_input(msg, self.state, self.mock_session)
                    mock_set.assert_awaited_once_with(
                        self.mock_session, "vless_profile_title", "✦ Just1k", updated_by=111
                    )

    async def test_reset_std_sub_incy_to_defaults(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = self.admin_user
        cb.data = "admin_std_incy_reset"
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.edit_text = AsyncMock()

        with patch("bot.handlers.admin.servers.incy_routes.is_admin", return_value=True):
            with patch("bot.handlers.admin.servers.incy_routes.set_system_setting", new_callable=AsyncMock) as mock_set:
                await reset_std_sub_incy_to_defaults(cb, self.state, self.mock_session)
                self.assertEqual(mock_set.await_count, 5)
                cb.answer.assert_awaited_once_with(texts.ADMIN_SERVER_INCY_RESET_SUCCESS, show_alert=False)


class TestAdminTariffsSplit(unittest.IsolatedAsyncioTestCase):
    """Test tariffs split by service_type (awg vs white_internet)."""

    async def asyncSetUp(self):
        self.storage = MemoryStorage()
        self.admin_user = TgUser(id=111, is_bot=False, first_name="Admin")
        self.key = StorageKey(bot_id=1, chat_id=111, user_id=111)
        self.state = FSMContext(storage=self.storage, key=self.key)
        self.mock_session = AsyncMock()

    async def test_build_tariffs_list_text_and_kb_awg(self):
        t1 = Tariff(
            id=1,
            name="Standard 30d",
            service_type="awg",
            duration_days=30,
            device_limit=2,
            price_rub=150,
            is_active=True,
        )
        rendered, builder = await _build_tariffs_list_text_and_kb(
            tariffs=[t1],
            page=1,
            total_pages=1,
            total=1,
            service_type="awg",
        )
        cbs = [b.callback_data for b in builder.as_markup().inline_keyboard for b in b]
        self.assertIn("admin_tariff_card:1", cbs)
        self.assertIn("admin_cat_std_sub", cbs)

    async def test_build_tariffs_list_text_and_kb_white_internet(self):
        t2 = Tariff(
            id=2,
            name="White 30d",
            service_type="white_internet",
            duration_days=30,
            device_limit=1,
            price_rub=200,
            is_active=True,
        )
        rendered, builder = await _build_tariffs_list_text_and_kb(
            tariffs=[t2],
            page=1,
            total_pages=1,
            total=1,
            service_type="white_internet",
        )
        cbs = [b.callback_data for b in builder.as_markup().inline_keyboard for b in b]
        self.assertIn("admin_tariff_card:2", cbs)
        self.assertIn("admin_cat_wi_sub", cbs)

    async def test_show_tariffs_list_dispatches_service_type(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = self.admin_user
        cb.data = "admin_tariffs:awg"
        cb.answer = AsyncMock()

        with patch("bot.handlers.admin.tariffs.is_admin", return_value=True):
            with patch("bot.handlers.admin.tariffs._show_tariffs_list", new_callable=AsyncMock) as mock_show:
                await show_tariffs_list(cb, self.state, self.mock_session)
                mock_show.assert_awaited_once_with(cb, self.mock_session, page=1, service_type="awg")

    async def test_tariffs_pagination_parses_service_type_and_page(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = self.admin_user
        cb.data = "admin_tariffs_page:white_internet:2"
        cb.answer = AsyncMock()

        with patch("bot.handlers.admin.tariffs.is_admin", return_value=True):
            with patch("bot.handlers.admin.tariffs._show_tariffs_list", new_callable=AsyncMock) as mock_show:
                await tariffs_pagination(cb, self.state, self.mock_session)
                mock_show.assert_awaited_once_with(cb, self.mock_session, page=2, service_type="white_internet")

    async def test_show_tariff_card_back_button(self):
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = self.admin_user
        cb.message = MagicMock()
        cb.message.edit_text = AsyncMock()

        tariff_wi = Tariff(
            id=5,
            name="WI Plan",
            service_type="white_internet",
            duration_days=30,
            device_limit=1,
            price_rub=250,
            is_active=True,
        )
        await _show_tariff_card(cb, tariff_wi)
        cb.message.edit_text.assert_awaited_once()
        kb = cb.message.edit_text.call_args[1]["reply_markup"]
        cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
        self.assertIn("admin_tariffs:white_internet", cbs)

    async def test_process_edit_tariff_rub_invalid_input_back_to_card(self):
        await self.state.set_state(AdminStates.editing_tariff_rub)
        await self.state.update_data(tariff_id=7)

        msg = MagicMock(spec=Message)
        msg.from_user = self.admin_user
        msg.chat = MagicMock(id=111)
        msg.bot = AsyncMock()
        msg.text = "not-a-number"
        msg.delete = AsyncMock()

        with patch("bot.handlers.admin.tariffs.is_admin", return_value=True):
            with patch("bot.handlers.admin.tariffs.render_hub", new_callable=AsyncMock) as mock_hub:
                await process_edit_tariff_rub(msg, self.state, self.mock_session)
                mock_hub.assert_awaited_once()
                kb = mock_hub.call_args[0][3]
                self.assertEqual(kb.inline_keyboard[0][0].callback_data, "admin_tariff_card:7")

    async def test_process_edit_tariff_rub_cancel_back_to_card(self):
        await self.state.set_state(AdminStates.editing_tariff_rub)
        await self.state.update_data(tariff_id=7)

        msg = MagicMock(spec=Message)
        msg.from_user = self.admin_user
        msg.chat = MagicMock(id=111)
        msg.bot = AsyncMock()
        msg.text = "/cancel"
        msg.delete = AsyncMock()

        with patch("bot.handlers.admin.tariffs.is_admin", return_value=True):
            with patch("bot.handlers.admin.tariffs.render_hub", new_callable=AsyncMock) as mock_hub:
                await process_edit_tariff_rub(msg, self.state, self.mock_session)
                mock_hub.assert_awaited_once()
                kb = mock_hub.call_args[0][3]
                self.assertEqual(kb.inline_keyboard[0][0].callback_data, "admin_tariff_card:7")
                state = await self.state.get_state()
                self.assertIsNone(state)
