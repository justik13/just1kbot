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
from bot.formatters import format_days_left, format_subscription_date
from utils.formatters import format_datetime, format_tg_time, format_traffic
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

    def test_to_msk_and_permanent_with_date(self):
        from utils.datetime_helpers import is_permanent_subscription, to_msk
        from bot.formatters import format_days_left

        d = datetime.date(2026, 10, 20)
        msk_dt = to_msk(d)
        self.assertIsNotNone(msk_dt)
        self.assertEqual(msk_dt.year, 2026)
        self.assertIsNone(to_msk(None))
        self.assertIsNone(to_msk("not-a-datetime"))

        self.assertFalse(is_permanent_subscription(d))
        self.assertTrue(is_permanent_subscription(datetime.date(2100, 1, 1)))
        self.assertFalse(is_permanent_subscription(None))
        self.assertFalse(is_permanent_subscription("invalid"))

        # format_days_left with date and invalid input
        self.assertEqual(format_days_left("invalid"), "—")
        self.assertNotEqual(format_days_left(datetime.date(2027, 1, 1)), "—")


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
        # Boundary cases and negative protection
        self.assertEqual(format_traffic(-100), "0 B")
        self.assertEqual(format_traffic(0), "0 B")
        self.assertEqual(format_traffic(1), "1 B")
        self.assertEqual(format_traffic(1023), "1023 B")
        self.assertEqual(format_traffic(1024), "1.0 KiB")
        self.assertEqual(format_traffic(1025), "1.0 KiB")
        self.assertEqual(format_traffic(1048576), "1.0 MiB")
        self.assertEqual(format_traffic(1073741824), "1.0 GiB")
        self.assertEqual(format_traffic(1099511627776), "1.0 TiB")

    def test_format_tg_time(self):
        # 1. None and invalid types
        self.assertEqual(format_tg_time(None), "—")
        self.assertEqual(format_tg_time("invalid"), "—")
        self.assertEqual(format_tg_time(12345), "—")

        # 2. Aware datetime (UTC)
        dt_aware = datetime.datetime(2026, 10, 20, 12, 0, 0, tzinfo=datetime.timezone.utc)
        ts = int(dt_aware.timestamp())
        self.assertEqual(
            format_tg_time(dt_aware, format_spec="dt"),
            f'<tg-time unix="{ts}" format="dt">20.10.2026 15:00</tg-time>',
        )

        # 3. Naive datetime (treated as UTC)
        dt_naive = datetime.datetime(2026, 10, 20, 12, 0, 0)
        self.assertEqual(
            format_tg_time(dt_naive, format_spec="d"),
            f'<tg-time unix="{ts}" format="d">20.10.2026 15:00</tg-time>',
        )

        # 4. Date object (combined with time.min at UTC)
        d_obj = datetime.date(2026, 10, 20)
        dt_date_expected = datetime.datetime(2026, 10, 20, 0, 0, 0, tzinfo=datetime.timezone.utc)
        ts_date = int(dt_date_expected.timestamp())
        self.assertEqual(
            format_tg_time(d_obj, format_spec="d"),
            f'<tg-time unix="{ts_date}" format="d">20.10.2026 03:00</tg-time>',
        )

        # 5. Custom fallback and HTML escaping inside tag
        self.assertEqual(
            format_tg_time(dt_aware, format_spec="R", custom_fallback="<custom & 'safe'>"),
            f'<tg-time unix="{ts}" format="R">&lt;custom &amp; &#x27;safe&#x27;&gt;</tg-time>',
        )

        # 6. Format spec cleanup (removes internal spaces and special characters) and empty format_spec
        self.assertEqual(
            format_tg_time(dt_aware, format_spec="d t", custom_fallback="fallback"),
            f'<tg-time unix="{ts}" format="dt">fallback</tg-time>',
        )
        self.assertEqual(
            format_tg_time(dt_aware, format_spec="d\" style='color:red'", custom_fallback="fallback"),
            f'<tg-time unix="{ts}" format="dstylecolorred">fallback</tg-time>',
        )
        self.assertEqual(
            format_tg_time(dt_aware, format_spec="", custom_fallback="fallback"),
            f'<tg-time unix="{ts}">fallback</tg-time>',
        )

        # 7. unix_ts <= 0: returns escaped fallback without tag
        dt_epoch = datetime.datetime(1970, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(
            format_tg_time(dt_epoch, custom_fallback="<epoch & raw>"),
            "&lt;epoch &amp; raw&gt;",
        )

    def test_format_subscription_date(self):
        from bot import texts
        from utils.datetime_helpers import now_msk

        # 1. None and invalid types
        self.assertEqual(format_subscription_date(None), texts.PLACEHOLDER_DASH)
        self.assertEqual(format_subscription_date("invalid"), texts.PLACEHOLDER_DASH)
        self.assertEqual(format_subscription_date(12345), texts.PLACEHOLDER_DASH)

        # 2. Permanent subscription (year >= 2100)
        dt_perm = datetime.datetime(2100, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(format_subscription_date(dt_perm), texts.TIME_FOREVER)
        d_perm = datetime.date(2100, 1, 1)
        self.assertEqual(format_subscription_date(d_perm), texts.TIME_FOREVER)

        # 3. Current year subscription
        cur_year = now_msk().year
        dt_current_year = datetime.datetime(cur_year, 8, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
        ts = int(dt_current_year.timestamp())
        expected_fallback = texts.DATE_DAY_MONTH_FORMAT.format(day=15, month=texts.MONTH_NAMES_LABELS[7])
        self.assertEqual(
            format_subscription_date(dt_current_year),
            f'<tg-time unix="{ts}" format="d">{expected_fallback}</tg-time>',
        )

        # 4. Date object (date without time)
        d_current_year = datetime.date(cur_year, 8, 15)
        d_expected_dt = datetime.datetime(cur_year, 8, 15, 0, 0, 0, tzinfo=datetime.timezone.utc)
        ts_d = int(d_expected_dt.timestamp())
        self.assertEqual(
            format_subscription_date(d_current_year),
            f'<tg-time unix="{ts_d}" format="d">{expected_fallback}</tg-time>',
        )

        # 5. Next year subscription (includes year)
        dt_next_year = datetime.datetime(cur_year + 1, 3, 10, 12, 0, 0, tzinfo=datetime.timezone.utc)
        ts_next = int(dt_next_year.timestamp())
        expected_next_fallback = texts.DATE_DAY_MONTH_YEAR_FORMAT.format(
            day=10, month=texts.MONTH_NAMES_LABELS[2], year=cur_year + 1
        )
        self.assertEqual(
            format_subscription_date(dt_next_year),
            f'<tg-time unix="{ts_next}" format="d">{expected_next_fallback}</tg-time>',
        )

    def test_has_rich_tags(self):
        from utils.telegram import has_rich_tags

        # Negative cases
        self.assertFalse(has_rich_tags(None))
        self.assertFalse(has_rich_tags(""))
        self.assertFalse(has_rich_tags("Hello, world!"))
        self.assertFalse(has_rich_tags("<b>Bold</b> <i>Italic</i> <code>Code</code> <pre>pre</pre>"))
        self.assertFalse(has_rich_tags('<a href="https://example.com">link</a>'))

        # Positive cases: headings, paragraphs, dividers
        self.assertTrue(has_rich_tags("<h1>Heading 1</h1>"))
        self.assertTrue(has_rich_tags("<h3>Section</h3>"))
        self.assertTrue(has_rich_tags("<p>Paragraph</p>"))
        self.assertTrue(has_rich_tags("<hr>"))
        self.assertTrue(has_rich_tags("<hr/>"))
        self.assertTrue(has_rich_tags("<hr />"))

        # Positive cases: tables
        self.assertTrue(has_rich_tags("<table><tr><td>Cell</td></tr></table>"))
        self.assertTrue(has_rich_tags("<th>Column</th>"))
        self.assertTrue(has_rich_tags("<thead></thead>"))
        self.assertTrue(has_rich_tags("<tbody></tbody>"))

        # Positive cases: blockquotes, buttons, details
        self.assertTrue(has_rich_tags("<blockquote>Quote</blockquote>"))
        self.assertTrue(has_rich_tags("<blockquote expandable>Expandable</blockquote>"))
        self.assertTrue(has_rich_tags('<tg-button text="Tap" />'))
        self.assertTrue(has_rich_tags("<tg-button-row></tg-button-row>"))
        self.assertTrue(has_rich_tags("<details><summary>Sum</summary>Content</details>"))
    def test_normalize_hostname(self):
        from utils.security import normalize_hostname

        self.assertEqual(normalize_hostname("https://cdn.example.com/"), "cdn.example.com")
        self.assertEqual(normalize_hostname("http://bot.example.com:8080/sub/wl"), "bot.example.com")
        self.assertEqual(normalize_hostname("  EXAMPLE.COM  "), "example.com")
        self.assertEqual(normalize_hostname("origin.example.com."), "origin.example.com")
        self.assertEqual(normalize_hostname("cdn.example.com/sub/wl/token"), "cdn.example.com")
        self.assertEqual(normalize_hostname("https://user:pass@cdn.example.com:443/test"), "cdn.example.com")
        self.assertEqual(normalize_hostname("http://127.0.0.1:8080"), "127.0.0.1")
        self.assertEqual(normalize_hostname("foo_bar.com"), "foo_bar.com")
        self.assertEqual(normalize_hostname("foo"), "foo")
        self.assertIsNone(normalize_hostname(""))
        self.assertIsNone(normalize_hostname("   "))
        self.assertIsNone(normalize_hostname(None))
        self.assertIsNone(normalize_hostname("evil.com:bad"))
        self.assertIsNone(normalize_hostname("trusted.com@evil.com"))

    def test_validate_public_fqdn(self):
        from utils.security import validate_public_fqdn

        self.assertTrue(validate_public_fqdn("cdn.example.com"))
        self.assertTrue(validate_public_fqdn("sub.domain.co.uk"))
        self.assertTrue(validate_public_fqdn("node-1.origin.cloud"))
        self.assertTrue(validate_public_fqdn("xn--e1afmkfd.xn--p1ai"))

        self.assertFalse(validate_public_fqdn("foo"))
        self.assertFalse(validate_public_fqdn("foo_bar.com"))
        self.assertFalse(validate_public_fqdn("localhost"))
        self.assertFalse(validate_public_fqdn("app.localhost"))
        self.assertFalse(validate_public_fqdn("127.0.0.1"))
        self.assertFalse(validate_public_fqdn("198.51.100.134"))
        self.assertFalse(validate_public_fqdn("evil.com:bad"))
        self.assertFalse(validate_public_fqdn("trusted.com@evil.com"))
        self.assertFalse(validate_public_fqdn("evil.com;rm"))
        self.assertFalse(validate_public_fqdn("-bad.com"))
        self.assertFalse(validate_public_fqdn("bad-.com"))
        self.assertFalse(validate_public_fqdn("foo.123"))
        self.assertFalse(validate_public_fqdn("foo.c"))
        self.assertFalse(validate_public_fqdn("foo..com"))
        self.assertFalse(validate_public_fqdn(""))
        self.assertFalse(validate_public_fqdn(None))

    def test_normalize_public_domain(self):
        from utils.security import normalize_public_domain

        self.assertEqual(normalize_public_domain("https://cdn.example.com/"), "cdn.example.com")
        self.assertEqual(normalize_public_domain("http://bot.example.com:8080/sub/wl"), "bot.example.com")
        self.assertEqual(normalize_public_domain("  EXAMPLE.COM  "), "example.com")
        self.assertEqual(normalize_public_domain("origin.example.com."), "origin.example.com")
        self.assertEqual(normalize_public_domain("cdn.example.com/sub/wl/token"), "cdn.example.com")
        self.assertEqual(normalize_public_domain("https://user:pass@cdn.example.com:443/test"), "cdn.example.com")

        self.assertIsNone(normalize_public_domain(""))
        self.assertIsNone(normalize_public_domain("   "))
        self.assertIsNone(normalize_public_domain(None))
        self.assertIsNone(normalize_public_domain("foo"))
        self.assertIsNone(normalize_public_domain("foo_bar.com"))
        self.assertIsNone(normalize_public_domain("127.0.0.1"))
        self.assertIsNone(normalize_public_domain("http://127.0.0.1:8080"))
        self.assertIsNone(normalize_public_domain("evil.com:bad"))
        self.assertIsNone(normalize_public_domain("trusted.com@evil.com"))
        self.assertIsNone(normalize_public_domain("evil.com;rm"))
        self.assertIsNone(normalize_public_domain("-bad.com"))
        self.assertIsNone(normalize_public_domain("bad-.com"))

        # Optional require_fqdn=False allows raw hostnames/IPs
        self.assertEqual(
            normalize_public_domain("http://127.0.0.1:8080", require_fqdn=False),
            "127.0.0.1",
        )

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

        # 1. Batch deletion happy path
        deleted, failed = await _safe_delete_batch(bot, 123, [1, 2, 3])
        bot.delete_messages.assert_awaited_once_with(chat_id=123, message_ids=[1, 2, 3])
        self.assertEqual(deleted, [1, 2, 3])
        self.assertEqual(failed, [])

        # 2. Chunking over 100 IDs
        bot.reset_mock()
        large_ids = list(range(1, 251))
        deleted_large, failed_large = await _safe_delete_batch(bot, 123, large_ids)
        self.assertEqual(bot.delete_messages.await_count, 3)
        self.assertEqual(len(deleted_large), 250)
        self.assertEqual(failed_large, [])

        # 3. Fallback on TelegramBadRequest
        bot.reset_mock()
        bot.delete_messages.side_effect = TelegramBadRequest(
            method=MagicMock(), message="Message to delete not found"
        )
        bot.delete_message.side_effect = [
            None,
            TelegramBadRequest(method=MagicMock(), message="Message to delete not found"),
            Exception("Network crash"),
        ]
        deleted, failed = await _safe_delete_batch(bot, 123, [1, 2, 3])
        self.assertIn(1, deleted)
        self.assertIn(2, deleted)
        self.assertIn(3, failed)

        # 4. Fallback on plural "messages can't be deleted"
        bot.reset_mock()
        bot.delete_messages.side_effect = TelegramBadRequest(
            method=MagicMock(), message="Bad Request: messages can't be deleted"
        )
        bot.delete_message.side_effect = [
            None,
            TelegramBadRequest(method=MagicMock(), message="message can't be deleted"),
        ]
        deleted_plural, failed_plural = await _safe_delete_batch(bot, 123, [10, 20])
        self.assertEqual(deleted_plural, [10, 20])
        self.assertEqual(failed_plural, [])

        # 5. Fallback on AttributeError (bot lacking delete_messages)
        bot_legacy = MagicMock()
        bot_legacy.delete_message = AsyncMock(return_value=None)
        del bot_legacy.delete_messages
        deleted_attr, failed_attr = await _safe_delete_batch(bot_legacy, 123, [4, 5])
        self.assertEqual(deleted_attr, [4, 5])
        self.assertEqual(failed_attr, [])

        # 6. Deduplication of duplicate message IDs
        bot.reset_mock()
        bot.delete_messages.side_effect = None
        deleted_dedup, failed_dedup = await _safe_delete_batch(bot, 123, [7, 7, 8, 8, 9])
        bot.delete_messages.assert_awaited_once_with(chat_id=123, message_ids=[7, 8, 9])
        self.assertEqual(deleted_dedup, [7, 8, 9])
        self.assertEqual(failed_dedup, [])

        # 7. Empty input
        d_empty, f_empty = await _safe_delete_batch(bot, 123, [])
        self.assertEqual(d_empty, [])
        self.assertEqual(f_empty, [])


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
