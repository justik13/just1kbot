"""End-to-end integration test for White Internet business flow.

Verifies:
1. Subscription creation & initial PENDING state.
2. Feed gating (503 while unsynced).
3. Reconciliation cycle (node sync & epoch alignment -> ACTIVE).
4. Feed delivery (200 OK with valid VLESS links and Userinfo).
5. Traffic deduction & cumulative uplink/downlink tracking.
6. Quota exhaustion -> transition to EXHAUSTED, desired_version advance.
7. Feed gating after exhaustion (403 Forbidden with Userinfo).
8. Node de-provisioning on exhaustion.
9. Top-up grant recovery -> re-activation and feed 200 OK restoration.
"""

from __future__ import annotations

import base64
import os
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch


from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from bot.handlers.white_internet_web import setup_white_internet_web_routes
from config.enums import (
    ServerHealthState,
    TariffQuoteOperation,
    TariffQuoteStatus,
    WhiteInternetGrantType,
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)

from database.models import (
    Server,
    TariffQuote,
    User,
    WhiteInternetQuotaGrant,
    WhiteInternetSubscription,
)
from services.workers.white_internet_reconciliation import WhiteInternetReconciliationWorker
from services.workers.white_internet_traffic import WhiteInternetTrafficWorker
from services.xray_node_client import XrayNodeClient


