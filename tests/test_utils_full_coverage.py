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
    days_left_msk,
    format_datetime_msk,
    is_expired,
    now_msk,
    now_utc,
)
from utils.encryption import (
    EncryptedString,
)
from utils.formatters import (
    format_datetime,
    format_days_left,
    format_traffic,
)
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

    def test_days_left_msk(self):
        dt_future = now_utc() + datetime.timedelta(days=5)
        res = days_left_msk(dt_future)
        self.assertNotEqual(res, "—")
        self.assertTrue(len(res) > 0)

        self.assertEqual(days_left_msk(None), "—")

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
        self.assertTrue(uri.startswith("vpn://"))
        decoded = decode_vpn_uri_to_json(uri)
        self.assertEqual(decoded.get("name"), "test_vpn")


if __name__ == "__main__":
    unittest.main()
