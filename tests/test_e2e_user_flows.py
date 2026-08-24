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

    async def stream_content(self, *args, **kwargs):
        yield b""

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
        self.env_patcher = patch.dict(os.environ, {
            "BOT_TOKEN": "123:test",
            "REDIS_URL": "redis://localhost:6379/1",
            "REDIS_PASSWORD": "test",
            "ADMIN_IDS": "[123456789]",
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
        })

        self.env_patcher.start()

        async def mock_throttle(handler, event, data):
            return await handler(event, data)

        self.throttle_patcher = patch(
            "bot.middlewares.throttling.ThrottlingMiddleware.__call__",
            side_effect=mock_throttle
        )
        self.throttle_patcher.start()

        # Clear the lru_cache of get_settings to force it to re-read env vars
        from config.settings import get_settings
        get_settings.cache_clear()

        self.session = MockedSession()
        self.bot = Bot(token="123:test", session=self.session)

        # We need a dispatcher but without setup_bot starting everything.
        # Actually, setup_bot just returns bot, dp.
        from aiogram.fsm.storage.memory import MemoryStorage
        _, self.dp = await setup_bot(self.bot, storage=MemoryStorage())

        self.user = User(
            id=123456789,
            is_bot=False,
            first_name="Test User",
            username="testuser",
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
            from_user=self.user,
            text=text,
        )
        return Update(update_id=self._update_counter, message=message)

    def _create_callback_update(self, data: str) -> Update:
        import time

        self._update_counter = getattr(self, "_update_counter", 0) + 1
        callback = CallbackQuery(
            id=f"query_{self._update_counter}",
            from_user=self.user,
            chat_instance="chat1",
            message=Message(
                message_id=100 + self._update_counter,
                date=int(time.time()),
                chat=self.chat,
                from_user=self.user,
                text="previous menu",
            ).as_(self.bot),
            data=data,
        ).as_(self.bot)
        return Update(update_id=self._update_counter, callback_query=callback)

    async def test_full_start_to_purchase_flow(self):
        # 1. User sends /start
        update = self._create_message_update("/start")
        await self.dp.feed_update(bot=self.bot, update=update)

        # Verify response
        req = next(r for r in reversed(self.session.requests) if r.__class__.__name__ == "SendMessage")
        self.assertIn("Добро пожаловать", req.text)

        # 2. User checks balance
        update = self._create_callback_update("menu_balance")
        await self.dp.feed_update(bot=self.bot, update=update)

        req = next(
            r
            for r in reversed(self.session.requests)
            if r.__class__.__name__ == "EditMessageText"
        )
        self.assertEqual(req.__class__.__name__, "EditMessageText")
        self.assertIn("Баланс:", req.text)

        # 3. User tries to buy tariff from showcase
        update = self._create_callback_update("payment_showcase")
        await self.dp.feed_update(bot=self.bot, update=update)
        req = next(
            r
            for r in reversed(self.session.requests)
            if r.__class__.__name__ == "EditMessageText"
        )
        self.assertIn("Базовый", str(req.reply_markup))

        # Emulate clicking on the tariff to quote
        update = self._create_callback_update(f"select_tariff:{self.tariff.id}:showcase")
        await self.dp.feed_update(bot=self.bot, update=update)
        req = next(r for r in reversed(self.session.requests) if r.__class__.__name__ in ("EditMessageText", "SendMessage") and hasattr(r, "text") and r.text)
        self.assertIn("Базовый", req.text)
        self.assertIn("150", req.text)
        # Here they should see "Не хватает" (insufficient funds)
        self.assertIn("Не хватает", req.text)


if __name__ == "__main__":
    unittest.main()
