import datetime
import ipaddress
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramBadRequest

from utils.admin import is_admin
from utils.callbacks import (
    parse_callback_id,
    parse_callback_int,
    parse_callback_parts,
)
from utils.datetime_helpers import (
    format_datetime_msk,
    is_expired,
    now_msk,
    now_utc,
)
from utils.encryption import (
    EncryptedString,
)
from bot.formatters import format_days_left
from utils.formatters import format_datetime, format_traffic
from utils.logging_security import (
    SensitiveDataFilter,
    safe_url_target,
    sanitize_short,
    sanitize_text,
)
from utils.rate_limiter import TokenBucketRateLimiter
from utils.security import (
    _host_is_localish,
    is_ip_allowed,
    is_safe_url,
)
from utils.telegram import (
    _cleanup_render_locks,
    _get_hub_render_lock,
    _safe_delete_batch,
)
from utils.text_limits import (
    split_text_by_lines,
    truncate_details,
)
from utils.user_locks import get_user_action_lock
from utils.vpn_parser import (
    decode_vpn_uri_to_json,
    encode_json_to_vpn_uri,
)


class TestUtilsAdmin(unittest.TestCase):
    def test_is_admin(self):
        mock_set = MagicMock()
        mock_set.ADMIN_IDS = [100, 200]
        with patch("utils.admin.get_settings", return_value=mock_set):
            self.assertTrue(is_admin(100))
            self.assertTrue(is_admin(200))
            self.assertFalse(is_admin(300))
            self.assertFalse(is_admin(None))


class TestUtilsCallbacks(unittest.TestCase):
    def test_callback_parsers(self):
        self.assertEqual(parse_callback_id("admin_server_card:123"), 123)
        self.assertIsNone(parse_callback_id("invalid_cb"))

        parts = parse_callback_parts("a:b:c", min_parts=2)
        self.assertEqual(parts, ["a", "b", "c"])

        self.assertEqual(parse_callback_int(["a", "123"], 1), 123)


class TestUtilsDatetimeHelpers(unittest.TestCase):
    def test_format_datetime_msk(self):
        dt = datetime.datetime(2026, 8, 7, 14, 30, 0, tzinfo=datetime.timezone.utc)
        fmt = format_datetime_msk(dt)
        self.assertIn("2026", fmt)

        self.assertEqual(format_datetime_msk(None), "—")

    def test_format_days_left_future(self):
        from bot.formatters import format_days_left

        dt_future = now_utc() + datetime.timedelta(days=5)
        res = format_days_left(dt_future)
        self.assertNotEqual(res, "—")
        self.assertTrue(len(res) > 0)

        self.assertEqual(format_days_left(None), "—")

    def test_is_expired(self):
        self.assertTrue(is_expired(None))
        past = now_utc() - datetime.timedelta(days=1)
        self.assertTrue(is_expired(past))
        future = now_utc() + datetime.timedelta(days=1)
        self.assertFalse(is_expired(future))

    def test_now_and_msk(self):
        self.assertIsNotNone(now_utc().tzinfo)
        self.assertIsNotNone(now_msk().tzinfo)


class TestUtilsEncryption(unittest.TestCase):
    def test_encrypted_string_type_decorator(self):
        enc_type = EncryptedString(critical=True)
        self.assertIsNone(enc_type.process_bind_param(None, None))
        self.assertIsNone(enc_type.process_result_value(None, None))

        with patch("config.settings.get_settings") as mock_settings:
            mock_settings.return_value.DB_ENCRYPTION_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
            bound = enc_type.process_bind_param("my_secret_string", None)
            self.assertNotEqual(bound, "my_secret_string")

            res = enc_type.process_result_value(bound, None)
            self.assertEqual(res, "my_secret_string")


