"""Tests for White Internet service layer, VLESS generation, and quota ledger invariants."""

import json
import unittest
import urllib.parse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from config.enums import WhiteInternetProvisioningStatus, WhiteInternetStatus
from database.models import WhiteInternetSubscription
from database.repositories import white_internet_repo
from services.white_internet_service import WhiteInternetService
from utils.datetime_helpers import calculate_extension_end, now_utc


class TestWhiteInternetVlessGeneration(unittest.TestCase):
    """Test VLESS XHTTP link formatting and compliance with Yandex CDN & INCY."""

    def test_generate_vless_links_format(self):
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.uuid = "11111111-2222-3333-4444-555555555555"
        cdn_domain = "cdn.just1k.online"

        links = WhiteInternetService.generate_vless_links(sub, cdn_domain)
        self.assertEqual(len(links), 1)

        link_wl = links[0]

        # Verify link structure
        self.assertTrue(link_wl.startswith(f"vless://{sub.uuid}@{cdn_domain}:443"))
        self.assertIn("type=xhttp", link_wl)
        self.assertIn("path=%2Fstream%2Fv1%2Fdefault", link_wl)
        self.assertIn("mode=packet-up", link_wl)
        self.assertIn("security=tls", link_wl)
        self.assertIn("fp=firefox", link_wl)
        self.assertIn("sni=cdn.just1k.online", link_wl)
        self.assertIn("Белый Интернет", urllib.parse.unquote(link_wl))

        # Decode and verify 'extra' parameters JSON
        parsed = urllib.parse.urlparse(link_wl)
        qs = urllib.parse.parse_qs(parsed.query)
        self.assertIn("extra", qs)

        extra_json = qs["extra"][0]
        extra = json.loads(extra_json)

        # Invariants from SSOT:
        self.assertEqual(extra["uplinkHTTPMethod"], "OPTIONS")
        self.assertTrue(extra["xPaddingObfsMode"])
        self.assertEqual(extra["xPaddingKey"], "dc")
        self.assertEqual(extra["xPaddingHeader"], "X-Cache")
        self.assertEqual(extra["xPaddingMethod"], "tokenish")
        self.assertEqual(extra["xPaddingPlacement"], "queryInHeader")


