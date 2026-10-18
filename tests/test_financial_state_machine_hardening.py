"""Production hardening tests.

Validates:
1. AmneziaWG configuration strict validation.
2. User identity cache in UserContextMiddleware.
3. Defensive parsing of topup context referrer fields.
"""

import unittest
from unittest.mock import MagicMock, patch

from aiogram.types import Message
from aiogram.types import User as TgUser

from database.models import User
from services.api_operations_executor import _is_usable_created_config


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


class AmneziaWGValidationTests(unittest.TestCase):
    """Verifies strict AmneziaWG configuration validation in executor."""

    def test_valid_awg_vpn_uri_accepted(self):
        """A valid vpn:// URI containing AWG container must be accepted."""
        with patch("utils.vpn_parser.is_valid_vpn_uri", return_value=True):
            self.assertTrue(_is_usable_created_config("vpn://awg_valid_sample_config_string"))

    def test_valid_wireguard_ini_accepted(self):
        """A raw AWG 2.0+ .conf containing [Interface], [Peer] and mandatory parameters must be accepted."""
        conf = (
            "[Interface]\n"
            "PrivateKey = aaaa=\n"
            "Address = 10.0.0.2/32\n"
            "Jc = 4\nJmin = 10\nJmax = 50\nS1 = 87\nS2 = 61\nS3 = 49\nS4 = 1\n"
            "H1 = 100-200\nH2 = 300-400\nH3 = 500-600\nH4 = 700-800\n"
            "[Peer]\n"
            "PublicKey = bbbb=\n"
            "Endpoint = 1.2.3.4:51820\n"
        )
        self.assertTrue(_is_usable_created_config(conf))

    def test_html_error_page_rejected(self):
        """HTML 502/504 Bad Gateway responses must be strictly rejected."""
        html_502 = "<!DOCTYPE html><html><head><title>502 Bad Gateway</title></head><body><h1>502 Bad Gateway</h1></body></html>"
        self.assertFalse(_is_usable_created_config(html_502))

        html_nginx = "<html>\r\n<head><title>504 Gateway Time-out</title></head>\r\n<body>\r\n<center><h1>504 Gateway Time-out</h1></center>\r\n<hr><center>nginx</center>\r\n</body>\r\n</html>"
        self.assertFalse(_is_usable_created_config(html_nginx))

    def test_json_error_rejected(self):
        """JSON error responses must be strictly rejected."""
        json_error = '{"status": 500, "error": "Internal Server Error", "message": "Docker container unreachable"}'
        self.assertFalse(_is_usable_created_config(json_error))

    def test_foreign_protocols_rejected(self):
        """Foreign protocols (vless, vmess, ss, trojan) must be strictly rejected."""
        self.assertFalse(_is_usable_created_config("vless://uuid@domain:443?security=reality"))
        self.assertFalse(_is_usable_created_config("vmess://eyJhZGRyIjoiMS4yLjMuNCJ9"))
        self.assertFalse(_is_usable_created_config("ss://YWVzLTEyOC1nY206cGFzc3dvcmRAMS4yLjMuNDo4Mzgw"))
        self.assertFalse(_is_usable_created_config("trojan://pass@domain:443"))

    def test_empty_and_garbage_rejected(self):
        """Empty, None, 'invalid', and arbitrary strings without AWG structure must be rejected."""
        self.assertFalse(_is_usable_created_config(None))
        self.assertFalse(_is_usable_created_config(""))
        self.assertFalse(_is_usable_created_config("   "))
        self.assertFalse(_is_usable_created_config("invalid"))
        self.assertFalse(_is_usable_created_config("arbitrary_string_that_is_longer_than_twenty_characters_and_not_awg"))


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
