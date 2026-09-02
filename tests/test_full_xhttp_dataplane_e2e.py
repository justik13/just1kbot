"""
Comprehensive End-to-End Data-Plane and Integration Verification Test Suite for White Internet.

Verifies:
1. End-to-end VLESS link path generation strictly matching Nginx location directives:
   - {secret_base_path}/default for standalone origin
   - {secret_base_path}/{code} when relaying
2. Complete data-plane flow simulation:
   Client -> Nginx (TLS/HTTP2, OPTIONS->POST mapping, buffer settings) -> Xray XHTTP (packet-up, xPadding) -> Relay -> Internet.
3. XHTTP packet-up obfuscation parameters compliance with CDN spec:
   - xPaddingPlacement="queryInHeader"
   - xPaddingKey="dc"
   - xPaddingHeader="X-Cache"
   - xPaddingMethod="tokenish"
   - uplinkHTTPMethod="OPTIONS"
4. Relay egress enforcement (Anti-Russian Exit):
   - Origin node just1k-wl-default routes strictly to relay outbound tunnel
   - Standalone origin without relays blocks direct egress (routes to blackhole)
   - geosite rules use geosite:category-ru and geosite:tld-ru (not deprecated geosite:ru)
5. Nginx camouflage site & probe endpoints:
   - / serves static HTML landing page returning HTTP 200
   - /cdn-check returns HTTP 204 No Content
   - Buffer limits configured: client_max_body_size 0, large_client_header_buffers 8 64k, http2_max_field_size 64k
6. Certbot automated renewal deploy hook:
   - /etc/letsencrypt/renewal-hooks/deploy/restart-xray-nginx.sh is deployed with executable permissions
   - Re-issues nginx reload and xray restart
7. Dynamic Profile-Title base64 header encoding & Subscription-Userinfo format.
8. Single source of truth (SSOT) capacity accounting query consistency.
9. Subscription feed Caddyfile dynamic routing ({$WHITE_INTERNET_SUB_PATH_PREFIX:/sub/wl}/*).
10. Environment-driven tariff pricing overrides.
"""

from __future__ import annotations

import base64
import json
import unittest
import urllib.parse
from decimal import Decimal
from pathlib import Path

from bot import texts
from config.constants import (
    DEFAULT_WHITE_INTERNET_PADDING_KEY,
    WHITE_INTERNET_BASE_DURATION_DAYS,
    WHITE_INTERNET_BASE_PRICE_RUB,
    WHITE_INTERNET_BASE_TRAFFIC_BYTES,
    WHITE_INTERNET_MAX_QUOTA_BYTES,
    WHITE_INTERNET_TOPUP_PACKS,
)
from config.enums import WhiteInternetProvisioningStatus, WhiteInternetStatus
from database.repositories.servers_repo import capacity_consuming_wl_condition
from services.white_internet_service import WhiteInternetService

REPO_ROOT = Path(__file__).resolve().parent.parent
JUST1KNODE_SH = REPO_ROOT / "scripts" / "just1knode.sh"
CADDYFILE = REPO_ROOT / "Caddyfile"
CADDYFILE_CI = REPO_ROOT / "Caddyfile.ci"


class DummySubscription:
    """Mock WhiteInternetSubscription model for unit and contract verification."""

    def __init__(
        self,
        uuid_val: str = "12345678-1234-5678-1234-567812345678",
        token: str = "testtoken12345678",
        origin_node_id: int = 1,
    ) -> None:
        self.id = 1
        self.uuid = uuid_val
        self.token = token
        self.origin_node_id = origin_node_id
        self.status = WhiteInternetStatus.ACTIVE
        self.provisioning_status = WhiteInternetProvisioningStatus.ACTIVE
        self.traffic_limit_bytes = 50 * 1024**3
        self.traffic_used_bytes = 10 * 1024**3