class TestWhiteInternetQuotaLedgerLogic(unittest.IsolatedAsyncioTestCase):
    """Test quota calculation, two-pool deduction, carryover, and cap enforcement."""

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
            base_traffic_bytes=50 * 1024**3,
            extra_traffic_bytes=25 * 1024**3,
            traffic_used_bytes=0,
            desired_version=1,
            actual_version=1,
        )

        mock_session = AsyncMock()
        mock_session.add = MagicMock()

        with patch(
            "database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub
        ):
            # 1. Deduct 40 GiB
            consumed, exhausted, overage = await white_internet_repo.deduct_traffic_atomic(
                mock_session, subscription_id=1, delta_bytes=40 * 1024**3, now=now
            )
            self.assertEqual(consumed, 40 * 1024**3)
            self.assertFalse(exhausted)
            self.assertEqual(overage, 0)
            self.assertEqual(sub.traffic_used_bytes, 40 * 1024**3)

            # 2. Deduct 20 GiB (total used 60 GiB against 75 GiB limit)
            consumed, exhausted, overage = await white_internet_repo.deduct_traffic_atomic(
                mock_session, subscription_id=1, delta_bytes=20 * 1024**3, now=now
            )
            self.assertEqual(consumed, 20 * 1024**3)
            self.assertFalse(exhausted)
            self.assertEqual(overage, 0)
            self.assertEqual(sub.traffic_used_bytes, 60 * 1024**3)

            # 3. Deduct 20 GiB when only 15 GiB remains (Overshoot scenario!)
            consumed, exhausted, overage = await white_internet_repo.deduct_traffic_atomic(
                mock_session, subscription_id=1, delta_bytes=20 * 1024**3, now=now
            )
            self.assertEqual(consumed, 15 * 1024**3)
            self.assertTrue(exhausted)
            self.assertEqual(overage, 5 * 1024**3)
            self.assertEqual(sub.traffic_used_bytes, 80 * 1024**3)
            self.assertEqual(sub.traffic_overage_bytes, 5 * 1024**3)
            self.assertEqual(sub.traffic_limit_bytes, 75 * 1024**3)
            self.assertEqual(sub.status, WhiteInternetStatus.EXHAUSTED)
            self.assertEqual(sub.desired_version, 2)

    async def test_topup_cap_150_gib_enforcement(self):
        now = now_utc()
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=1,
            token="test-token-12345",
            uuid="a2b9d4e1-73c5-4812-b964-f3e7b85a1902",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=15),
            base_traffic_bytes=50 * 1024**3,
            extra_traffic_bytes=90 * 1024**3,
            traffic_used_bytes=0,
            desired_version=1,
            actual_version=1,
        )

        mock_session = AsyncMock()
        mock_session.add = MagicMock()

        with patch(
            "database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub
        ):
            # Base (50) + Extra (90) + Pack (25) = 165 GiB > 150 GiB Hard Cap!
            with self.assertRaises(white_internet_repo.WhiteInternetQuotaCapExceededError):
                await white_internet_repo.topup_quota_atomic(
                    mock_session,
                    subscription_id=1,
                    quote_id=10,
                    pack_gb=25,
                    price_rub=Decimal("100.00"),
                )

            # Buying +10 GiB is allowed: 50 + 90 + 10 = 150 GiB <= 150 GiB
            await white_internet_repo.topup_quota_atomic(
                mock_session,
                subscription_id=1,
                quote_id=11,
                pack_gb=10,
                price_rub=Decimal("40.00"),
            )
            self.assertEqual(sub.extra_traffic_bytes, 100 * 1024**3)
            self.assertEqual(sub.traffic_limit_bytes, 150 * 1024**3)

    async def test_renew_subscription_resets_period_usage_and_preserves_carried_topup(self):
        """Renewal must reset period usage to 0, preserve node snapshots, and carry unused topup."""
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=1,
            token="test-token-12345",
            uuid="a2b9d4e1-73c5-4812-b964-f3e7b85a1902",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now - timedelta(days=29),
            expires_at=now + timedelta(days=1),
            base_traffic_bytes=50 * 1024**3,
            extra_traffic_bytes=25 * 1024**3,
            traffic_used_bytes=47 * 1024**3,
            traffic_overage_bytes=2 * 1024**3,
            traffic_uplink_bytes=18 * 1024**3,
            traffic_downlink_bytes=29 * 1024**3,
            last_uplink_snapshot=18 * 1024**3,
            last_downlink_snapshot=29 * 1024**3,
            traffic_stats_epoch="epoch-1",
            desired_version=2,
            actual_version=2,
        )

        mock_session = AsyncMock()
        mock_session.add = MagicMock()

        with patch(
            "database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub
        ):
            renewed = await white_internet_repo.renew_subscription_atomic(
                mock_session,
                subscription_id=1,
                quote_id=42,
                price_rub=Decimal("250.00"),
                duration_days=30,
                base_bytes=50 * 1024**3,
                now=now,
            )

            # Invariants:
            # 1. Period counters reset to 0
            self.assertEqual(renewed.traffic_used_bytes, 0)
            self.assertEqual(renewed.traffic_overage_bytes, 0)
            self.assertEqual(renewed.traffic_uplink_bytes, 0)
            self.assertEqual(renewed.traffic_downlink_bytes, 0)
            # Node snapshots and epoch are preserved as baseline
            self.assertEqual(renewed.last_uplink_snapshot, 18 * 1024**3)
            self.assertEqual(renewed.last_downlink_snapshot, 29 * 1024**3)
            self.assertEqual(renewed.traffic_stats_epoch, "epoch-1")

            # 2. Limit is fresh available quota (50 GiB base + 25 GiB carried extra = 75 GiB)
            self.assertEqual(renewed.base_traffic_bytes, 50 * 1024**3)
            self.assertEqual(renewed.extra_traffic_bytes, 25 * 1024**3)
            self.assertEqual(renewed.traffic_limit_bytes, 75 * 1024**3)

    def test_generate_full_xray_config_outbounds_consistency(self):
        """Verify that every outboundTag in routing.rules exists in outbounds list."""
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            token="test-token",
            uuid="a2b9d4e1-73c5-4812-b964-f3e7b85a1902",
        )
        cfg = WhiteInternetService.generate_full_xray_config(sub, cdn_domain="cdn.example.test")
        outbound_tags = {ob["tag"] for ob in cfg["outbounds"]}

        for rule in cfg["routing"]["rules"]:
            tag = rule.get("outboundTag")
            if tag:
                self.assertIn(
                    tag,
                    outbound_tags,
                    f"Routing rule references undefined outboundTag: {tag}",
                )


