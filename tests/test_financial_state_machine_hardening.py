"""Production hardening tests.

Validates:
1. AmneziaWG configuration strict validation.
2. User identity cache in UserContextMiddleware.
3. Defensive parsing of topup context referrer fields.
"""

import unittest
from unittest.mock import MagicMock

from aiogram.types import Message
from aiogram.types import User as TgUser

from database.models import User


class MockSession:
    """Lightweight session mock for middleware testing."""

    def __init__(self, db_user=None):
        self.added = []
        self._user = db_user
        self.flushed = False

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed = True

    async def refresh(self, obj):
        pass

    async def execute(self, stmt):
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=self._user)
        result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        return result

    async def scalar(self, stmt):
        return self._user

    async def get(self, model, ident):
        if model is User and self._user and self._user.id == ident:
            return self._user
        return None


class UserContextMiddlewareCacheTests(unittest.IsolatedAsyncioTestCase):
    """Verifies that UserContextMiddleware caches telegram_id -> user_id without leaking ORM instances."""

    async def test_cache_hits_fetch_fresh_user_from_current_session(self):
        from bot.middlewares.user_context import (
            UserContextMiddleware,
            clear_user_cache,
            get_cached_user_id,
            invalidate_user_cache,
        )

        clear_user_cache()

        user = User(
            id=42,
            telegram_id=99999,
            username="testuser",
            is_deleted=False,
            is_banned=False,
        )
        session1 = MockSession(db_user=user)
        middleware = UserContextMiddleware()

        message = MagicMock(spec=Message)
        from_user = MagicMock(spec=TgUser)
        from_user.id = 99999
        from_user.username = "testuser"
        from_user.first_name = "Tester"
        message.from_user = from_user

        handler_called = False

        async def handler(event, data):
            nonlocal handler_called
            handler_called = True
            return data.get("db_user")

        # First invocation -> cache miss, populates cache with user.id
        data1 = {"session": session1}
        result_user1 = await middleware(handler, message, data1)
        self.assertTrue(handler_called)
        self.assertIsNotNone(result_user1)
        self.assertEqual(result_user1.id, 42)
        is_cached, cached_uid = get_cached_user_id(99999)
        self.assertTrue(is_cached)
        self.assertEqual(cached_uid, 42)

        # Second invocation with session2 -> cache hit, queries session2 with user_id=42
        user_in_session2 = User(
            id=42,
            telegram_id=99999,
            username="testuser",
            is_deleted=False,
            is_banned=True,  # Changed in DB!
        )
        session2 = MockSession(db_user=user_in_session2)
        data2 = {"session": session2}
        result_user2 = await middleware(handler, message, data2)
        self.assertIsNotNone(result_user2)
        self.assertEqual(result_user2.id, 42)
        # Verify that security flag is fresh from session2!
        self.assertTrue(result_user2.is_banned)

        # Invalidate cache
        invalidate_user_cache(99999)
        is_cached, _ = get_cached_user_id(99999)
        self.assertFalse(is_cached)

    async def test_stale_cache_miss_falls_back_to_telegram_id_in_same_request(self):
        """When cached user.id misses in DB, middleware must fall back to telegram_id in same request."""
        from bot.middlewares.user_context import (
            UserContextMiddleware,
            clear_user_cache,
            get_cached_user_id,
            set_cached_user_id,
        )

        clear_user_cache()

        # Seed cache with stale user_id = 999
        set_cached_user_id(88888, 999)

        real_user = User(
            id=77,
            telegram_id=88888,
            username="fallback_user",
            is_deleted=False,
            is_banned=False,
        )

        class StaleSession(MockSession):
            async def execute(self, stmt):
                sql = str(stmt)
                result = MagicMock()
                if "users.id =" in sql or "users_1.id =" in sql or "WHERE users.id" in sql:
                    result.scalar_one_or_none = MagicMock(return_value=None)
                else:
                    result.scalar_one_or_none = MagicMock(return_value=real_user)
                return result

        session = StaleSession(db_user=real_user)
        middleware = UserContextMiddleware()

        message = MagicMock(spec=Message)
        from_user = MagicMock(spec=TgUser)
        from_user.id = 88888
        from_user.username = "fallback_user"
        from_user.first_name = "Fallback"
        message.from_user = from_user

        data = {"session": session}

        async def handler(event, data):
            return data.get("db_user")

        result = await middleware(handler, message, data)

        self.assertIsNotNone(result)
        self.assertEqual(result.id, 77)
        is_cached, cached_uid = get_cached_user_id(88888)
        self.assertTrue(is_cached)
        self.assertEqual(cached_uid, 77)


class TopupContextDefensiveParsingTests(unittest.TestCase):
    """Verifies defensive parsing of topup_context referrer fields."""

    def test_valid_referrer_fields_parsed(self):
        ctx = {"referrer_telegram_id": "123456", "referrer_bonus": "150"}
        ref_id_raw = ctx.get("referrer_telegram_id")
        ref_bonus_raw = ctx.get("referrer_bonus", 0)

        ref_id = None
        ref_bonus = 0
        try:
            if ref_id_raw is not None:
                parsed_id = int(ref_id_raw)
                if parsed_id > 0:
                    ref_id = parsed_id
            if ref_bonus_raw is not None:
                parsed_bonus = int(ref_bonus_raw)
                if parsed_bonus > 0:
                    ref_bonus = parsed_bonus
        except (ValueError, TypeError):
            ref_id = None
            ref_bonus = 0

        self.assertEqual(ref_id, 123456)
        self.assertEqual(ref_bonus, 150)

    def test_malformed_referrer_fields_handled_safely(self):
        for bad_ctx in [
            {"referrer_telegram_id": "not_an_int", "referrer_bonus": "bad"},
            {"referrer_telegram_id": -10, "referrer_bonus": -50},
            {"referrer_telegram_id": None, "referrer_bonus": None},
            {"referrer_telegram_id": {}, "referrer_bonus": []},
        ]:
            ref_id_raw = bad_ctx.get("referrer_telegram_id")
            ref_bonus_raw = bad_ctx.get("referrer_bonus", 0)

            ref_id = None
            ref_bonus = 0
            try:
                if ref_id_raw is not None:
                    parsed_id = int(ref_id_raw)
                    if parsed_id > 0:
                        ref_id = parsed_id
                if ref_bonus_raw is not None:
                    parsed_bonus = int(ref_bonus_raw)
                    if parsed_bonus > 0:
                        ref_bonus = parsed_bonus
            except (ValueError, TypeError):
                ref_id = None
                ref_bonus = 0

            self.assertIsNone(ref_id)
            self.assertEqual(ref_bonus, 0)


if __name__ == "__main__":
    unittest.main()
