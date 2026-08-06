import asyncio
import os
import unittest
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.methods.base import Request, TelegramType
from aiogram.types import (
    CallbackQuery,
    Chat,
    Message,
    Update,
    User,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.main import setup_bot
from database.models import Tariff, TariffVersion
from database.models import User as DBUser

DB = os.getenv("TEST_DATABASE_URL")

class MockedSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.responses: list[Any] = []
        self.requests: list[Request] = []

    def add_result(self, response: Any) -> None:
        self.responses.append(response)

    def get_request(self) -> Request:
        return self.requests[-1]

    async def close(self):
        pass

    async def make_request(
        self, bot: Bot, method: TelegramMethod[TelegramType], timeout: int | None = None
    ) -> TelegramType:
        self.requests.append(method)
        if self.responses:
            return self.responses.pop(0)
        # Default mock returns
        if method.__class__.__name__ == "SendMessage":
            return Message(
                message_id=uuid.uuid4().int % 10000,
                date=int(asyncio.get_event_loop().time()),
                chat=Chat(id=method.chat_id, type="private"),
                text=method.text,
            )
        elif method.__class__.__name__ == "EditMessageText":
            return Message(
                message_id=method.message_id or 1,
                date=int(asyncio.get_event_loop().time()),
                chat=Chat(id=method.chat_id if method.chat_id else 1, type="private"),
                text=method.text,
            )
        return MagicMock()


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class E2EUserFlowsPostgresTests(unittest.IsolatedAsyncioTestCase):
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
            self.db_user = DBUser(telegram_id=123456789)
            self.tariff = Tariff(
                name="E2E Basic",
                duration_days=30,
                device_limit=2,
                price_rub=150,
                is_active=True,
            )
            session.add_all((self.db_user, self.tariff))
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
            
        # Patch config
        self.patcher = patch("bot.main.get_settings")
        self.mock_settings = self.patcher.start()
        
        class MockConfig:
            BOT_TOKEN = "123:test"
            REDIS_URL = "redis://redis:6379/1"
            ADMIN_IDS = (123456789,)
            SUPPORT_USERNAME = "test_support"
            DOMAIN = "test.domain"
            DB_ENCRYPTION_KEY = "test_key"
            
        self.mock_settings.return_value = MockConfig()
        
        self.session = MockedSession()
        self.bot = Bot(token="123:test", session=self.session)
        
        # We need a dispatcher but without setup_bot starting everything.
        # Actually, setup_bot just returns bot, dp.
        _, self.dp = await setup_bot()

        self.user = User(
            id=123456789,
            is_bot=False,
            first_name="Test User",
            username="testuser",
        )
        self.chat = Chat(id=123456789, type="private")

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
            from_user=self.user,
            text=text,
        )
        return Update(update_id=1, message=message)

    def _create_callback_update(self, data: str) -> Update:
        callback = CallbackQuery(
            id="query1",
            from_user=self.user,
            chat_instance="chat1",
            message=Message(
                message_id=101,
                date=0,
                chat=self.chat,
                from_user=self.user,
                text="previous menu",
            ),
            data=data,
        )
        return Update(update_id=2, callback_query=callback)

    async def test_full_start_to_purchase_flow(self):
        # 1. User sends /start
        update = self._create_message_update("/start")
        await self.dp.feed_update(bot=self.bot, update=update)
        
        # Verify response
        req = self.session.get_request()
        self.assertEqual(req.__class__.__name__, "SendMessage")
        self.assertIn("Главное меню", req.text)

        # 2. User checks balance
        update = self._create_callback_update("menu_balance")
        await self.dp.feed_update(bot=self.bot, update=update)
        
        req = self.session.get_request()
        self.assertEqual(req.__class__.__name__, "EditMessageText")
        self.assertIn("Баланс: 0", req.text)
        
        # 3. User tries to buy tariff from showcase
        update = self._create_callback_update("payment_showcase")
        await self.dp.feed_update(bot=self.bot, update=update)
        req = self.session.get_request()
        self.assertIn("E2E Basic", req.text)
        
        # Emulate clicking on the tariff to quote
        update = self._create_callback_update(f"quote_tariff:{self.tariff.id}")
        await self.dp.feed_update(bot=self.bot, update=update)
        req = self.session.get_request()
        self.assertIn("E2E Basic", req.text)
        self.assertIn("150", req.text)
        # Here they should see "Недостаточно средств" (insufficient funds)
        self.assertIn("Недостаточно средств", req.text)


if __name__ == "__main__":
    unittest.main()
