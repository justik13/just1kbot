import os
import unittest
from sqlalchemy import text
from sqlalchemy.exc import DataError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from unittest.mock import patch

from database.models import User
from services.audit_service import AuditService

DB = os.getenv("TEST_DATABASE_URL")

@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class TestAuditServicePostgres(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(DB)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.execute(text("TRUNCATE users, audit_logs RESTART IDENTITY CASCADE"))

    async def asyncTearDown(self):
        async with self.engine.begin() as conn:
            await conn.execute(text("TRUNCATE users, audit_logs RESTART IDENTITY CASCADE"))
        await self.engine.dispose()

    async def test_audit_failure_does_not_poison_transaction(self):
        """If AuditService fails (e.g. invalid data length), outer transaction must still commit."""
        async with self.session_factory() as session:
            # 1. Do some business logic
            user = User(telegram_id=999)
            session.add(user)
            
            # 2. Trigger AuditService but pass an invalid target_type that exceeds varchar limit
            # This causes an actual DB level DataError when create_audit_log tries to flush its insert!
            # The begin_nested() should catch it, rollback its savepoint, and outer transaction lives.
            await AuditService.log_action(
                session,
                admin_id=0,
                action="test",
                target_type="x" * 200, # Assuming limit is 50
                target_id=1,
            )
            
            # 3. Outer transaction should still commit!
            await session.commit()
            
            # Verify user was saved
            u = await session.get(User, user.id)
            self.assertIsNotNone(u)

    async def test_outer_flush_failure_bubbles_up(self):
        """If there is an invalid pending business change, AuditService's preliminary flush must raise it."""
        from sqlalchemy.exc import IntegrityError
        async with self.session_factory() as session:
            # 1. Invalid business logic (duplicate telegram_id)
            user1 = User(telegram_id=888)
            session.add(user1)
            await session.commit()

            user2 = User(telegram_id=888)
            session.add(user2)
            # We DO NOT flush here intentionally
            
            # 2. AuditService should flush first and raise the IntegrityError!
            with self.assertRaises(IntegrityError):
                await AuditService.log_action(session, admin_id=0, action="test")
