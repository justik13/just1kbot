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

import base64
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase
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
)
from bot.handlers.vless_web import setup_vless_web_routes
from bot.states import AdminStates
from database.models import Server, Tariff, User, VlessSubscription


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
                self.assertEqual(mock_set.await_count, 4)
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


class TestVlessWebFeedGlobalSettings(AioHTTPTestCase):
    """Test vless_web reading profile title, description, and announce from SystemSetting."""

    def setUp(self):
        super().setUp()
        from bot.handlers import vless_web
        vless_web._ip_rate_limiter.buckets.clear()
        vless_web._token_rate_limiter.buckets.clear()

        now = datetime.now(timezone.utc)
        self.default_user = User(
            id=42,
            telegram_id=999,
            is_banned=False,
            is_deleted=False,
            device_limit=3,
            subscription_end=now + timedelta(days=30),
        )
        self.default_sub = VlessSubscription(
            id=10,
            user_id=42,
            token="test-feed-token-12345678",
            uuid="11111111-2222-3333-4444-555555555555",
            is_active=True,
            active_hwids={},
        )
        self.default_server = Server(
            id=1,
            name="NL-Node-1",
            country_flag="🇳🇱",
            is_active=True,
            protocol="awg",
            capabilities=["awg", "vless"],
            api_url="https://nl.example.com:8443",
            extra_data={"domain": "nl.example.com", "vless_port": 443},
        )

    def tearDown(self):
        from bot.handlers import vless_web
        vless_web._ip_rate_limiter.buckets.clear()
        vless_web._token_rate_limiter.buckets.clear()
        super().tearDown()

    async def get_application(self):
        app = web.Application()
        setup_vless_web_routes(app)
        return app

    async def test_feed_includes_global_system_settings_in_headers(self):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=0)))

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        settings_dict = {
            "vless_profile_title": "✦ MySpecialVPN",
            "vless_profile_description": "High-Speed Encrypted",
            "vless_announce": "Maintenance scheduled tonight",
            "vless_announce_url": "https://t.me/news_channel",
        }

        async def fake_get_setting(session, key, default=None):
            return settings_dict.get(key, default)

        with (
            patch("bot.handlers.vless_web.session_scope", fake_session_scope),
            patch("database.repositories.vless_subscription_repo.get_subscription_by_token", return_value=self.default_sub),
            patch("database.repositories.users_repo.get_user_by_id", return_value=self.default_user),
            patch("services.subscription.SubscriptionService.get_effective_device_limit", return_value=3),
            patch("database.repositories.vless_subscription_repo.register_hwid_atomic", return_value=(True, 1, 3)),
            patch("services.vless_subscription_service.VlessSubscriptionService.ensure_synced_background"),
            patch("services.vless_subscription_service.VlessSubscriptionService.get_eligible_vless_servers", return_value=[self.default_server]),
            patch("bot.handlers.vless_web.get_system_setting", side_effect=fake_get_setting),
        ):
            headers = {"X-Hwid": "client-hwid-abc"}
            resp = await self.client.get("/sub/access/test-feed-token-12345678", headers=headers)
            self.assertEqual(resp.status, 200)

            # Check Profile-Title
            raw_title = resp.headers.get("Profile-Title", "")
            title_b64 = raw_title.removeprefix("base64:")
            title = base64.b64decode(title_b64).decode("utf-8")
            self.assertEqual(title, "✦ MySpecialVPN")

            # Check Profile-Description
            raw_desc = resp.headers.get("Profile-Description", "")
            desc_b64 = raw_desc.removeprefix("base64:")
            desc = base64.b64decode(desc_b64).decode("utf-8")
            self.assertEqual(desc, "High-Speed Encrypted")

            # Check Announce
            raw_ann = resp.headers.get("Announce", "")
            ann_b64 = raw_ann.removeprefix("base64:")
            ann = base64.b64decode(ann_b64).decode("utf-8")
            self.assertEqual(ann, "Maintenance scheduled tonight")

            # Check Announce-Url
            self.assertEqual(resp.headers.get("Announce-Url"), "https://t.me/news_channel")