class TestUtilsFormatters(unittest.TestCase):
    def test_format_traffic(self):
        self.assertEqual(format_traffic(0), "0 B")
        self.assertEqual(format_traffic(1024), "1.0 KiB")
        self.assertEqual(format_traffic(1048576), "1.0 MiB")

    def test_format_datetime(self):
        dt = datetime.datetime(2026, 8, 7, 14, 30, 0, tzinfo=datetime.timezone.utc)
        self.assertIn("2026", format_datetime(dt))

    def test_format_days_left(self):
        self.assertEqual(format_days_left(None), "—")


class TestUtilsLoggingSecurity(unittest.TestCase):
    def test_sanitize_text(self):
        msg = "Token: 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
        sanitized = sanitize_text(msg)
        self.assertNotIn("123456:ABC-DEF", sanitized)

        short = sanitize_short("Hello world", limit=5)
        self.assertTrue(len(short) <= 8)

        target = safe_url_target("https://api.yookassa.ru/v3/payments")
        self.assertEqual(target, "api.yookassa.ru")

    def test_sensitive_data_filter(self):
        flt = SensitiveDataFilter()
        record = MagicMock()
        record.getMessage.return_value = "Sensitive bot token 123456:ABCDEF"
        record.msg = "Sensitive bot token 123456:ABCDEF"
        record.args = ()
        self.assertTrue(flt.filter(record))


class TestUtilsRateLimiter(unittest.IsolatedAsyncioTestCase):
    async def test_rate_limiter(self):
        limiter = TokenBucketRateLimiter(rate=100.0, burst=10)
        await limiter.acquire()


class TestUtilsSecurity(unittest.IsolatedAsyncioTestCase):
    def test_host_is_localish(self):
        self.assertTrue(_host_is_localish("localhost"))
        self.assertTrue(_host_is_localish("127.0.0.1"))
        self.assertTrue(_host_is_localish("10.0.0.1"))
        self.assertTrue(_host_is_localish("192.168.1.1"))
        self.assertFalse(_host_is_localish("8.8.8.8"))
        self.assertFalse(_host_is_localish("invalid-host!"))

    def test_is_ip_allowed(self):
        ip_public = ipaddress.ip_address("8.8.8.8")
        ip_private = ipaddress.ip_address("127.0.0.1")
        ip_multicast = ipaddress.ip_address("224.0.0.1")

        self.assertTrue(is_ip_allowed(ip_public))
        self.assertFalse(is_ip_allowed(ip_private, allow_local=False))
        self.assertTrue(is_ip_allowed(ip_private, allow_local=True))
        self.assertFalse(is_ip_allowed(ip_multicast))

    @patch("config.settings.get_settings")
    async def test_is_safe_url(self, mock_settings):
        mock_settings.return_value.ALLOW_LOCAL_HTTP = True
        mock_settings.return_value.ALLOW_LOCAL_HTTPS = True

        self.assertTrue(await is_safe_url("http://localhost:8080/health"))
        self.assertFalse(await is_safe_url("ftp://localhost"))
        self.assertFalse(await is_safe_url("http://169.254.169.254/latest/meta-data"))
        self.assertFalse(await is_safe_url("invalid-url-string"))


class TestUtilsTelegram(unittest.IsolatedAsyncioTestCase):
    async def test_render_lock(self):
        lock1 = _get_hub_render_lock(111)
        lock2 = _get_hub_render_lock(111)
        self.assertIs(lock1, lock2)
        _cleanup_render_locks(1000000000.0)

    async def test_safe_delete_batch(self):
        bot = AsyncMock()
        bot.delete_message.side_effect = [
            None,
            TelegramBadRequest(method=MagicMock(), message="Message to delete not found"),
            Exception("Network crash"),
        ]
        deleted, failed = await _safe_delete_batch(bot, 123, [1, 2, 3])
        self.assertIn(1, deleted)
        self.assertIn(2, deleted)
        self.assertIn(3, failed)