class TestWhiteInternetExtension(unittest.IsolatedAsyncioTestCase):
    """Test White Internet subscription duration extension."""

    async def test_extend_subscription_atomic_active(self):
        """Extending active subscription adds days to current expires_at and bumps desired_version."""
        now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        current_expires = now + timedelta(days=5)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            status=WhiteInternetStatus.ACTIVE,
            expires_at=current_expires,
            desired_version=1,
            actual_version=1,
        )

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sub
        mock_session.execute.return_value = mock_result

        extended = await white_internet_repo.extend_subscription_atomic(
            mock_session,
            subscription_id=1,
            days=30,
            now=now,
        )

        self.assertEqual(extended.expires_at, current_expires + timedelta(days=30))
        self.assertEqual(extended.desired_version, 2)
        self.assertFalse(extended.notified_3d)
        mock_session.flush.assert_awaited_once()

    async def test_extend_subscription_atomic_consecutive_active(self):
        """Consecutive extensions on active subscription monotonically increment desired_version."""
        now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        current_expires = now + timedelta(days=5)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            status=WhiteInternetStatus.ACTIVE,
            expires_at=current_expires,
            desired_version=1,
            actual_version=1,
        )

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sub
        mock_session.execute.return_value = mock_result

        # First extension: +30 days -> version 2
        ext1 = await white_internet_repo.extend_subscription_atomic(
            mock_session,
            subscription_id=1,
            days=30,
            now=now,
        )
        self.assertEqual(ext1.desired_version, 2)
        self.assertEqual(ext1.expires_at, current_expires + timedelta(days=30))

        # Second extension: +30 days -> version 3, expires_at +60 days
        ext2 = await white_internet_repo.extend_subscription_atomic(
            mock_session,
            subscription_id=1,
            days=30,
            now=now,
        )
        self.assertEqual(ext2.desired_version, 3)
        self.assertEqual(ext2.expires_at, current_expires + timedelta(days=60))

    async def test_extend_subscription_atomic_expired(self):
        """Extending expired subscription resets to ACTIVE, zeroes traffic and counts days from now."""
        now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        old_expires = now - timedelta(days=2)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            status=WhiteInternetStatus.EXPIRED,
            expires_at=old_expires,
            desired_version=1,
            actual_version=1,
            traffic_used_bytes=50 * 1024 * 1024 * 1024,
            traffic_overage_bytes=1024,
            traffic_uplink_bytes=20 * 1024 * 1024 * 1024,
            traffic_downlink_bytes=30 * 1024 * 1024 * 1024,
            notified_90p=True,
        )

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sub
        mock_session.execute.return_value = mock_result

        extended = await white_internet_repo.extend_subscription_atomic(
            mock_session,
            subscription_id=1,
            days=7,
            now=now,
        )

        self.assertEqual(extended.expires_at, now + timedelta(days=7))
        self.assertEqual(extended.status, WhiteInternetStatus.ACTIVE)
        self.assertEqual(extended.traffic_used_bytes, 0)
        self.assertEqual(extended.traffic_overage_bytes, 0)
        self.assertEqual(extended.traffic_uplink_bytes, 0)
        self.assertEqual(extended.traffic_downlink_bytes, 0)
        self.assertFalse(extended.notified_90p)
        self.assertEqual(extended.desired_version, 2)
        mock_session.flush.assert_awaited_once()

    async def test_extend_subscription_atomic_exhausted(self):
        """Extending EXHAUSTED subscription resets traffic counters, transitions to ACTIVE and increments desired_version."""
        now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            status=WhiteInternetStatus.EXHAUSTED,
            expires_at=now + timedelta(days=5),
            desired_version=1,
            actual_version=1,
            traffic_used_bytes=50 * 1024 * 1024 * 1024,
            traffic_overage_bytes=1024,
            traffic_uplink_bytes=20 * 1024 * 1024 * 1024,
            traffic_downlink_bytes=30 * 1024 * 1024 * 1024,
            notified_90p=True,
        )

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sub
        mock_session.execute.return_value = mock_result

        extended = await white_internet_repo.extend_subscription_atomic(
            mock_session,
            subscription_id=1,
            days=30,
            now=now,
        )

        self.assertEqual(extended.status, WhiteInternetStatus.ACTIVE)
        self.assertIsNone(extended.status_reason)
        self.assertEqual(extended.provisioning_status, WhiteInternetProvisioningStatus.PENDING_UPDATE)
        self.assertEqual(extended.traffic_used_bytes, 0)
        self.assertEqual(extended.traffic_overage_bytes, 0)
        self.assertEqual(extended.traffic_uplink_bytes, 0)
        self.assertEqual(extended.traffic_downlink_bytes, 0)
        self.assertFalse(extended.notified_90p)
        self.assertEqual(extended.desired_version, 2)
        self.assertEqual(extended.expires_at, now + timedelta(days=35))
        mock_session.flush.assert_awaited_once()

    async def test_extend_subscription_atomic_active_past_expiry_resets_traffic(self):
        """Extending an ACTIVE subscription that already lapsed calendar-wise resets traffic counters."""
        now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        lapsed_expires = now - timedelta(minutes=5)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            status=WhiteInternetStatus.ACTIVE,
            expires_at=lapsed_expires,
            desired_version=1,
            actual_version=1,
            traffic_used_bytes=45 * 1024 * 1024 * 1024,
            traffic_overage_bytes=1024,
            traffic_uplink_bytes=20 * 1024 * 1024 * 1024,
            traffic_downlink_bytes=25 * 1024 * 1024 * 1024,
            notified_90p=True,
        )

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sub
        mock_session.execute.return_value = mock_result

        extended = await white_internet_repo.extend_subscription_atomic(
            mock_session,
            subscription_id=1,
            days=30,
            now=now,
        )

        self.assertEqual(extended.status, WhiteInternetStatus.ACTIVE)
        self.assertEqual(extended.traffic_used_bytes, 0)
        self.assertEqual(extended.traffic_overage_bytes, 0)
        self.assertEqual(extended.traffic_uplink_bytes, 0)
        self.assertEqual(extended.traffic_downlink_bytes, 0)
        self.assertFalse(extended.notified_90p)
        self.assertEqual(extended.desired_version, 2)
        self.assertEqual(extended.expires_at, now + timedelta(days=30))
        mock_session.flush.assert_awaited_once()

    async def test_extend_subscription_atomic_active_preserves_notified_90p(self):
        """Extending an ACTIVE subscription before expiry preserves mid-cycle notified_90p state."""
        now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        current_expires = now + timedelta(days=10)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            status=WhiteInternetStatus.ACTIVE,
            expires_at=current_expires,
            desired_version=1,
            actual_version=1,
            traffic_used_bytes=95 * 1024 * 1024 * 1024,
            traffic_limit_bytes=100 * 1024 * 1024 * 1024,
            notified_90p=True,
            notified_3d=True,
        )

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sub
        mock_session.execute.return_value = mock_result

        extended = await white_internet_repo.extend_subscription_atomic(
            mock_session,
            subscription_id=1,
            days=30,
            now=now,
        )

        self.assertEqual(extended.status, WhiteInternetStatus.ACTIVE)
        self.assertEqual(extended.expires_at, current_expires + timedelta(days=30))
        self.assertEqual(extended.traffic_used_bytes, 95 * 1024 * 1024 * 1024)
        self.assertTrue(extended.notified_90p, "Mid-cycle active extension must NOT reset notified_90p")
        self.assertFalse(extended.notified_3d, "Calendar notifications must be reset for the new end date")
        mock_session.flush.assert_awaited_once()

    async def test_extend_subscription_service_sync(self):
        """WhiteInternetService.extend_subscription calls repo and inline node sync."""
        mock_session = AsyncMock()
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            status=WhiteInternetStatus.ACTIVE,
            expires_at=now_utc() + timedelta(days=10),
            origin_node_id=3,
        )
        origin_node = MagicMock(is_active=True)
        mock_session.get.return_value = origin_node

        with patch("database.repositories.white_internet_repo.get_subscription_by_user_id", new=AsyncMock(return_value=sub)), \
             patch("database.repositories.white_internet_repo.extend_subscription_atomic", new=AsyncMock(return_value=sub)) as mock_repo_extend, \
             patch.object(WhiteInternetService, "_try_inline_sync", new=AsyncMock(return_value=True)) as mock_sync:

            ok, msg, res_sub = await WhiteInternetService.extend_subscription(mock_session, user_id=10, days=30)
            self.assertTrue(ok)
            self.assertEqual(res_sub.id, 1)
            mock_repo_extend.assert_awaited_once_with(mock_session, 1, 30)
            mock_session.commit.assert_awaited_once()
            mock_sync.assert_awaited_once()


class TestCalculateExtensionEnd(unittest.TestCase):
    """Test unified extension end calculation helper."""

    def test_active_expiry_extended(self):
        now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        current = now + timedelta(days=5)
        res = calculate_extension_end(current, 30, now=now)
        self.assertEqual(res, current + timedelta(days=30))

    def test_expired_expiry_extended_from_now(self):
        now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        current = now - timedelta(days=5)
        res = calculate_extension_end(current, 10, now=now)
        self.assertEqual(res, now + timedelta(days=10))

    def test_none_expiry_extended_from_now(self):
        now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        res = calculate_extension_end(None, 7, now=now)
        self.assertEqual(res, now + timedelta(days=7))

    def test_permanent_duration_returns_permanent_end_date(self):
        from config.constants import PERMANENT_END_DATE, PERMANENT_SUBSCRIPTION_DAYS
        now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        res = calculate_extension_end(now, PERMANENT_SUBSCRIPTION_DAYS, now=now)
        self.assertEqual(res, PERMANENT_END_DATE)
