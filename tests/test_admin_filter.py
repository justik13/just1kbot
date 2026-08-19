"""Unit and integration tests for centralized AdminFilter and admin router tree."""

import importlib
import sys
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram import Router
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from bot.filters import AdminFilter
from bot.handlers.admin import admin_router


class TestAdminFilterUnit(unittest.IsolatedAsyncioTestCase):
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


class TestAdminRouterTreeArchitecture(unittest.TestCase):
    """Architectural tests to guarantee no orphaned admin routers ever bypass the gate."""

    def _collect_descendant_routers(self, root: Router) -> set[Router]:
        collected = {root}
        for sub in root.sub_routers:
            collected |= self._collect_descendant_routers(sub)
        return collected

    def test_all_admin_package_routers_are_descendants_of_admin_root(self):
        """Every Router instance defined in bot.handlers.admin must be inside admin_router."""
        admin_tree = self._collect_descendant_routers(admin_router)
        admin_package_dir = Path(__file__).parents[1] / "bot" / "handlers" / "admin"

        found_routers: list[tuple[str, str, Router]] = []
        for file_path in admin_package_dir.rglob("*.py"):
            rel = file_path.relative_to(admin_package_dir.parent.parent.parent)
            module_name = str(rel.with_suffix("")).replace("/", ".").replace("\\", ".")
            if module_name.endswith(".__init__"):
                module_name = module_name[:-9]
            mod = sys.modules.get(module_name)
            if mod is None:
                try:
                    mod = importlib.import_module(module_name)
                except Exception as e:
                    self.fail(f"Failed to import admin module {module_name}: {e}")

            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if isinstance(attr, Router) and attr is not admin_router:
                    found_routers.append((module_name, attr_name, attr))

        self.assertGreater(len(found_routers), 0, "No admin routers discovered")
        for mod_name, attr_name, router_obj in found_routers:
            with self.subTest(module=mod_name, attribute=attr_name):
                self.assertIn(
                    router_obj,
                    admin_tree,
                    f"Router '{attr_name}' in '{mod_name}' is NOT attached to admin_router! "
                    f"It would bypass the centralized AdminFilter.",
                )


class TestSetupBotLifecycleIdempotency(unittest.IsolatedAsyncioTestCase):
    """Verifies setup_bot() can be called repeatedly without router/parent conflicts."""

    async def test_repeated_setup_bot_lifecycle_clean(self):
        from bot.main import setup_bot
        from bot.handlers.admin import dashboard_router, disputes_router

        fake_settings = MagicMock()
        fake_settings.BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
        fake_settings.REDIS_URL = "redis://localhost:6379/0"
        fake_settings.REDIS_PASSWORD = None
        fake_settings.ADMIN_IDS = [12345]

        for _ in range(3):
            storage = MemoryStorage()
            bot = MagicMock()
            bot.session = AsyncMock()

            with (
                patch("bot.main.get_settings", return_value=fake_settings),
                patch("bot.main.RedisStorage.from_url", return_value=storage),
                patch("bot.main.setup_bot_commands", new_callable=AsyncMock),
            ):
                bot_instance, dp = await setup_bot(bot=bot, storage=storage)
                try:
                    self.assertIn(admin_router, dp.sub_routers)
                    self.assertIn(dashboard_router, admin_router.sub_routers)
                    self.assertIn(disputes_router, dashboard_router.sub_routers)
                finally:
                    await storage.close()
                    for r in dp.sub_routers[:]:
                        r._parent_router = None


