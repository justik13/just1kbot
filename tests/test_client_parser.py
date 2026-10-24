"""Unit tests for universal subscription client parsing."""

from __future__ import annotations

import unittest

from utils.client_parser import extract_device_metadata, parse_client_from_user_agent


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

    def test_extract_device_metadata_complete(self):
        headers = {
            "X-Device-Os": "iOS",
            "X-Ver-Os": "17.5.1",
            "X-Device-Model": "iPhone 15 Pro",
            "User-Agent": "INCY/3.7.0 (iPhone; iOS 17.5.1)",
        }
        meta = extract_device_metadata(headers)
        self.assertEqual(meta.get("os"), "iOS 17.5.1")
        self.assertEqual(meta.get("model"), "iPhone 15 Pro")
        self.assertEqual(meta.get("client"), "INCY 3.7.0")
        self.assertEqual(meta.get("ua"), "INCY/3.7.0 (iPhone; iOS 17.5.1)")

    def test_extract_device_metadata_header_variants(self):
        # Alternate header casing and fallback names
        headers = {
            "x-os": "Android",
            "x-ver-os": "14",
            "x-model": "Pixel 8",
            "user-agent": "Happ/3.6.5",
        }
        meta = extract_device_metadata(headers)
        self.assertEqual(meta.get("os"), "Android 14")
        self.assertEqual(meta.get("model"), "Pixel 8")
        self.assertEqual(meta.get("client"), "Happ 3.6.5")
        self.assertEqual(meta.get("ua"), "Happ/3.6.5")

    def test_extract_device_metadata_partial_fields(self):
        # Only OS without version
        meta_os_only = extract_device_metadata({"X-Device-Os": "Windows"})
        self.assertEqual(meta_os_only, {"os": "Windows"})

        # Only version without OS
        meta_ver_only = extract_device_metadata({"X-Ver-Os": "11.0"})
        self.assertEqual(meta_ver_only, {"os": "11.0"})

        # Only model
        meta_model_only = extract_device_metadata({"X-Model": "MacBookPro18,1"})
        self.assertEqual(meta_model_only, {"model": "MacBookPro18,1"})

        # Only UA without client version
        meta_ua_only = extract_device_metadata({"User-Agent": "CustomBrowserWithoutVersion"})
        self.assertEqual(meta_ua_only, {"ua": "CustomBrowserWithoutVersion"})

    def test_extract_device_metadata_sanitization_and_truncation(self):
        headers = {
            "X-Device-Os": "iOS!@#$%^&*()_+",
            "X-Ver-Os": "17.5.1<script>",
            "X-Device-Model": "Super-Phone-Model, Mark IV; Special",
            "User-Agent": "INCY/3.7.0\r\nInjected-Header: evil\nRest of UA " + "A" * 100,
        }
        meta = extract_device_metadata(headers)
        self.assertEqual(meta.get("os"), "iOS_ 17.5.1script")
        self.assertEqual(meta.get("model"), "Super-Phone-Model, Mark IV Special")
        self.assertEqual(meta.get("client"), "INCY 3.7.0")
        self.assertNotIn("\r", meta.get("ua", ""))
        self.assertNotIn("\n", meta.get("ua", ""))
        self.assertLessEqual(len(meta.get("ua", "")), 64)

    def test_extract_device_metadata_request_object_and_empty(self):
        # Emulate aiohttp web.Request with .headers attribute
        class MockRequest:
            def __init__(self, headers):
                self.headers = headers

        req = MockRequest({"X-Device-Os": "Linux", "User-Agent": "sing-box/1.9.0"})
        meta = extract_device_metadata(req)
        self.assertEqual(meta.get("os"), "Linux")
        self.assertEqual(meta.get("client"), "sing-box 1.9.0")

        # Empty / None inputs
        self.assertEqual(extract_device_metadata({}), {})
        self.assertEqual(extract_device_metadata(None), {})
        self.assertEqual(extract_device_metadata("not-a-dict"), {})


if __name__ == "__main__":
    unittest.main()

