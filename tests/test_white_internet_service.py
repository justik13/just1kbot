"""Tests for White Internet service layer, VLESS generation, and quota ledger invariants."""

import json
import unittest
import urllib.parse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from config.enums import (
    WhiteInternetGrantType,
    WhiteInternetStatus,
)
from database.models import (
    WhiteInternetQuotaGrant,
    WhiteInternetSubscription,
)
from database.repositories import white_internet_repo
from services.white_internet_service import WhiteInternetService


class TestWhiteInternetVlessGeneration(unittest.TestCase):
    """Test VLESS XHTTP link formatting and compliance with Yandex CDN & INCY."""

    def test_generate_vless_links_format(self):
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.uuid = "a2b9d4e1-73c5-4812-b964-f3e7b85a1902"
        cdn_domain = "cdn.just1k.online"

        links = WhiteInternetService.generate_vless_links(sub, cdn_domain)
        self.assertEqual(len(links), 2)

        link_de, link_nl = links

        # Verify DE link
        self.assertTrue(link_de.startswith(f"vless://{sub.uuid}@{cdn_domain}:443"))
        self.assertIn("type=xhttp", link_de)
        self.assertIn("path=%2Fapi%2Fv3%2Fde", link_de)
        self.assertIn("mode=packet-up", link_de)
        self.assertIn("security=tls", link_de)
        self.assertIn("sni=cdn.just1k.online", link_de)
        self.assertIn("Германия", urllib.parse.unquote(link_de))

        # Verify NL link
        self.assertTrue(link_nl.startswith(f"vless://{sub.uuid}@{cdn_domain}:443"))
        self.assertIn("path=%2Fapi%2Fv3%2Fnl", link_nl)
        self.assertIn("Нидерланды", urllib.parse.unquote(link_nl))

        # Decode and verify 'extra' parameters JSON
        parsed_de = urllib.parse.urlparse(link_de)
        qs_de = urllib.parse.parse_qs(parsed_de.query)
        self.assertIn("extra", qs_de)

        extra_json = qs_de["extra"][0]
        extra = json.loads(extra_json)

        # Invariants from SSOT:
        self.assertEqual(extra["uplinkHTTPMethod"], "OPTIONS")
        self.assertTrue(extra["xPaddingObfsMode"])
        self.assertEqual(extra["xPaddingKey"], "dc")
        self.assertEqual(extra["xPaddingHeader"], "X-Cache")
        self.assertEqual(extra["xPaddingMethod"], "tokenish")
        self.assertEqual(extra["xPaddingPlacement"], "header")

    def test_generate_amnezia_vpn_key_roundtrip_decompression(self):
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.uuid = "a2b9d4e1-73c5-4812-b964-f3e7b85a1902"
        cdn_domain = "cdn.just1k.online"

        key = WhiteInternetService.generate_amnezia_vpn_key(sub, cdn_domain)
        self.assertTrue(key.startswith("vpn://"))

        # Decompress and decode using qUncompress format
        payload = WhiteInternetService.decode_amnezia_vpn_key(key)

        self.assertIn("containers", payload)
        self.assertEqual(len(payload["containers"]), 1)
        container = payload["containers"][0]
        self.assertEqual(container["container"], "amnezia-xray")
        self.assertTrue(container["xray"]["isThirdPartyConfig"])

        # Validate extracted Xray config
        last_config = json.loads(container["xray"]["last_config"])
        self.assertIn("outbounds", last_config)
        self.assertIn("routing", last_config)
        self.assertEqual(payload["defaultContainer"], "amnezia-xray")
        self.assertEqual(payload["hostName"], cdn_domain)