class TestUtilsTextLimits(unittest.TestCase):
    def test_split_text_by_lines(self):
        text = "Line 1\nLine 2\nLine 3\nLine 4"
        chunks = split_text_by_lines(text, limit=15)
        self.assertTrue(len(chunks) > 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 15)

    def test_split_text_by_lines_with_blockquote_transition(self):
        text = "Hello world\n<blockquote expandable>Secret key</blockquote>"
        chunks = split_text_by_lines(text, limit=50)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], "Hello world")
        self.assertEqual(chunks[1], "<blockquote expandable>Secret key</blockquote>")
        self.assertNotIn("</blockquote>", chunks[0])

    def test_split_text_by_lines_long_single_line_blockquote(self):
        text = "<blockquote expandable>Secret key is 1234567890abcdefghijklmnopqrstuvwxyz</blockquote>"
        chunks = split_text_by_lines(text, limit=40)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 40)
            self.assertTrue(chunk.startswith("<blockquote"))
            self.assertTrue(chunk.endswith("</blockquote>"))

    def test_split_text_by_lines_multiline_blockquote(self):
        text = "Header\n<blockquote expandable>\nKey line 1\nKey line 2\nKey line 3\n</blockquote>\nFooter"
        chunks = split_text_by_lines(text, limit=50)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 50)
            if "<blockquote" in chunk:
                self.assertIn("</blockquote>", chunk)

    def test_split_text_by_lines_small_limit_no_infinite_loop(self):
        text = "A" * 50
        chunks = split_text_by_lines(text, limit=10)
        self.assertEqual(len(chunks), 5)
        for chunk in chunks:
            self.assertEqual(len(chunk), 10)

    def test_split_text_by_lines_none_or_empty(self):
        self.assertEqual(split_text_by_lines(None), [])
        self.assertEqual(split_text_by_lines("Short"), ["Short"])

    def test_split_text_by_lines_blockquote_almost_fills_limit_and_next_line(self):
        # Regression test for W8 chunk length overflow
        text = "<blockquote expandable>123456789012345\nnext line"
        chunks = split_text_by_lines(text, limit=40)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 40, f"Chunk exceeded limit 40: len={len(c)}: {c}")
            if "<blockquote" in c:
                self.assertIn("</blockquote>", c)

        text2 = "<blockquote expandable>" + "A" * 30 + "\n" + "B" * 30
        for limit in [40, 50, 60]:
            chunks = split_text_by_lines(text2, limit=limit)
            self.assertGreater(len(chunks), 1)
            for c in chunks:
                self.assertLessEqual(
                    len(c), limit, f"Chunk exceeded limit {limit}: len={len(c)}: {c}"
                )
                if "<blockquote" in c:
                    self.assertIn("</blockquote>", c)

    def test_split_text_by_lines_preserves_expandable_attribute(self):
        text = "<blockquote expandable>Very long line that will split into multiple chunks because it exceeds the limit</blockquote>"
        chunks = split_text_by_lines(text, limit=45)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 45)
            self.assertTrue(c.startswith("<blockquote expandable>"))
            self.assertTrue(c.endswith("</blockquote>"))

    def test_split_text_by_lines_small_limits_strip_tags_cleanly(self):
        text = "<blockquote expandable>Hello world this is a test of blockquote splitting with small limits</blockquote>"
        for lim in [5, 10, 15, 20, 30, 35, 36, 37]:
            chunks = split_text_by_lines(text, limit=lim)
            self.assertGreater(len(chunks), 1)
            for c in chunks:
                self.assertLessEqual(len(c), lim, f"Limit {lim} exceeded: len={len(c)}: {c}")
                if "<blockquote" in c:
                    self.assertIn("</blockquote>", c)

    def test_split_text_by_lines_boundary_limits(self):
        text = "<blockquote expandable>xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\ny</blockquote>"
        for lim in [1, 2, 5, 10, 20, 35, 36, 37, 38, 39, 40, 45, 50, 100, 4096]:
            chunks = split_text_by_lines(text, limit=lim)
            self.assertTrue(len(chunks) >= 1)
            for idx, c in enumerate(chunks):
                self.assertLessEqual(len(c), lim, f"Limit {lim} exceeded on chunk {idx}: {c}")
                if "<blockquote" in c:
                    self.assertIn("</blockquote>", c)
                    self.assertTrue(c.startswith("<blockquote expandable>"))
                    self.assertTrue(c.endswith("</blockquote>"))

    def test_split_text_by_lines_transitions_outside_inside_outside(self):
        text = "outside line 1\n<blockquote expandable>inside line</blockquote>\noutside line 2"
        for lim in [40, 50, 60, 100, 4096]:
            chunks = split_text_by_lines(text, limit=lim)
            self.assertTrue(len(chunks) >= 1)
            for c in chunks:
                self.assertLessEqual(len(c), lim)
                if "<blockquote" in c:
                    self.assertIn("</blockquote>", c)

    def test_split_text_by_lines_fuzzed_invariant(self):
        import random
        for limit in [1, 5, 10, 20, 35, 36, 37, 38, 39, 40, 50, 75, 100, 200, 500, 4096]:
            for _ in range(15):
                lines_count = random.randint(1, 10)
                test_lines = []
                in_bq = False
                for _ in range(lines_count):
                    l_len = random.randint(5, 300)
                    if not in_bq and random.random() < 0.3:
                        in_bq = True
                        test_lines.append(f'<blockquote expandable>line_{"x" * l_len}')
                    elif in_bq and random.random() < 0.3:
                        in_bq = False
                        test_lines.append(f'line_{"x" * l_len}</blockquote>')
                    else:
                        test_lines.append(f'line_{"x" * l_len}')
                if in_bq:
                    test_lines.append('</blockquote>')

                sample_text = "\n".join(test_lines)
                chunks = split_text_by_lines(sample_text, limit=limit)
                for idx, ch in enumerate(chunks):
                    self.assertLessEqual(
                        len(ch), limit, f"Limit {limit} violated by chunk {idx}: {ch}"
                    )
                    if "<blockquote" in ch:
                        self.assertIn("</blockquote>", ch)

    def test_truncate_text(self):
        res = truncate_details("Hello World", limit=5)
        self.assertTrue(res.endswith("…"))


