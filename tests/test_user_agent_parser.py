"""Tests for utils/user_agent_parser.py."""

from __future__ import annotations

import unittest

from utils.user_agent_parser import parse_device_model_from_ua


class TestUserAgentParser(unittest.TestCase):
    def test_empty_or_none(self):
        self.assertEqual(parse_device_model_from_ua(None, 1), "Device #1 (INCY)")
        self.assertEqual(parse_device_model_from_ua("", 2), "Device #2 (INCY)")
        self.assertEqual(parse_device_model_from_ua("   ", 3), "Device #3 (INCY)")
        self.assertEqual(
            parse_device_model_from_ua(None, 1, fallback_template="Custom #{index}"),
            "Custom #1",
        )

    def test_ios_devices(self):
        self.assertEqual(parse_device_model_from_ua("INCY/1.4.2 (iPhone; iOS 18.2)"), "📱 iPhone")
        self.assertEqual(
            parse_device_model_from_ua("INCY/1.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X)"),
            "📱 iPhone",
        )
        self.assertEqual(
            parse_device_model_from_ua("INCY/1.4.2 (iPad; iPadOS 18.1)"),
            "📱 iPad",
        )

    def test_android_devices(self):
        self.assertEqual(
            parse_device_model_from_ua("INCY/1.3.0 (samsung SM-S928B; Android 14)"),
            "🤖 Samsung (Android)",
        )
        self.assertEqual(
            parse_device_model_from_ua("Happ/2.1 (Android 14; Google Pixel 8 Pro)"),
            "🤖 Google Pixel",
        )
        self.assertEqual(
            parse_device_model_from_ua("v2rayNG/1.8.19 (Android 14; Xiaomi 23049PCD8G)"),
            "🤖 Xiaomi (Android)",
        )
        self.assertEqual(
            parse_device_model_from_ua("INCY/1.0 (Linux; Android 12; OnePlus 9)"),
            "🤖 Android",
        )

    def test_desktop_os(self):
        self.assertEqual(
            parse_device_model_from_ua("Mozilla/5.0 (Windows NT 10.0; Win64; x64)"),
            "💻 Windows PC",
        )
        self.assertEqual(
            parse_device_model_from_ua("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"),
            "💻 Mac",
        )
        self.assertEqual(
            parse_device_model_from_ua("curl/7.88.1 (Linux x86_64)"),
            "💻 Linux PC",
        )

    def test_unrecognized_fallback(self):
        self.assertEqual(
            parse_device_model_from_ua("CustomClient/1.0", fallback_index=4),
            "Device #4 (INCY)",
        )


if __name__ == "__main__":
    unittest.main()