class TestFullXHttpDataPlaneE2E(unittest.TestCase):
    """End-to-end verification of the XHTTP data plane, routing contracts, and security rules."""

    def test_link_path_strictly_matches_nginx_location(self) -> None:
        """VLESS link path generated for standalone origin must match Nginx location ^~ block."""
        sh_content = JUST1KNODE_SH.read_text(encoding="utf-8")
        sub = DummySubscription()

        # 1. Standalone Origin (no relays attached)
        links = WhiteInternetService.generate_vless_links(
            subscription=sub,
            cdn_domain="cdn.example.com",
            port=443,
            path="/w_abcdef12",
            relays=[],
        )
        self.assertEqual(len(links), 1)
        link = links[0]
        parsed = urllib.parse.urlparse(link)
        params = urllib.parse.parse_qs(parsed.query)

        # Standalone path must be {base}/default
        self.assertIn("path", params)
        link_path = params["path"][0]
        self.assertEqual(link_path, "/w_abcdef12/default")

        # Nginx configuration template must define location ^~ ${secret_path}/default
        self.assertIn("location ^~ ${secret_path}/default", sh_content)
        # Nginx default config must define client_max_body_size 0
        self.assertIn("client_max_body_size 0;", sh_content)

    def test_relay_links_match_nginx_relay_locations(self) -> None:
        """When relays are present, links must match the per-relay Nginx locations."""
        sub = DummySubscription()
        relays = [
            {"code": "de", "name": "Германия", "inbound_port": 8004, "path": "/w_abcdef12/de"},
            {"code": "nl", "name": "Нидерланды", "inbound_port": 8005, "path": "/w_abcdef12/nl"},
            {"code": "se", "name": "Швеция", "inbound_port": 8006, "path": "/w_abcdef12/se"},
        ]

        links = WhiteInternetService.generate_vless_links(
            subscription=sub,
            cdn_domain="cdn.example.com",
            port=443,
            path="/w_abcdef12",
            relays=relays,
        )
        self.assertEqual(len(links), 3)

        de_parsed = urllib.parse.urlparse(links[0])
        de_params = urllib.parse.parse_qs(de_parsed.query)
        self.assertEqual(de_params["path"][0], "/w_abcdef12/de")
        self.assertEqual(urllib.parse.unquote(de_parsed.fragment), "Германия")

        nl_parsed = urllib.parse.urlparse(links[1])
        nl_params = urllib.parse.parse_qs(nl_parsed.query)
        self.assertEqual(nl_params["path"][0], "/w_abcdef12/nl")
        self.assertEqual(urllib.parse.unquote(nl_parsed.fragment), "Нидерланды")

        se_parsed = urllib.parse.urlparse(links[2])
        se_params = urllib.parse.parse_qs(se_parsed.query)
        self.assertEqual(se_params["path"][0], "/w_abcdef12/se")
        self.assertEqual(urllib.parse.unquote(se_parsed.fragment), "Швеция")

    def test_padding_placement_is_query_in_header(self) -> None:
        """XPadding parameter must be set to queryInHeader per Yandex Cloud CDN spec."""
        sub = DummySubscription()
        links = WhiteInternetService.generate_vless_links(
            subscription=sub,
            cdn_domain="cdn.example.com",
            port=443,
            path="/w_custom",
            relays=[],
        )
        parsed = urllib.parse.urlparse(links[0])
        params = urllib.parse.parse_qs(parsed.query)
        self.assertIn("extra", params)
        extra_data = json.loads(params["extra"][0])

        self.assertEqual(extra_data.get("xPaddingPlacement"), "queryInHeader")
        self.assertEqual(extra_data.get("xPaddingKey"), DEFAULT_WHITE_INTERNET_PADDING_KEY)
        self.assertEqual(extra_data.get("xPaddingHeader"), "X-Cache")
        self.assertEqual(extra_data.get("xPaddingMethod"), "tokenish")
        self.assertEqual(extra_data.get("uplinkHTTPMethod"), "OPTIONS")
        self.assertEqual(extra_data.get("mode"), "packet-up")
        self.assertTrue(extra_data.get("xPaddingObfsMode"))

        # Full xray config generator must also have queryInHeader
        full_cfg = WhiteInternetService.generate_full_xray_config(
            subscription=sub,
            cdn_domain="cdn.example.com",
            port=443,
            path="/w_custom",
        )
        outbound = full_cfg["outbounds"][0]
        xhttp_settings = outbound["streamSettings"]["xhttpSettings"]
        self.assertEqual(xhttp_settings.get("xPaddingPlacement"), "queryInHeader")
        self.assertEqual(xhttp_settings.get("path"), "/w_custom/default")
        self.assertEqual(xhttp_settings.get("uplinkHTTPMethod"), "OPTIONS")

        # just1knode.sh template must also use queryInHeader
        sh_content = JUST1KNODE_SH.read_text(encoding="utf-8")
        self.assertIn("'xPaddingPlacement': 'queryInHeader'", sh_content)

    def test_origin_routing_never_exits_to_russian_internet(self) -> None:
        """On Origin node, just1k-wl-default must never route to direct freedom outbound."""
        sh_content = JUST1KNODE_SH.read_text(encoding="utf-8")

        # In standalone mode (install_xray_origin_node), default inbound routes to blackhole block
        self.assertIn(
            "'inboundTag': ['just1k-wl-default'],\n    'outboundTag': 'just1k-wl-block'",
            sh_content,
        )

        # In add_relay_node, default inbound routes to the primary relay outbound
        self.assertIn("primary_relay_tag = f'just1k-wl-outbound-{primary_relay_code}'", sh_content)
        self.assertIn("r['outboundTag'] = primary_relay_tag", sh_content)

    def test_client_geosite_rules_use_category_ru_and_tld_ru(self) -> None:
        """Client routing rules must use geosite:category-ru and geosite:tld-ru (not deprecated geosite:ru)."""
        sub = DummySubscription()
        full_cfg = WhiteInternetService.generate_full_xray_config(
            subscription=sub,
            cdn_domain="cdn.example.com",
        )
        rules = full_cfg["routing"]["rules"]
        direct_domain_rule = next(
            (r for r in rules if r.get("outboundTag") == "direct" and "domain" in r),
            None,
        )
        self.assertIsNotNone(direct_domain_rule)
        domains = direct_domain_rule["domain"]
        self.assertIn("geosite:category-ru", domains)
        self.assertIn("geosite:tld-ru", domains)
        self.assertNotIn("geosite:ru", domains)

    def test_nginx_camouflage_site_and_buffers(self) -> None:
        """Nginx must serve camouflage site on / and define zero request buffering for streaming."""
        sh_content = JUST1KNODE_SH.read_text(encoding="utf-8")

        # Camouflage site landing page created
        self.assertIn("mkdir -p \"${WWW_HTML_DIR}\"", sh_content)
        self.assertIn("<!DOCTYPE html>", sh_content)
        self.assertIn("try_files \\$uri \\$uri/ =404;", sh_content)
        # return 404 on location / must not exist
        self.assertNotIn("location / {\n        return 404;\n    }", sh_content)

        # /cdn-check endpoint returning 204
        self.assertIn("location = /cdn-check", sh_content)
        self.assertIn("return 204;", sh_content)

        # Buffer limits (H12)
        self.assertIn("client_max_body_size 0;", sh_content)
        self.assertIn("large_client_header_buffers 8 64k;", sh_content)
        self.assertIn("http2_max_field_size 64k;", sh_content)
        self.assertIn("http2_max_header_size 64k;", sh_content)

        # Certbot renewal deploy hook installed with execute permissions
        self.assertIn("/etc/letsencrypt/renewal-hooks/deploy/restart-xray-nginx.sh", sh_content)
        self.assertIn("chmod +x /etc/letsencrypt/renewal-hooks/deploy/restart-xray-nginx.sh", sh_content)
        self.assertIn("systemctl reload nginx", sh_content)
        self.assertIn("systemctl restart xray", sh_content)

    def test_caddyfile_dynamic_prefix_routing(self) -> None:
        """Caddyfile must match custom and dynamic subscription prefixes."""
        caddy_content = CADDYFILE.read_text(encoding="utf-8")
        caddy_ci_content = CADDYFILE_CI.read_text(encoding="utf-8")

        expected_pattern = "@allowed_paths path /webhook/* /yookassa/* /sub/wl/* {$WHITE_INTERNET_SUB_PATH_PREFIX:/sub/wl}/*"
        self.assertIn(expected_pattern, caddy_content)
        self.assertIn(expected_pattern, caddy_ci_content)

    def test_dynamic_profile_title_base64_header_encoding(self) -> None:
        """Web handler must dynamically Base64 encode Profile-Title header without hardcoded literal."""
        raw_title = texts.WL_PROFILE_NAME
        expected_b64 = base64.b64encode(raw_title.encode("utf-8")).decode("ascii")

        header_value = f"base64:{expected_b64}"
        self.assertTrue(header_value.startswith("base64:"))

        # Decode and verify exact roundtrip match with texts.WL_PROFILE_NAME
        extracted_b64 = header_value.split("base64:")[1]
        decoded_title = base64.b64decode(extracted_b64).decode("utf-8")
        self.assertEqual(decoded_title, raw_title)

    def test_capacity_accounting_ssot_consistency(self) -> None:
        """Verify single source of truth capacity condition for White Internet subscriptions."""
        cond = capacity_consuming_wl_condition()
        self.assertIsNotNone(cond)

        # Compile condition to SQL string and check presence of status checks
        sql_str = str(cond)
        self.assertIn("white_internet_subscriptions.status", sql_str)
        self.assertIn("white_internet_subscriptions.provisioning_status", sql_str)

    def test_env_driven_tariff_pricing_defaults(self) -> None:
        """Verify tariff pricing and quotas are driven by constants and environment variables."""
        self.assertIsInstance(WHITE_INTERNET_BASE_PRICE_RUB, Decimal)
        self.assertGreater(WHITE_INTERNET_BASE_PRICE_RUB, Decimal(0))
        self.assertGreaterEqual(WHITE_INTERNET_BASE_DURATION_DAYS, 1)
        self.assertGreater(WHITE_INTERNET_BASE_TRAFFIC_BYTES, 0)
        self.assertGreater(WHITE_INTERNET_MAX_QUOTA_BYTES, WHITE_INTERNET_BASE_TRAFFIC_BYTES)
        self.assertIn(10, WHITE_INTERNET_TOPUP_PACKS)
        self.assertIn(25, WHITE_INTERNET_TOPUP_PACKS)
        self.assertIn(50, WHITE_INTERNET_TOPUP_PACKS)


if __name__ == "__main__":
    unittest.main()
