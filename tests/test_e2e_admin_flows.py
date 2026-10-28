import os
import unittest
from unittest.mock import patch

from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.main import setup_bot
from database.models import Tariff
from database.models import User as DBUser
from tests.test_e2e_user_flows import MockedSession

DB = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class E2EAdminFlowsPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from database.connection import close_db
        from utils.telegram import _hub_cache, _hub_render_locks

        await close_db()
        _hub_cache.clear()
        _hub_render_locks.clear()
        self.engine = create_async_engine(DB)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        try:
            from tests.db_utils import TRUNCATE_SQL
        except ImportError:
            from db_utils import TRUNCATE_SQL
        async with self.sessions.begin() as session:
            await session.execute(text(TRUNCATE_SQL))
            # Create the admin user
            self.admin_user_db = DBUser(telegram_id=123456789)
            # Create a regular user
            self.target_user_db = DBUser(telegram_id=987654321)

            self.tariff = Tariff(
                name="E2E Basic",
                duration_days=30,
                device_limit=2,
                price_rub=150,
                is_active=True,
            )
            session.add_all((self.admin_user_db, self.target_user_db, self.tariff))

        self.env_patcher = patch.dict(
            os.environ,
            {
                "BOT_TOKEN": "123:test",
                "REDIS_URL": "redis://localhost:6379/1",
                "REDIS_PASSWORD": "test",
                "ADMIN_IDS": "[123456789, 999999999]",
                "SUPPORT_USERNAME": "test_support",
                "DOMAIN": "test.domain",
                "SSL_EMAIL": "test@domain.com",
                "YOOKASSA_SHOP_ID": "123456",
                "YOOKASSA_SECRET_KEY": "test_secret",
                "YOOKASSA_RETURN_URL": "https://t.me/{bot_username}",
                "YOOKASSA_WEBHOOK_PORT": "8080",
                "DB_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
                "DATABASE_URL": os.getenv("TEST_DATABASE_URL", "postgresql+asyncpg://projectx:projectx@localhost:5432/projectx_test"),
            },
        )

        self.env_patcher.start()

        async def mock_throttle(handler, event, data):
            return await handler(event, data)

        self.throttle_patcher = patch(
            "bot.middlewares.throttling.ThrottlingMiddleware.__call__",
            side_effect=mock_throttle,
        )
        self.throttle_patcher.start()

        from config.settings import get_settings

        get_settings.cache_clear()

        self.session = MockedSession()
        self.bot = Bot(token="123:test", session=self.session)

        from aiogram.fsm.storage.memory import MemoryStorage

        _, self.dp = await setup_bot(self.bot, storage=MemoryStorage())

        self.admin = User(
            id=123456789,
            is_bot=False,
            first_name="Admin User",
            username="adminuser",
        )
        self.chat = Chat(id=123456789, type="private")

    async def asyncTearDown(self):
        from bot.middlewares.clean_chat import stop_clean_chat_worker
        from config.settings import get_settings
        from database.connection import close_db
        from utils.telegram import _hub_cache, _hub_render_locks

        await close_db()
        await stop_clean_chat_worker()
        self.env_patcher.stop()
        self.throttle_patcher.stop()
        get_settings.cache_clear()
        _hub_cache.clear()
        _hub_render_locks.clear()
        await self.dp.storage.close()
        # Detach module-level routers from this dispatcher so subsequent
        # test methods can call setup_bot cleanly without aiogram raising
        # "Router is already included in ...".
        for router in self.dp.sub_routers[:]:
            router._parent_router = None
        await self.bot.session.close()
        await self.engine.dispose()

    def _create_message_update(self, text: str) -> Update:
        import time

        self._update_counter = getattr(self, "_update_counter", 0) + 1
        message = Message(
            message_id=100 + self._update_counter,
            date=int(time.time()),
            chat=self.chat,
            from_user=self.admin,
            text=text,
        )
        return Update(update_id=self._update_counter, message=message)

    def _create_callback_update(self, data: str) -> Update:
        import time

        self._update_counter = getattr(self, "_update_counter", 0) + 1
        callback = CallbackQuery(
            id=f"query_{self._update_counter}",
            from_user=self.admin,
            chat_instance="chat1",
            message=Message(
                message_id=100 + self._update_counter,
                date=int(time.time()),
                chat=self.chat,
                from_user=self.admin,
                text="previous menu",
            ).as_(self.bot),
            data=data,
        ).as_(self.bot)
        return Update(update_id=self._update_counter, callback_query=callback)

    async def test_admin_flow_open_menu(self):
        update = self._create_callback_update("admin_menu")
        await self.dp.feed_update(bot=self.bot, update=update)

        req = next(
            req
            for req in reversed(self.session.requests)
            if req.__class__.__name__ == "EditMessageText"
        )
        self.assertIn("Админка", req.text)


    async def test_admin_flow_user_management(self):
        update = self._create_callback_update("admin_users")
        await self.dp.feed_update(bot=self.bot, update=update)

        req = next(
            req
            for req in reversed(self.session.requests)
            if req.__class__.__name__ == "EditMessageText"
        )
        self.assertIn("Пользователи", req.text)

        # Admin clicks on user card
        import asyncio
        await asyncio.sleep(0.35)
        update = self._create_callback_update("admin_user_card:987654321")
        await self.dp.feed_update(bot=self.bot, update=update)

        req = next(
            req
            for req in reversed(self.session.requests)
            if req.__class__.__name__ == "EditMessageText"
        )
        self.assertIn("987654321", req.text)

    async def test_admin_flow_send_direct_message(self):
        import asyncio
        update = self._create_callback_update("admin_send_msg:987654321")
        await self.dp.feed_update(bot=self.bot, update=update)

        req = next(
            req
            for req in reversed(self.session.requests)
            if req.__class__.__name__ == "EditMessageText"
        )
        self.assertIn("Отправка сообщения пользователю", req.text)

        await asyncio.sleep(0.35)
        update = self._create_message_update("Hello from Admin!")
        await self.dp.feed_update(bot=self.bot, update=update)

        import asyncio
        import time
        deadline = time.time() + 2.0
        send_req = None
        while time.time() < deadline:
            try:
                send_req = next(
                    req
                    for req in reversed(self.session.requests)
                    if req.__class__.__name__ == "SendMessage" and req.chat_id == 987654321
                )
                break
            except StopIteration:
                await asyncio.sleep(0.05)
                
        if send_req is None:
            self.fail("No outgoing Telegram request produced by admin flow — check bot send path and test mocks")
        self.assertIn("Hello from Admin!", send_req.text)

    async def test_user_filter_counts_matches_individual_filtered_counts_in_db(self):
        """DB integration test verifying get_user_filter_counts() equals get_filtered_users_count() for all filters."""
        from datetime import datetime, timedelta, timezone
        from database.models import User as DBUser
        from database.repositories.users_repo import get_user_filter_counts, get_filtered_users_count
        try:
            from tests.db_utils import TRUNCATE_SQL
        except ImportError:
            from db_utils import TRUNCATE_SQL

        now = datetime.now(timezone.utc)
        async with self.sessions.begin() as session:
            await session.execute(text(TRUNCATE_SQL))
            # 1. Normal active user (new within 7 days, active sub for 30 days)
            u1 = DBUser(telegram_id=1, username="u1", created_at=now - timedelta(days=2), subscription_end=now + timedelta(days=30))
            # 2. Expiring user (active sub for 2 days)
            u2 = DBUser(telegram_id=2, username="u2", created_at=now - timedelta(days=10), subscription_end=now + timedelta(days=2))
            # 3. Expired user (subscription_end in the past)
            u3 = DBUser(telegram_id=3, username="u3", created_at=now - timedelta(days=20), subscription_end=now - timedelta(days=5))
            # 4. User without subscription (expired)
            u4 = DBUser(telegram_id=4, username="u4", created_at=now - timedelta(days=3), subscription_end=None)
            # 5. Banned user
            u5 = DBUser(telegram_id=5, username="u5", created_at=now - timedelta(days=4), is_banned=True)
            # 6. Bot blocked user
            u6 = DBUser(telegram_id=6, username="u6", created_at=now - timedelta(days=5), is_bot_blocked=True)
            # 7. Soft-deleted user (should be excluded from all filter counts)
            u7 = DBUser(telegram_id=7, username="u7", created_at=now - timedelta(days=1), is_deleted=True)

            session.add_all([u1, u2, u3, u4, u5, u6, u7])

        async with self.sessions() as session:
            aggregated = await get_user_filter_counts(session)

            for f_type in ["all", "new_7d", "active", "expiring_3d", "expired", "banned"]:
                individual = await get_filtered_users_count(session, filter_type=f_type)
                self.assertEqual(aggregated[f_type], individual, f"Mismatch for filter {f_type}: {aggregated[f_type]} != {individual}")


if __name__ == "__main__":
    unittest.main()
