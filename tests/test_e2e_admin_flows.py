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
        self.engine = create_async_engine(DB)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions.begin() as session:
            await session.execute(
                text(
                    "TRUNCATE account_balance_reservations, "
                    "account_ledger_allocations, account_ledger_entries, "
                    "entitlement_entries, paid_value_ledger, "
                    "tariff_quotes, tariff_versions, payments, users, tariffs "
                    "RESTART IDENTITY CASCADE"
                )
            )
            # Create the admin user
            self.admin_user_db = DBUser(telegram_id=999999999)
            # Create a regular user
            self.target_user_db = DBUser(telegram_id=123456789)

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
                "ADMIN_IDS": "[999999999]",
                "SUPPORT_USERNAME": "test_support",
                "DOMAIN": "test.domain",
                "DB_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
                "DATABASE_URL": "postgresql+asyncpg://projectx:projectx@localhost:5432/projectx_test",
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
            id=999999999,
            is_bot=False,
            first_name="Admin User",
            username="adminuser",
        )
        self.chat = Chat(id=999999999, type="private")

    async def asyncTearDown(self):
        from bot.middlewares.clean_chat import stop_clean_chat_worker
        from config.settings import get_settings
        from database.connection import close_db

        await close_db()
        await stop_clean_chat_worker()
        self.env_patcher.stop()
        get_settings.cache_clear()
        await self.dp.storage.close()
        await self.bot.session.close()
        await self.engine.dispose()

    def _create_message_update(self, text: str) -> Update:
        message = Message(
            message_id=100,
            date=0,
            chat=self.chat,
            from_user=self.admin,
            text=text,
        )
        return Update(update_id=1, message=message)

    def _create_callback_update(self, data: str) -> Update:
        callback = CallbackQuery(
            id="query1",
            from_user=self.admin,
            chat_instance="chat1",
            message=Message(
                message_id=101,
                date=0,
                chat=self.chat,
                from_user=self.admin,
                text="previous menu",
            ),
            data=data,
        )
        return Update(update_id=2, callback_query=callback)

    async def test_admin_flow_open_menu(self):
        update = self._create_callback_update("admin_menu")
        await self.dp.feed_update(bot=self.bot, update=update)

        req = next(
            req
            for req in reversed(self.session.requests)
            if req.__class__.__name__ == "EditMessageText"
        )
        self.assertIn("Админ-панель", req.text)

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
        update = self._create_callback_update("admin_user_card:123456789")
        await self.dp.feed_update(bot=self.bot, update=update)

        req = next(
            req
            for req in reversed(self.session.requests)
            if req.__class__.__name__ == "EditMessageText" and "Карточка" in req.text
        )
        self.assertIn("Карточка", req.text)


if __name__ == "__main__":
    unittest.main()
