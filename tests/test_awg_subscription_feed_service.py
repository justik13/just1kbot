import base64
import unittest

from services.awg_subscription_feed_service import AWGSubscriptionFeedService

SAMPLE_CONF_NL = """[Interface]
Address = 10.8.0.2/32
DNS = 8.8.8.8, 8.8.4.4
MTU = 1280
PrivateKey = uC6xUgdQDF4+fAOiw37ZQCG7XljilDsnBCl7VH7bAl8=
Jc = 4
Jmin = 10
Jmax = 50
S1 = 79
S2 = 115
S3 = 5
S4 = 1
H1 = 169154911-1234371153
H2 = 2057051984-2121122945
H3 = 2132872968-2133668229
H4 = 2136455412-2141801388

[Peer]
PublicKey = bmXOC+F1FxEMF9dyiK2H5/1SUtzH0JuVo51h2wPfgyo=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 195.133.1.20:443
PersistentKeepalive = 25
"""

SAMPLE_CONF_PL = """[Interface]
Address = 10.8.1.2/32
DNS = 1.1.1.1, 1.0.0.1
MTU = 1280
PrivateKey = iD8yVheREG5+gBPjx48aRDH8YmkjmEtoCDm8WI8cBm9=
Jc = 3
Jmin = 15
Jmax = 45
S1 = 82
S2 = 110
S3 = 8
S4 = 2
H1 = 170000000-1200000000
H2 = 200000000-2100000000
H3 = 210000000-2130000000
H4 = 213000000-2140000000

[Peer]
PublicKey = cnYPD+G2GyFNF0eziL3H6/2TVu0I1KxWp62i3xQghzp=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 185.220.1.50:443
PersistentKeepalive = 25
"""


