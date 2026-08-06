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
            
        self.patcher = patch("bot.main.get_settings")
        self.mock_settings = self.patcher.start()
        
        class MockConfig:
            BOT_TOKEN = "123:test"
            REDIS_URL = "redis://redis:6379/1"
            ADMIN_IDS = (999999999,)  # the admin user
            SUPPORT_USERNAME = "test_support"
            DOMAIN = "test.domain"
            DB_ENCRYPTION_KEY = "test_key"
            
        self.mock_settings.return_value = MockConfig()
        
        self.session = MockedSession()
        self.bot = Bot(token="123:test", session=self.session)
        
        _, self.dp = await setup_bot()

        self.admin = User(
            id=999999999,
            is_bot=False,
            first_name="Admin User",
            username="adminuser",
        )
        self.chat = Chat(id=999999999, type="private")

    async def asyncTearDown(self):
        await self.dp.storage.close()
        await self.bot.session.close()
        await self.engine.dispose()
        self.patcher.stop()

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
        update = self._create_message_update("/admin")
        await self.dp.feed_update(bot=self.bot, update=update)
        
        req = self.session.get_request()
        self.assertEqual(req.__class__.__name__, "SendMessage")
        self.assertIn("Панель администратора", req.text)

    async def test_admin_flow_user_management(self):
        update = self._create_callback_update("admin_users")
        await self.dp.feed_update(bot=self.bot, update=update)
        
        req = self.session.get_request()
        self.assertEqual(req.__class__.__name__, "EditMessageText")
        self.assertIn("Введите ID пользователя", req.text)
        
        # Admin sends user ID
        update = self._create_message_update("123456789")
        await self.dp.feed_update(bot=self.bot, update=update)
        
        req = self.session.get_request()
        self.assertEqual(req.__class__.__name__, "SendMessage")
        self.assertIn("Профиль пользователя", req.text)

if __name__ == "__main__":
    unittest.main()
