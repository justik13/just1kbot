import datetime
import os
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import Server, User, VPNProfile
from services.workers import (
    cleanup,
    notifications,
)

DB = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class ServicesWorkersFullCoverageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env_patcher = patch.dict(

            os.environ,
            {
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
                "DATABASE_URL": os.getenv("TEST_DATABASE_URL", "postgresql+asyncpg://projectx:projectx@localhost:5432/projectx_test"),
            },
        )
        self.env_patcher.start()
        from config.settings import get_settings
        get_settings.cache_clear()

        self.engine = create_async_engine(DB)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        from tests.db_utils import TRUNCATE_SQL
        async with self.sessions.begin() as session:
            await session.execute(text(TRUNCATE_SQL))

    async def asyncTearDown(self):
        from config.settings import get_settings
        get_settings.cache_clear()
        self.env_patcher.stop()
        await self.engine.dispose()

    async def test_notifications_worker_single_step(self):
        bot = AsyncMock()
        async with self.sessions.begin() as session:
            now = datetime.datetime.now(datetime.timezone.utc)
            u = User(
                telegram_id=999111222,
                subscription_end=now + datetime.timedelta(hours=1),
                notified_2h=False,
            )
            session.add(u)
            await session.flush()

        current_time = datetime.datetime.now(datetime.timezone.utc)
        async with self.sessions.begin() as s2:
            with patch("services.workers.notifications.session_scope") as mock_scope:
                mock_scope.return_value.__aenter__.return_value = s2
                await notifications._send_pre_expiry_notifications(bot, current_time)

        bot.send_message.assert_awaited_once()
        self.assertEqual(bot.send_message.call_args[0][0], 999111222)
        self.assertEqual(bot.send_message.call_args[0][1], notifications.NOTIFY_2H)

    async def test_grace_cleanup_disables_instead_of_deleting_hold_with_active_sub(self):
        """H1: financial_hold + active subscription => disable (pending_update), NEVER delete."""
        async with self.sessions.begin() as session:
            now = datetime.datetime.now(datetime.timezone.utc)
            u = User(
                telegram_id=888112,
                subscription_end=now + datetime.timedelta(days=10),
                financial_hold=True,
            )
            s = Server(name="CleanupHold", api_url="https://hold.test", api_key="k")
            session.add_all((u, s))
            await session.flush()

            p = VPNProfile(
                user_id=u.id,
                server_id=s.id,
                device_name="HoldKey",
                peer_id="peer-hold",
                raw_config="ss://hold",
                provisioning_status="active",
                desired_is_active=True,
                is_active=True,
            )
            session.add(p)
            await session.flush()
            profile_id = p.id

        async with self.sessions.begin() as s2:
            with patch("services.workers.cleanup.session_scope") as mock_scope:
                mock_scope.return_value.__aenter__.return_value = s2
                await cleanup._cleanup_expired_profiles_grace()

        async with self.sessions.begin() as s3:
            from sqlalchemy import select
            row = await s3.scalar(select(VPNProfile).where(VPNProfile.id == profile_id))
            self.assertIsNotNone(row, "profile with active sub under hold must NOT be deleted")
            self.assertFalse(row.desired_is_active)
            self.assertEqual(row.provisioning_status, "pending_update")


if __name__ == "__main__":
    unittest.main()