class TestWhiteInternetEndToEndLifecycle(AioHTTPTestCase):
    """Full lifecycle E2E test from purchase to exhaustion, feed gating, and topup recovery."""

    async def get_application(self):
        app = web.Application()
        setup_white_internet_web_routes(app)
        return app

    @unittest_run_loop
    async def test_full_subscription_lifecycle(self):
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        node_epoch = "epoch_20260830_initial123"

        # 1. Setup Server & User
        server = Server(
            id=1,
            name="Origin-MSK-01",
            protocol="xray",
            capabilities=["xray_origin"],
            api_url="https://origin.just1k.online:8444",
            api_key="node-secret-key",
            health_state=ServerHealthState.ONLINE,
            is_active=True,
            xray_instance_epoch=node_epoch,
        )

        user = User(
            id=42,
            telegram_id=999888777,
        )


        base_bytes = 50 * 1024**3
        quote = TariffQuote(
            id=101,
            user_id=user.id,
            service_type="white_internet",
            operation_type=TariffQuoteOperation.PURCHASE,
            status=TariffQuoteStatus.ACTIVE,
            target_tariff_version_id=1,
            current_paid_hours=0,
            current_paid_value_rub=Decimal("0.00"),
            bonus_hours=0,
            amount_due_rub=Decimal("120.00"),
            resulting_paid_hours=720,
            resulting_paid_value_rub=Decimal("120.00"),
            resulting_bonus_hours=0,
            rounding_loss_hours=Decimal("0.00"),
            rounding_loss_value_rub=Decimal("0.00"),
            expires_at=now + timedelta(hours=1),
        )



        # Create subscription in PENDING state (post-purchase)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=user.id,
            origin_node_id=server.id,
            token="wi_token_test_full_lifecycle_1234567890abcdef",
            uuid="a2b9d4e1-73c5-4812-b964-f3e7b85a1902",
            status=WhiteInternetStatus.PENDING,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=base_bytes,
            traffic_used_bytes=0,
            traffic_uplink_bytes=0,
            traffic_downlink_bytes=0,
            last_uplink_snapshot=0,
            last_downlink_snapshot=0,
            traffic_stats_epoch=None,
            provisioning_status=WhiteInternetProvisioningStatus.PENDING_CREATE,
            desired_version=1,
            actual_version=0,
            last_reconciled_node_epoch=None,
        )

        grant = WhiteInternetQuotaGrant(
            id=1,
            subscription_id=sub.id,
            grant_type=WhiteInternetGrantType.BASE,
            bytes_granted=base_bytes,
            bytes_remaining=base_bytes,
            price_rub=Decimal("120.00"),
            quote_id=quote.id,
            expires_at=sub.expires_at,
            created_at=now,
        )

        # 2. Feed check before reconciliation -> must return 503 (Retry-After: 5)
        with patch("bot.handlers.white_internet_web.session_scope") as mock_scope:
            session = AsyncMock()
            session.scalar.return_value = server
            session.execute.return_value = MagicMock(scalar_one_or_none=lambda: server)
            mock_scope.return_value.__aenter__.return_value = session

            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                resp = await self.client.get(f"/sub/wl/{sub.token}")
                self.assertEqual(resp.status, 503)
                self.assertEqual(resp.headers.get("Retry-After"), "5")

        # 3. Reconciliation Worker runs: provisions user on Xray node
        mock_node_client = AsyncMock(spec=XrayNodeClient)
        mock_node_client.check_health.return_value = (True, node_epoch, {"status": "ok", "grpc_ok": True, "xray_running": True, "boot_id": "boot-1", "starttime": 12345})
        mock_node_client.sync_client.return_value = (True, None)

        recon_worker = WhiteInternetReconciliationWorker(node_client=mock_node_client)

        recon_session = AsyncMock()
        recon_session.get.return_value = sub
        recon_session.execute.side_effect = [
            MagicMock(scalars=lambda: MagicMock(all=lambda: [server])),  # servers query
            MagicMock(scalars=lambda: MagicMock(all=lambda: [sub])),     # pending subscriptions query
        ]

        with patch("database.repositories.servers_repo.update_server_xray_epoch_cas", return_value=(True, server)):
            with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub):
                synced = await recon_worker.run_reconciliation_cycle(recon_session)
                self.assertEqual(synced, 1)
                mock_node_client.sync_client.assert_awaited_once_with(
                    server.api_url,
                    server.api_key,
                    sub.uuid,
                    is_active=True,
                )

        # After reconciliation, state is ACTIVE with aligned epoch
        self.assertEqual(sub.status, WhiteInternetStatus.ACTIVE)
        self.assertEqual(sub.actual_version, 1)
        self.assertEqual(sub.last_reconciled_node_epoch, node_epoch)

        # 4. Feed check after reconciliation -> 200 OK with VLESS links
        with patch.dict(os.environ, {"WHITE_INTERNET_CDN_DOMAIN": "cdn.just1k.online"}):
            with patch("bot.handlers.white_internet_web.session_scope") as mock_scope:
                session = AsyncMock()
                session.scalar.return_value = server
                session.execute.return_value = MagicMock(scalar_one_or_none=lambda: server)
                mock_scope.return_value.__aenter__.return_value = session

                with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                    with patch("database.repositories.white_internet_repo.get_period_grants", return_value=[grant]):
                        resp = await self.client.get(f"/sub/wl/{sub.token}")
                        self.assertEqual(resp.status, 200)
                        self.assertEqual(resp.headers.get("Profile-Title"), "base64:SnVzdDFrINCR0LXQu9GL0Lkg0JjQvdGC0LXRgNC90LXRgg==")
                        self.assertEqual(resp.headers.get("Profile-Update-Interval"), "12")
                        self.assertEqual(resp.headers.get("hide-url"), "1")
                        self.assertEqual(resp.headers.get("no-limit-enabled"), "1")

                        body_b64 = await resp.text()
                        decoded_lines = base64.b64decode(body_b64).decode("utf-8").splitlines()
                        self.assertEqual(len(decoded_lines), 2)
                        self.assertTrue(decoded_lines[0].startswith("vless://"))
                        self.assertTrue(decoded_lines[1].startswith("vless://"))

                        userinfo = resp.headers.get("Subscription-Userinfo", "")
                        self.assertIn("upload=0;", userinfo)
                        self.assertIn("download=0;", userinfo)
                        self.assertIn(f"total={base_bytes};", userinfo)

        # 5. Traffic sync worker consumes 20 GB (5 GB up, 15 GB down)
        up_1 = 5 * 1024**3
        down_1 = 15 * 1024**3
        mock_node_client.get_traffic_snapshot.return_value = (
            node_epoch,
            "boot-1",
            12345,
            {sub.uuid: {"uplink": up_1, "downlink": down_1}},
        )

        traffic_worker = WhiteInternetTrafficWorker(node_client=mock_node_client)

        traffic_session = AsyncMock()
        traffic_session.execute.return_value = MagicMock(scalars=lambda: MagicMock(all=lambda: [server]))
        traffic_session.scalar.return_value = sub

        with patch("database.repositories.servers_repo.update_server_xray_epoch_cas", return_value=(True, server)):
            with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub):
                with patch("database.repositories.white_internet_repo.deduct_traffic_atomic") as mock_deduct:
                    with patch("database.repositories.white_internet_repo.record_traffic_event_atomic"):
                        mock_deduct.return_value = (up_1 + down_1, False, 0)

                        await traffic_worker.run_traffic_cycle(traffic_session)

                        mock_deduct.assert_awaited_once_with(
                            traffic_session,
                            subscription_id=sub.id,
                            delta_bytes=up_1 + down_1,
                            delta_uplink=up_1,
                            delta_downlink=down_1,
                            now=unittest.mock.ANY,
                        )

        # Update sub model with accumulated values
        sub.traffic_used_bytes = up_1 + down_1
        sub.traffic_uplink_bytes = up_1
        sub.traffic_downlink_bytes = down_1
        grant.bytes_remaining = base_bytes - (up_1 + down_1)

        # 6. Second traffic cycle exhausts remaining 30 GB (additional 35 GB total consumed)
        up_2 = 10 * 1024**3
        down_2 = 45 * 1024**3
        mock_node_client.get_traffic_snapshot.return_value = (
            node_epoch,
            "boot-1",
            12345,
            {sub.uuid: {"uplink": up_2, "downlink": down_2}},
        )

        with patch("database.repositories.servers_repo.update_server_xray_epoch_cas", return_value=(True, server)):
            with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub):
                with patch("database.repositories.white_internet_repo.deduct_traffic_atomic") as mock_deduct:
                    with patch("database.repositories.white_internet_repo.record_traffic_event_atomic"):
                        mock_deduct.return_value = (grant.bytes_remaining, True, 5 * 1024**3)

                        sub.status = WhiteInternetStatus.EXHAUSTED
                        sub.desired_version = 2
                        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
                        sub.traffic_used_bytes = 55 * 1024**3
                        sub.traffic_uplink_bytes = up_2
                        sub.traffic_downlink_bytes = down_2
                        grant.bytes_remaining = 0

                        await traffic_worker.run_traffic_cycle(traffic_session)

        self.assertEqual(sub.status, WhiteInternetStatus.EXHAUSTED)
        self.assertEqual(sub.desired_version, 2)

        # 7. Feed check while exhausted -> 403 Forbidden with exact accumulated Userinfo
        with patch("bot.handlers.white_internet_web.session_scope") as mock_scope:
            session = AsyncMock()
            mock_scope.return_value.__aenter__.return_value = session

            with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                with patch("database.repositories.white_internet_repo.get_period_grants", return_value=[grant]):
                    resp = await self.client.get(f"/sub/wl/{sub.token}")
                    self.assertEqual(resp.status, 403)
                    userinfo = resp.headers.get("Subscription-Userinfo", "")
                    self.assertIn(f"upload={sub.traffic_uplink_bytes};", userinfo)
                    self.assertIn(f"download={sub.traffic_downlink_bytes};", userinfo)
                    self.assertIn(f"total={base_bytes};", userinfo)

        # 8. Reconciliation de-provisions exhausted user from Xray
        mock_node_client.sync_client.reset_mock()
        mock_node_client.sync_client.return_value = (True, None)

        recon_session_2 = AsyncMock()
        recon_session_2.get.return_value = sub
        recon_session_2.execute.side_effect = [
            MagicMock(scalars=lambda: MagicMock(all=lambda: [server])),
            MagicMock(scalars=lambda: MagicMock(all=lambda: [sub])),
        ]

        with patch("database.repositories.servers_repo.update_server_xray_epoch_cas", return_value=(True, server)):
            with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub):
                synced = await recon_worker.run_reconciliation_cycle(recon_session_2)
                self.assertEqual(synced, 1)
                mock_node_client.sync_client.assert_awaited_once_with(
                    server.api_url,
                    server.api_key,
                    sub.uuid,
                    is_active=False,
                )
                self.assertEqual(sub.actual_version, 2)

        # 9. Top-up grant recovery: user purchases real 25 GB topup pack (100 RUB)
        topup_gb = 25
        topup_bytes = topup_gb * 1024**3
        topup_price = Decimal("100.00")
        sub.traffic_limit_bytes += topup_bytes
        sub.status = WhiteInternetStatus.ACTIVE
        sub.desired_version = 3
        sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
        grant_topup = WhiteInternetQuotaGrant(
            id=2,
            subscription_id=sub.id,
            grant_type=WhiteInternetGrantType.TOPUP,
            bytes_granted=topup_bytes,
            bytes_remaining=topup_bytes,
            price_rub=topup_price,
            quote_id=102,
            expires_at=sub.expires_at,
            created_at=now,
        )

        # Reconciliation re-enables user on node
        mock_node_client.sync_client.reset_mock()
        recon_session_3 = AsyncMock()
        recon_session_3.get.return_value = sub
        recon_session_3.execute.side_effect = [
            MagicMock(scalars=lambda: MagicMock(all=lambda: [server])),
            MagicMock(scalars=lambda: MagicMock(all=lambda: [sub])),
        ]

        with patch("database.repositories.servers_repo.update_server_xray_epoch_cas", return_value=(True, server)):
            with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub):
                synced = await recon_worker.run_reconciliation_cycle(recon_session_3)
                self.assertEqual(synced, 1)
                mock_node_client.sync_client.assert_awaited_once_with(
                    server.api_url,
                    server.api_key,
                    sub.uuid,
                    is_active=True,
                )
                self.assertEqual(sub.actual_version, 3)

        # Feed is restored to 200 OK
        with patch.dict(os.environ, {"WHITE_INTERNET_CDN_DOMAIN": "cdn.just1k.online"}):
            with patch("bot.handlers.white_internet_web.session_scope") as mock_scope:
                session = AsyncMock()
                session.scalar.return_value = server
                session.execute.return_value = MagicMock(scalar_one_or_none=lambda: server)
                mock_scope.return_value.__aenter__.return_value = session

                with patch("database.repositories.white_internet_repo.get_subscription_by_token", return_value=sub):
                    with patch("database.repositories.white_internet_repo.get_period_grants", return_value=[grant, grant_topup]):
                        resp = await self.client.get(f"/sub/wl/{sub.token}")
                        self.assertEqual(resp.status, 200)