class AWGSubscriptionFeedServiceTests(unittest.TestCase):
    def test_encode_config_strict_urlsafe_base64(self):
        # A config string crafted so that standard Base64 produces '+' or '/'
        conf_with_special_chars = "[Interface]\nPrivateKey = >>>>????\n"
        uri = AWGSubscriptionFeedService.encode_config_to_awg_uri(
            conf_with_special_chars, "Netherlands", "🇳🇱"
        )

        self.assertTrue(uri.startswith("awg://"))
        self.assertIn("#🇳🇱 Netherlands", uri)

        inner_b64 = uri.removeprefix("awg://").split("#", 1)[0]

        # Ensure strict URL-safe: no '+' or '/' allowed by INCY spec
        self.assertNotIn("+", inner_b64)
        self.assertNotIn("/", inner_b64)

        # Decoding via urlsafe_b64decode reproduces original config
        decoded = base64.urlsafe_b64decode(inner_b64.encode("ascii")).decode("utf-8")
        self.assertEqual(decoded, conf_with_special_chars.strip())

    def test_build_subscription_body_multi_server(self):
        server_configs = [
            (SAMPLE_CONF_NL, "Netherlands", "🇳🇱"),
            (SAMPLE_CONF_PL, "Poland", "🇵🇱"),
        ]

        b64_body = AWGSubscriptionFeedService.build_subscription_body(server_configs)
        self.assertTrue(len(b64_body) > 0)

        # Decode feed body
        decoded_feed = base64.b64decode(b64_body).decode("utf-8")
        lines = [line.strip() for line in decoded_feed.strip().split("\n") if line.strip()]

        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("awg://"))
        self.assertIn("#🇳🇱 Netherlands", lines[0])
        self.assertTrue(lines[1].startswith("awg://"))
        self.assertIn("#🇵🇱 Poland", lines[1])

        # Verify both payloads decode back to exact sample configurations
        payload_nl = lines[0].removeprefix("awg://").split("#", 1)[0]
        self.assertEqual(
            base64.urlsafe_b64decode(payload_nl).decode("utf-8"),
            SAMPLE_CONF_NL.strip(),
        )

        payload_pl = lines[1].removeprefix("awg://").split("#", 1)[0]
        self.assertEqual(
            base64.urlsafe_b64decode(payload_pl).decode("utf-8"),
            SAMPLE_CONF_PL.strip(),
        )

    def test_build_subscription_headers(self):
        headers = AWGSubscriptionFeedService.build_subscription_headers(
            profile_title="JUST1K VPN VIP",
            expire_ts=1790000000,
            upload_bytes=1048576,
            download_bytes=10485760,
            total_quota_bytes=107374182400,
            update_interval_hours=6,
            support_url="https://t.me/just1k_support",
            hide_url=True,
        )

        self.assertEqual(headers["Content-Type"], "text/plain; charset=utf-8")
        self.assertEqual(headers["hide-url"], "1")
        self.assertEqual(headers["Profile-Update-Interval"], "6")
        self.assertEqual(headers["support-url"], "https://t.me/just1k_support")

        # Decode profile-title
        title_b64 = headers["Profile-Title"].removeprefix("base64:")
        self.assertEqual(
            base64.b64decode(title_b64).decode("utf-8"),
            "JUST1K VPN VIP",
        )

        # Validate subscription-userinfo header structure
        userinfo = headers["Subscription-Userinfo"]
        self.assertIn("upload=1048576", userinfo)
        self.assertIn("download=10485760", userinfo)
        self.assertIn("total=107374182400", userinfo)
        self.assertIn("expire=1790000000", userinfo)

    def test_empty_configs_returns_empty_body(self):
        body = AWGSubscriptionFeedService.build_subscription_body([])
        self.assertEqual(body, "")

    def test_create_stub_server(self):
        conf, name, flag = AWGSubscriptionFeedService.create_stub_server(
            "Подписка истекла (Продлите: @just1kbot)",
            "🛑",
        )
        self.assertIn("127.0.0.1:1", conf)
        self.assertEqual(name, "Подписка истекла (Продлите: @just1kbot)")
        self.assertEqual(flag, "🛑")

        body = AWGSubscriptionFeedService.build_subscription_body([(conf, name, flag)])
        decoded = base64.b64decode(body).decode("utf-8")
        self.assertTrue(decoded.startswith("awg://"))
        self.assertIn("#🛑 Подписка истекла (Продлите: @just1kbot)", decoded)

    def test_rich_button_and_banner_headers(self):
        headers = AWGSubscriptionFeedService.build_subscription_headers(
            profile_title="JUST1K VPN",
            support_url="https://t.me/just1k_support",
            web_page_url="https://t.me/just1kbot",
            premium_url="https://t.me/just1kbot?start=renew",
            support_email="support@just1k.best",
            sort_order="ping",
            announce="Техработы завершены",
            announce_url="https://t.me/just1kbot?start=news",
            profile_description="Премиальный быстрый VPN",
            hide_check=True,
            banner_text="Скидка 20% на продление!",
            banner_button_text="Купить",
            banner_button_url="https://t.me/just1kbot?start=sale",
            banner_bg_color="#1e293b",
            banner_button_color="#10b981",
        )

        self.assertEqual(headers["profile-web-page-url"], "https://t.me/just1kbot")
        self.assertEqual(headers["premium-url"], "https://t.me/just1kbot?start=renew")
        self.assertEqual(headers["support-email"], "support@just1k.best")
        self.assertEqual(headers["sort-order"], "ping")
        self.assertEqual(headers["announce-url"], "https://t.me/just1kbot?start=news")
        self.assertEqual(headers["hide-check"], "1")
        self.assertEqual(headers["banner-button-text"], "Купить")
        self.assertEqual(headers["banner-button-url"], "https://t.me/just1kbot?start=sale")
        self.assertEqual(headers["banner-bg-color"], "#1e293b")
        self.assertEqual(headers["banner-button-color"], "#10b981")

        ann_decoded = base64.b64decode(headers["announce"].removeprefix("base64:")).decode("utf-8")
        self.assertEqual(ann_decoded, "Техработы завершены")

        desc_decoded = base64.b64decode(headers["profile-description"].removeprefix("base64:")).decode("utf-8")
        self.assertEqual(desc_decoded, "Премиальный быстрый VPN")

        b_decoded = base64.b64decode(headers["banner-text"].removeprefix("base64:")).decode("utf-8")
        self.assertEqual(b_decoded, "Скидка 20% на продление!")

    def test_build_subscription_body_with_inline_metadata(self):
        inline_meta = [
            "#announce: Важное уведомление",
            "#announce-url: https://t.me/just1kbot",
        ]
        server_configs = [
            (SAMPLE_CONF_NL, "Netherlands", "🇳🇱"),
        ]
        body = AWGSubscriptionFeedService.build_subscription_body(
            server_configs, inline_metadata=inline_meta
        )
        decoded = base64.b64decode(body).decode("utf-8")
        lines = [line.strip() for line in decoded.strip().split("\n") if line.strip()]

        self.assertEqual(lines[0], "#announce: Важное уведомление")
        self.assertEqual(lines[1], "#announce-url: https://t.me/just1kbot")
        self.assertTrue(lines[2].startswith("awg://"))

    def test_sort_servers_by_ping(self):
        configs = [
            ("conf_nl", "Netherlands", "🇳🇱", 99),
            ("conf_de", "Germany", "🇩🇪", 51),
            ("conf_pl", "Poland", "🇵🇱", 71),
            ("conf_unknown", "Unknown", "❓", None),
        ]
        sorted_cfgs = AWGSubscriptionFeedService.sort_servers(configs, mode="ping")
        names = [c[1] for c in sorted_cfgs]
        self.assertEqual(names, ["Germany", "Poland", "Netherlands", "Unknown"])
        # Ensure returned tuples are 3 items: (conf, name, flag)
        self.assertEqual(len(sorted_cfgs[0]), 3)

    def test_sort_servers_by_name(self):
        configs = [
            ("conf_pl", "Poland", "🇵🇱", 71),
            ("conf_de", "Germany", "🇩🇪", 51),
            ("conf_nl", "Netherlands", "🇳🇱", 99),
        ]
        sorted_cfgs = AWGSubscriptionFeedService.sort_servers(configs, mode="name")
        names = [c[1] for c in sorted_cfgs]
        self.assertEqual(names, ["Germany", "Netherlands", "Poland"])

    def test_sort_servers_default_preserves_order(self):
        configs = [
            ("conf_pl", "Poland", "🇵🇱", 71),
            ("conf_de", "Germany", "🇩🇪", 51),
        ]
        sorted_cfgs = AWGSubscriptionFeedService.sort_servers(configs, mode="none")
        names = [c[1] for c in sorted_cfgs]
        self.assertEqual(names, ["Poland", "Germany"])


if __name__ == "__main__":
    unittest.main()
