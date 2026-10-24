"""Unit tests for User-Agent VPN client and version parser."""

import unittest

from utils.client_parser import parse_vpn_client


class TestVpnClientParser(unittest.TestCase):
    def test_popular_vpn_clients(self):
        cases = [
            ("Happ/5.5.0 (iOS 17.5; iPhone15,2)", "Happ 5.5.0"),
            ("Happ/1.8.0 (iPhone; iOS 17.5.1; Scale/3.00)", "Happ 1.8.0"),
            ("HappClient/5.5.0", "Happ 5.5.0"),
            ("Happ Client/5.5.0", "Happ 5.5.0"),
            ("Happ Client 5.5.0", "Happ 5.5.0"),
            ("Happ 5.5.0", "Happ 5.5.0"),
            ("Happ", "Happ"),
            ("INCY/3.7.0 (Android 14; Pixel 8)", "INCY 3.7.0"),
            ("INCY/2.4.1 (Linux; U; Android 14; SM-S918B)", "INCY 2.4.1"),
            ("IncyClient/2.1.0", "INCY 2.1.0"),
            ("INCY Client/2.1.0", "INCY 2.1.0"),
            ("INCY Client 2.1.0", "INCY 2.1.0"),
            ("INCY/1.2", "INCY 1.2"),
            ("INCY", "INCY"),
            ("v2rayNG/1.8.5", "v2rayNG 1.8.5"),
            ("v2rayNG/1.9.13 (Linux; U; Android 13; SM-G991B)", "v2rayNG 1.9.13"),
            ("v2rayNG 1.8.5", "v2rayNG 1.8.5"),
            ("v2rayN/6.39", "v2rayN 6.39"),
            ("v2rayN 6.39", "v2rayN 6.39"),
            ("FoXray/1.4.2 (iOS 17.2; iPhone14,2)", "FoXray 1.4.2"),
            ("FoXray/1.4.2", "FoXray 1.4.2"),
            ("sing-box/1.9.0", "sing-box 1.9.0"),
            ("sing-box/1.10.0-rc.1", "sing-box 1.10.0-rc.1"),
            ("sing-box 1.8.0", "sing-box 1.8.0"),
            ("sing box 1.9.0", "sing-box 1.9.0"),
            ("SFI/1.8.0", "sing-box 1.8.0"),
            ("SFM/1.8.0", "sing-box 1.8.0"),
            ("SFA/1.8.0", "sing-box 1.8.0"),
            ("Streisand/1.5.8", "Streisand 1.5.8"),
            ("Streisand/1.5.8 CFNetwork/1490.0.4 Darwin/23.2.0", "Streisand 1.5.8"),
            ("Shadowrocket/2.2.36 (iOS 17.4; iPhone14,5)", "Shadowrocket 2.2.36"),
            ("Shadowrocket/1982 CFNetwork/1494.0.7 Darwin/23.4.0", "Shadowrocket 1982"),
            ("Karing/1.0.28", "Karing 1.0.28"),
            ("Karing/1.0.29 (Windows NT 10.0; Win64; x64)", "Karing 1.0.29"),
            ("Hiddify/2.0.5", "Hiddify 2.0.5"),
            ("HiddifyNext/0.14.0", "Hiddify 0.14.0"),
            ("Hiddify Next/2.0.5", "Hiddify 2.0.5"),
            ("Hiddify Next 2.0.5", "Hiddify 2.0.5"),
            ("HiddifyCli/1.0.0", "Hiddify 1.0.0"),
            ("NekoBox/1.3.1", "NekoBox 1.3.1"),
            ("NekoBoxForAndroid/1.3.1", "NekoBox 1.3.1"),
            ("NekoBox for Android/1.3.1", "NekoBox 1.3.1"),
            ("NekoBox for Android 1.3.1", "NekoBox 1.3.1"),
            ("NekoRay/3.26", "NekoRay 3.26"),
            ("Clash/1.18.0", "Clash 1.18.0"),
            ("Clash 1.18.0", "Clash 1.18.0"),
            ("Clash.Meta/v1.16.0", "Clash Meta 1.16.0"),
            ("ClashMeta/1.16.0", "Clash Meta 1.16.0"),
            ("Clash Meta/1.18.0", "Clash Meta 1.18.0"),
            ("Clash Meta 1.18.0", "Clash Meta 1.18.0"),
            ("Clash-verge/1.6.6", "Clash Verge 1.6.6"),
            ("clash-verge-rev/1.7.7", "Clash Verge 1.7.7"),
            ("ClashVerge/1.5.11", "Clash Verge 1.5.11"),
            ("Clash Verge/1.5.1", "Clash Verge 1.5.1"),
            ("Clash Verge 1.5.1", "Clash Verge 1.5.1"),
            ("Clash Verge Rev/1.7.5", "Clash Verge 1.7.5"),
            ("Clash Verge Rev 1.7.5", "Clash Verge 1.7.5"),
            ("ClashForAndroid/2.5.12", "Clash for Android 2.5.12"),
            ("Clash for Android/2.5.12", "Clash for Android 2.5.12"),
            ("Clash for Android 2.5.12", "Clash for Android 2.5.12"),
            ("ClashForWindows/0.20.39", "Clash for Windows 0.20.39"),
            ("Clash for Windows/0.20.39", "Clash for Windows 0.20.39"),
            ("Clash for Windows 0.20.39", "Clash for Windows 0.20.39"),
            ("ClashX/1.118.0", "ClashX 1.118.0"),
            ("ClashX 1.118.0", "ClashX 1.118.0"),
            ("ClashX Pro/1.118.0", "ClashX 1.118.0"),
            ("Mihomo/1.18.0", "Mihomo 1.18.0"),
            ("Mihomo-Party/1.5.0", "Mihomo 1.5.0"),
            ("Mihomo Party/1.5.0", "Mihomo 1.5.0"),
            ("Mihomo Party 1.5.0", "Mihomo 1.5.0"),
            ("Flclash/0.8.59", "FlClash 0.8.59"),
            ("V2Box/1.0.0", "V2Box 1.0.0"),
            ("Surge/5.8.0", "Surge 5.8.0"),
            ("Surge iOS/2883", "Surge 2883"),
            ("Loon/3.2.1", "Loon 3.2.1"),
            ("Stash/2.5.4", "Stash 2.5.4"),
            ("Quantumult%20X/1.0.30", "Quantumult X 1.0.30"),
            ("Quantumult X/1.0.30", "Quantumult X 1.0.30"),
            ("Quantumult/1.0.30", "Quantumult 1.0.30"),
            ("WingsX/2.0.0", "Wings X 2.0.0"),
            ("Wings X/2.0.0", "Wings X 2.0.0"),
            ("AmneziaVPN/4.6.0", "AmneziaVPN 4.6.0"),
            ("Amnezia VPN/4.6.0", "AmneziaVPN 4.6.0"),
            ("AmneziaWG/1.0.0", "AmneziaWG 1.0.0"),
            ("Xray/1.8.4", "Xray 1.8.4"),
            ("v2ray/5.1.0", "v2ray 5.1.0"),
            ("Egern/3.0.0", "Egern 3.0.0"),
            ("Egern 3.0.0", "Egern 3.0.0"),
            ("OneClick/2.0", "OneClick 2.0"),
            ("One-Click 2.0", "OneClick 2.0"),
            ("Outline/1.10.1", "Outline 1.10.1"),
            ("WireGuard/1.0.15", "WireGuard 1.0.15"),
            ("Matsuri/0.5.6", "Matsuri 0.5.6"),
            ("BifrostV/0.6.9", "BifrostV 0.6.9"),
            ("FairVPN/1.0", "Fair VPN 1.0"),
            ("Potatso/2.4.0", "Potatso 2.4.0"),
        ]
        for ua, expected in cases:
            with self.subTest(ua=ua):
                self.assertEqual(parse_vpn_client(ua), expected)

    def test_browser_and_system_uas_are_rejected(self):
        rejected = [
            # Desktop browsers
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 YaBrowser/24.1.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Vivaldi/6.6.3271.45 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 OPR/108.0.0.0",
            # Mobile browsers
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.230 Mobile Safari/537.36",
            "Mozilla/5.0 (Linux; Android 13; SAMSUNG SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/23.0 Chrome/115.0.0.0 Mobile Safari/537.36",
            "Mozilla/5.0 (Linux; U; Android 13; ru-ru) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/100.0.4896.127 Mobile Safari/537.36 XiaoMi/MiuiBrowser/13.28.0-gn",
            "Mozilla/5.0 (Linux; U; Android 12; ru-RU) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/99.0.4844.88 UCBrowser/13.4.0.1306 Mobile Safari/537.36",
            # System HTTP clients and libraries
            "CFNetwork/1494.0.7 Darwin/23.4.0",
            "CFNetwork/1490.0.4",
            "Darwin/23.2.0",
            "okhttp/4.9.2",
            "okhttp/3.12.0",
            "curl/7.88.1",
            "curl/8.4.0",
            "libcurl/7.88.1",
            "Wget/1.21",
            "python-requests/2.31.0",
            "python-urllib/3.12",
            "urllib3/2.1.0",
            "httpx/0.26.0",
            "aiohttp/3.9.1",
            "Go-http-client/1.1",
            "Dalvik/2.1.0 (Linux; U; Android 14; Pixel 8)",
            "PostmanRuntime/7.36.0",
            "WinHTTP/1.0",
            "GuzzleHttp/7.0",
            "Faraday/2.8",
            "Dart/3.0 (dart:io)",
            "Apache-HttpClient/4.5.13",
            # Internal & monitoring agents
            "Just1kBot-Monitor",
            "UptimeRobot/2.0",
            "zabbix",
        ]
        for ua in rejected:
            with self.subTest(ua=ua):
                self.assertIsNone(parse_vpn_client(ua), f"Expected None for: {ua}")

    def test_client_embedded_in_browser_string(self):
        ua = (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 FoXray/1.4.2"
        )
        self.assertEqual(parse_vpn_client(ua), "FoXray 1.4.2")

    def test_client_combined_with_system_library(self):
        ua = "INCY/2.4.1 (Linux; U; Android 14; Pixel 8) okhttp/4.9.2"
        self.assertEqual(parse_vpn_client(ua), "INCY 2.4.1")

    def test_unknown_generic_client(self):
        self.assertEqual(parse_vpn_client("CustomVpn/3.2.1"), "CustomVpn 3.2.1")
        self.assertEqual(parse_vpn_client("SpeedyProxy/v2.0"), "SpeedyProxy 2.0")

    def test_empty_and_invalid_inputs(self):
        self.assertIsNone(parse_vpn_client(None))
        self.assertIsNone(parse_vpn_client(""))
        self.assertIsNone(parse_vpn_client("   "))
        self.assertIsNone(parse_vpn_client("JustRandomNoiseWithoutSlashOrClient"))


if __name__ == "__main__":
    unittest.main()