class TestWhiteInternetQuotaLedgerLogic(unittest.IsolatedAsyncioTestCase):
    """Test quota calculation, Base-first deduction, carryover, and cap enforcement."""

    async def test_deduct_traffic_base_first_and_overshoot(self):
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=1,
            token="test-token-12345",
            uuid="a2b9d4e1-73c5-4812-b964-f3e7b85a1902",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=75 * 1024**3,
            traffic_used_bytes=0,
            desired_version=1,
            actual_version=1,
        )

        grant_base = WhiteInternetQuotaGrant(
            id=101,
            subscription_id=sub.id,
            grant_type=WhiteInternetGrantType.BASE,
            bytes_granted=50 * 1024**3,
            bytes_remaining=50 * 1024**3,
            price_rub=Decimal("250.00"),
            quote_id=1,
            expires_at=sub.expires_at,
            created_at=now,
        )

        grant_topup = WhiteInternetQuotaGrant(
            id=102,
            subscription_id=sub.id,
            grant_type=WhiteInternetGrantType.TOPUP,
            bytes_granted=25 * 1024**3,
            bytes_remaining=25 * 1024**3,
            price_rub=Decimal("100.00"),
            quote_id=2,
            expires_at=sub.expires_at,
            created_at=now + timedelta(minutes=5),
        )

        mock_session = AsyncMock()
        mock_session.add = MagicMock()

        # Mock repository lookups
        with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub):
            with patch(
                "database.repositories.white_internet_repo.get_active_grants_for_deduction",
                return_value=[grant_base, grant_topup],
            ):
                with patch(
                    "database.repositories.white_internet_repo.get_available_quota_bytes",
                    side_effect=[
                        (50 - 40 + 25) * 1024**3,  # after 1st deduction: 35 GiB
                        (10 - 10 + 25 - 10) * 1024**3,  # after 2nd deduction: 15 GiB
                        0,  # after 3rd deduction: 0 GiB (exhausted)
                    ],
                ):
                    # 1. Deduct 40 GiB (BASE grant should be consumed first)
                    consumed, exhausted, overage = await white_internet_repo.deduct_traffic_atomic(
                        mock_session, subscription_id=1, delta_bytes=40 * 1024**3, now=now
                    )
                    self.assertEqual(consumed, 40 * 1024**3)
                    self.assertFalse(exhausted)
                    self.assertEqual(overage, 0)
                    self.assertEqual(grant_base.bytes_remaining, 10 * 1024**3)
                    self.assertEqual(grant_topup.bytes_remaining, 25 * 1024**3)
                    self.assertEqual(sub.traffic_used_bytes, 40 * 1024**3)

                    # 2. Deduct 20 GiB (remaining 10 GiB BASE + 10 GiB from TOPUP)
                    consumed, exhausted, overage = await white_internet_repo.deduct_traffic_atomic(
                        mock_session, subscription_id=1, delta_bytes=20 * 1024**3, now=now
                    )
                    self.assertEqual(consumed, 20 * 1024**3)
                    self.assertFalse(exhausted)
                    self.assertEqual(overage, 0)
                    self.assertEqual(grant_base.bytes_remaining, 0)
                    self.assertEqual(grant_topup.bytes_remaining, 15 * 1024**3)
                    self.assertEqual(sub.traffic_used_bytes, 60 * 1024**3)

                    # 3. Deduct 20 GiB when only 15 GiB remains (Overshoot scenario!)
                    consumed, exhausted, overage = await white_internet_repo.deduct_traffic_atomic(
                        mock_session, subscription_id=1, delta_bytes=20 * 1024**3, now=now
                    )
                    self.assertEqual(consumed, 15 * 1024**3)
                    self.assertTrue(exhausted)
                    self.assertEqual(overage, 5 * 1024**3)
                    self.assertEqual(grant_base.bytes_remaining, 0)
                    self.assertEqual(grant_topup.bytes_remaining, 0)
                    # Full actual traffic recorded
                    self.assertEqual(sub.traffic_used_bytes, 80 * 1024**3)
                    self.assertEqual(sub.status, WhiteInternetStatus.EXHAUSTED)
                    self.assertEqual(sub.desired_version, 2)

    async def test_topup_cap_500_gib_enforcement(self):
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=1,
            token="test-token-12345",
            uuid="a2b9d4e1-73c5-4812-b964-f3e7b85a1902",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=15),
            traffic_limit_bytes=480 * 1024**3,
            traffic_used_bytes=0,
            desired_version=1,
            actual_version=1,
        )

        mock_session = AsyncMock()
        mock_session.add = MagicMock()

        # Available is 480 GiB
        with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub):
            with patch(
                "database.repositories.white_internet_repo.get_available_quota_bytes",
                return_value=480 * 1024**3,
            ):
                # Buying +50 GiB would result in 480 + 50 = 530 GiB > 500 GiB cap!
                with self.assertRaises(white_internet_repo.WhiteInternetQuotaCapExceededError):
                    await white_internet_repo.topup_quota_atomic(
                        mock_session,
                        subscription_id=1,
                        quote_id=10,
                        pack_gb=50,
                        price_rub=Decimal("200.00"),
                    )

                # Buying +10 GiB is allowed: 480 + 10 = 490 GiB <= 500 GiB
                grant = await white_internet_repo.topup_quota_atomic(
                    mock_session,
                    subscription_id=1,
                    quote_id=11,
                    pack_gb=10,
                    price_rub=Decimal("40.00"),
                )
                self.assertEqual(grant.bytes_granted, 10 * 1024**3)
                self.assertEqual(grant.bytes_remaining, 10 * 1024**3)
                self.assertEqual(grant.expires_at, sub.expires_at)
