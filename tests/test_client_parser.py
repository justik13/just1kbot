"""Unit tests for universal subscription client parsing."""

from __future__ import annotations

import unittest

from utils.client_parser import parse_client_from_user_agent


class TestClientParser(unittest.TestCase):
    def test_parse_incy_and_happ(self):
        cases = [
            ("INCY/3.7.0 (iPhone; iOS 17.5.1; Scale/3.00)", "INCY 3.7.0"),
            ("INCY 3.7.0", "INCY 3.7.0"),
            ("incy/3.7.0", "INCY 3.7.0"),
            ("INCY/v3.7.0", "INCY 3.7.0"),
            ("INCY/V3.7.0", "INCY 3.7.0"),
            ("Happ/3.6.5 (build 365; iOS 18.0.0)", "Happ 3.6.5"),
            ("happ 3.6.5", "Happ 3.6.5"),
            ("Happ/v3.6.5", "Happ 3.6.5"),
            ("HAPP/V3.6.5", "Happ 3.6.5"),
        ]
        for ua, expected in cases:
            with self.subTest(ua=ua):
                self.assertEqual(parse_client_from_user_agent(ua), expected)

    def test_parse_any_hwid_client_fallback(self):
        cases = [
            ("v2rayNG/1.8.5", "v2rayNG 1.8.5"),
            ("sing-box/1.9.0", "sing-box 1.9.0"),
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
            ("AnyCustomHWIDClient/2.4.1 (Linux; x86_64)", "AnyCustomHWIDClient 2.4.1"),
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

    def test_empty_and_invalid_inputs(self):
        self.assertIsNone(parse_client_from_user_agent(""))
        self.assertIsNone(parse_client_from_user_agent(None))
        self.assertIsNone(parse_client_from_user_agent("   "))
        self.assertIsNone(parse_client_from_user_agent("some-random-text-without-version"))


if __name__ == "__main__":
    unittest.main()