class TestUtilsUserLocks(unittest.IsolatedAsyncioTestCase):
    async def test_user_locks(self):
        user_id = 99999
        lock1 = get_user_action_lock(user_id)
        lock2 = get_user_action_lock(user_id)
        self.assertIs(lock1, lock2)


class TestUtilsVpnParser(unittest.TestCase):
    def test_parse_vpn_connection_string(self):
        data = {"name": "test_vpn"}
        uri = encode_json_to_vpn_uri(data)
        decoded = decode_vpn_uri_to_json(uri)
        self.assertEqual(decoded.get("name"), "test_vpn")


class TestUtilsTelegramAutoDelete(unittest.IsolatedAsyncioTestCase):
    async def test_spawn_auto_delete_execution(self):
        from utils.telegram import _auto_delete_delay, spawn_auto_delete

        bot = AsyncMock()
        with patch("utils.telegram._load_hub_ids_from_db", new_callable=AsyncMock) as mock_load:
            mock_load.return_value = []
            with patch("asyncio.sleep", new_callable=AsyncMock):
                spawn_auto_delete(bot, chat_id=123, msg_id=456, delay=0.01)
                await _auto_delete_delay(bot, 123, 456, delay=0.01)
                bot.delete_message.assert_awaited_with(chat_id=123, message_id=456)

            # Test skip when msg_id is in active hub ids
            bot.reset_mock()
            mock_load.return_value = [456]
            with patch("asyncio.sleep", new_callable=AsyncMock):
                await _auto_delete_delay(bot, 123, 456, delay=0.01)
                bot.delete_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
