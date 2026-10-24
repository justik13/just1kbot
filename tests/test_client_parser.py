"""Unit tests for universal subscription client parsing."""

from __future__ import annotations

import unittest

from utils.client_parser import parse_client_from_user_agent


class TestClientParser(unittest.TestCase):
    def test_parse_known_and_custom_subscription_clients(self):
        cases = [
            ("INCY/3.7.0 (iPhone; iOS 17.5.1; Scale/3.00)", "INCY 3.7.0"),
            ("INCY 3.7.0", "INCY 3.7.0"),
            ("INCY/ 3.7.0", "INCY 3.7.0"),
            ("Happ/3.6.5 (build 365; iOS 18.0.0)", "Happ 3.6.5"),
            ("v2rayNG/1.8.5", "v2rayNG 1.8.5"),
            ("sing-box/1.9.0", "sing-box 1.9.0"),
            ("sing-box 1.9.0", "sing-box 1.9.0"),
            ("sing-box / 1.9.0", "sing-box 1.9.0"),
            ("sing-box/1.9.0-rc.1", "sing-box 1.9.0-rc.1"),
            ("FoXray/1.4.2", "FoXray 1.4.2"),
            ("Clash.Meta/1.18.0", "Clash.Meta 1.18.0"),
            ("Clash/1.0", "Clash 1.0"),
            ("Clash/V1.6.6", "Clash 1.6.6"),
            ("Shadowrocket/1982", "Shadowrocket 1982"),
            ("Streisand/1.5", "Streisand 1.5"),
            ("Karing/1.0.21", "Karing 1.0.21"),
            ("clash-verge/v1.6.6", "clash-verge 1.6.6"),
            ("Flclash/0.4.15", "Flclash 0.4.15"),
        ]
        for ua, expected in cases:
            with self.subTest(ua=ua):
                self.assertEqual(parse_client_from_user_agent(ua), expected)

    def test_parse_webview_with_subscription_client(self):
        ua = (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5_1 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 INCY/3.7.0"
        )
        self.assertEqual(parse_client_from_user_agent(ua), "INCY 3.7.0")

    def test_reject_standard_browsers(self):
        browsers = [
            # Chrome on Windows
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            # Firefox on Windows
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0",
            # Safari on macOS
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
            # Edge on Windows
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
            # Chrome on Android
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        ]
        for ua in browsers:
            with self.subTest(ua=ua):
                self.assertIsNone(parse_client_from_user_agent(ua))

    def test_reject_system_libraries_and_generic_tools(self):
        system_tools = [
            "curl/7.88.1",
            "Wget/1.21.4",
            "okhttp/4.9.2",
            "CFNetwork/1410.0.3 Darwin/22.6.0",
            "python-requests/2.31.0",
            "Dart/3.1 (dart:io)",
            "Python/3.11 aiohttp/3.9.1",
            "Go-http-client/1.1",
            "Go-http-client/2.0",
            "HTTP/1.1",
            "Java/17.0.2",
            "PostmanRuntime/7.36.0",
            "axios/1.6.2",
        ]
        for ua in system_tools:
            with self.subTest(ua=ua):
                self.assertIsNone(parse_client_from_user_agent(ua))

    def test_empty_and_invalid_inputs(self):
        self.assertIsNone(parse_client_from_user_agent(""))
        self.assertIsNone(parse_client_from_user_agent(None))
        self.assertIsNone(parse_client_from_user_agent("   "))
        self.assertIsNone(parse_client_from_user_agent("some-random-text-without-version"))


if __name__ == "__main__":
    unittest.main()

