import os
import unittest
from unittest.mock import patch

from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.main import setup_bot
from database.models import Tariff, TariffVersion
from database.models import User as DBUser
from tests.test_e2e_user_flows import MockedSession

DB = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class E2EAdminFlowsPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from utils.telegram import _hub_cache, _hub_render_locks

        _hub_cache.clear()
        _hub_render_locks.clear()
        self.engine = create_async_engine(DB)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions.begin() as session:
            await session.execute(
                text(
                    "TRUNCATE account_balance_reservations, "
                    "account_ledger_allocations, account_ledger_entries, "
                    "entitlement_entries, paid_value_ledger, "
                    "tariff_quotes, tariff_versions, payments, "
                    "hub_messages, vpn_profiles, maintenance_mode, audit_logs, "
                    "users, tariffs, system_settings, payment_disputes "
                    "RESTART IDENTITY CASCADE"
                )
            )
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
            await session.flush()
            version = TariffVersion(
                tariff_id=self.tariff.id,
                version_number=1,
                name_snapshot=self.tariff.name,
                duration_hours=720,
                device_limit=2,
                price_rub=150,
                currency="RUB",
            )
            session.add(version)
            await session.commit()

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
                "AMNEZIA_BRIDGE_HMAC_SECRET": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                "DATABASE_URL": os.getenv("TEST_DATABASE_URL", "postgresql+asyncpg://projectx:projectx@localhost:5432/projectx_test"),
            },
        )

        self.env_patcher.start()

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
        for _ in range(20):
            await asyncio.sleep(0)
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

        for _ in range(20):
            await asyncio.sleep(0)
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


if __name__ == "__main__":
    unittest.main()