class TestAdminRouterDispatcherIntegration(unittest.IsolatedAsyncioTestCase):
    """End-to-end Dispatcher integration tests proving non-admin updates NEVER execute admin handlers."""

    async def asyncSetUp(self):
        from bot.main import setup_bot

        self.bot = AsyncMock()
        self.bot.id = 100
        self.bot.session = AsyncMock()
        self.storage = MemoryStorage()

        self.admin_id = 12345
        self.non_admin_id = 99999

        self.fake_settings = MagicMock()
        self.fake_settings.ADMIN_IDS = [self.admin_id]
        self.fake_settings.BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
        self.fake_settings.REDIS_URL = "redis://localhost:6379/0"
        self.fake_settings.REDIS_PASSWORD = None

        with (
            patch("bot.main.get_settings", return_value=self.fake_settings),
            patch("bot.main.RedisStorage.from_url", return_value=self.storage),
            patch("bot.main.setup_bot_commands", new_callable=AsyncMock),
        ):
            self.bot_instance, self.dp = await setup_bot(bot=self.bot, storage=self.storage)

    async def asyncTearDown(self):
        await self.storage.close()
        for r in self.dp.sub_routers[:]:
            r._parent_router = None

    def _make_callback_update(self, user_id: int, callback_data: str) -> Update:
        user = User(id=user_id, is_bot=False, first_name="Tester", username="tester")
        chat = Chat(id=user_id, type="private")
        message = Message(
            message_id=1,
            date=1000,
            chat=chat,
            from_user=user,
            text="Admin prompt",
        )
        cb = CallbackQuery(
            id="cb_1",
            from_user=user,
            chat_instance="ci_1",
            data=callback_data,
            message=message,
        )
        return Update(update_id=1, callback_query=cb)

    async def test_non_admin_callbacks_never_execute_admin_handlers(self):
        """Security Proof: Non-admin updates targeting admin endpoints NEVER invoke the admin handlers."""
        mock_user = MagicMock()
        mock_user.id = self.non_admin_id
        mock_user.is_banned = False
        mock_user.is_deleted = False

        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = mock_user

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=exec_result)

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        # Probe points across key admin subsystems with their core action functions
        probe_targets = [
            ("admin_menu", "bot.handlers.admin.dashboard._show_admin_dashboard"),
            ("admin_users", "bot.handlers.admin.users.list_routes._build_users_list_text_and_kb"),
            ("admin_servers", "bot.handlers.admin.servers.list_routes._show_servers_list"),
            ("admin_tariffs", "bot.handlers.admin.tariffs._build_tariffs_list_text_and_kb"),
            ("admin_disputes", "bot.handlers.admin.disputes._list_keyboard"),
            ("admin_payments", "bot.handlers.admin.payments._show_payments_list"),
            ("admin_purchases", "bot.handlers.admin.purchases._show_purchases_list"),
            ("aq:home", "bot.handlers.admin.payment_queues._show_home"),
        ]

        with (
            patch("utils.admin.get_settings", return_value=self.fake_settings),
            patch("config.settings.get_settings", return_value=self.fake_settings),
            patch("bot.middlewares.ban_check.get_settings", return_value=self.fake_settings),
            patch("bot.main.get_settings", return_value=self.fake_settings),
            patch("bot.middlewares.db_session.session_scope", side_effect=fake_session_scope),
            patch("bot.middlewares.user_context.get_user_by_telegram_id_any", new_callable=AsyncMock, return_value=mock_user),
            patch("database.repositories.users_repo.get_user_by_telegram_id", new_callable=AsyncMock, return_value=mock_user),
        ):
            for callback_data, handler_action_path in probe_targets:
                with self.subTest(callback=callback_data, action=handler_action_path):
                    with patch(handler_action_path, new_callable=AsyncMock if "list_keyboard" not in handler_action_path else MagicMock) as mock_action:
                        non_admin_update = self._make_callback_update(self.non_admin_id, callback_data)
                        await self.dp.feed_update(self.bot, non_admin_update)

                        # PROOF: Admin action was NOT executed for non-admin update!
                        if isinstance(mock_action, AsyncMock):
                            mock_action.assert_not_awaited()
                        mock_action.assert_not_called()

    async def test_admin_callbacks_successfully_execute_admin_handlers(self):
        """Positive Proof: Admin updates targeting admin endpoints successfully reach and execute the admin handler."""
        mock_admin = MagicMock()
        mock_admin.id = self.admin_id
        mock_admin.is_banned = False
        mock_admin.is_deleted = False

        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = mock_admin

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=exec_result)

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with (
            patch("utils.admin.get_settings", return_value=self.fake_settings),
            patch("config.settings.get_settings", return_value=self.fake_settings),
            patch("bot.middlewares.ban_check.get_settings", return_value=self.fake_settings),
            patch("bot.main.get_settings", return_value=self.fake_settings),
            patch("bot.middlewares.db_session.session_scope", side_effect=fake_session_scope),
            patch("bot.middlewares.user_context.get_user_by_telegram_id_any", new_callable=AsyncMock, return_value=mock_admin),
            patch("database.repositories.users_repo.get_user_by_telegram_id", new_callable=AsyncMock, return_value=mock_admin),
            patch("bot.handlers.admin.dashboard._show_admin_dashboard", new_callable=AsyncMock) as mock_show_dashboard,
        ):
            admin_update = self._make_callback_update(self.admin_id, "admin_menu")
            await self.dp.feed_update(self.bot, admin_update)

            # PROOF: Admin action MUST be executed for authorized admin
            mock_show_dashboard.assert_awaited_once()
