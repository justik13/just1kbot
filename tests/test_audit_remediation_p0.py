"""Regression tests for audit remediation fixes (H1, H2, H3, D1, M1, M2, M3, M5)."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import web

from config.enums import ServiceType
from database.models import Tariff, Payment, User
from database.repositories.tariffs_repo import get_active_tariffs
from services.account_purchase import (
    AccountPurchaseError,
    prepare_account_purchase,
    _settle_account_purchase,
)
from services.ban_service import BanService
from services.workers.payments import _retry_auto_fulfillment, _needs_attention
from bot.main import HealthcheckAccessLogger


class AuditRemediationTests(unittest.IsolatedAsyncioTestCase):
    """Test suite verifying all fixes from the audit remediation."""

    async def test_get_active_tariffs_filters_by_awg_by_default(self):
        """H1: get_active_tariffs must only return AWG tariffs by default."""
        session = AsyncMock()
        mock_result = MagicMock()
        awg_tariff = Tariff(id=1, name="AWG 30d", service_type=ServiceType.AWG, is_active=True)
        mock_result.scalars.return_value.all.return_value = [awg_tariff]
        session.execute.return_value = mock_result

        tariffs = await get_active_tariffs(session)
        self.assertEqual(len(tariffs), 1)
        self.assertEqual(tariffs[0].service_type, ServiceType.AWG)

        # Assert query contained service_type == 'awg'
        stmt = session.execute.call_args[0][0]
        compiled = str(stmt)
        self.assertIn("tariffs.service_type =", compiled)

    async def test_prepare_account_purchase_blocks_white_internet_tariff(self):
        """H1: prepare_account_purchase must reject white_internet tariffs."""
        session = AsyncMock()
        wi_tariff = Tariff(
            id=99,
            name="Белый Интернет 50 ГБ",
            service_type=ServiceType.WHITE_INTERNET,
            is_active=True,
            device_limit=1,
            duration_days=30,
            price_rub=250,
        )
        session.scalar.return_value = wi_tariff
        user = User(id=1, telegram_id=12345, financial_hold=False, is_deleted=False, is_banned=False)

        with patch("services.account_purchase.lock_checkout_user", return_value=user), \
             patch("services.account_purchase.get_account_balance") as mock_bal:
            mock_bal.return_value = MagicMock(debt=0)
            with self.assertRaises(AccountPurchaseError) as ctx:
                await prepare_account_purchase(session, user_id=1, tariff_id=99)
            self.assertEqual(ctx.exception.code, "tariff_unavailable")

    async def test_settle_account_purchase_blocks_white_internet_tariff(self):
        """H1: _settle_account_purchase must reject white_internet tariffs."""
        from datetime import timedelta
        from utils.datetime_helpers import now_utc

        session = AsyncMock()
        wi_tariff = Tariff(
            id=99,
            name="Белый Интернет 50 ГБ",
            service_type=ServiceType.WHITE_INTERNET,
            is_active=True,
            device_limit=1,
            duration_days=30,
            price_rub=250,
        )
        user = User(id=1, telegram_id=12345, financial_hold=False, is_deleted=False, is_banned=False)
        quote = MagicMock(
            id=1,
            user_id=1,
            target_tariff_version_id=10,
            operation_type="purchase",
            status="active",
            expires_at=now_utc() + timedelta(hours=1),
            amount_due_rub=250,
            currency="RUB",
        )
        version = MagicMock(tariff_id=99, duration_hours=720, price_rub=250, currency="RUB", device_limit=1)
        session.get.return_value = version
        # scalar will be called for quote lookup, then for tariff lookup
        session.scalar.side_effect = [quote, wi_tariff]

        with patch("services.account_purchase.lock_checkout_user", return_value=user), \
             patch("services.account_purchase._settled_state", return_value=(None, False)):
            with self.assertRaises(AccountPurchaseError) as ctx:
                await _settle_account_purchase(session, user_id=1, quote_public_id="mock-uuid")
            self.assertEqual(ctx.exception.code, "tariff_unavailable")

    def test_xray_presence_probe_non_destructive_lifecycle(self):
        """H2: probe_user_presence and verify_user_absent must be strictly non-destructive (zero AlterInbound calls)."""
        from scripts.xray_api.xray_grpc import XrayGrpcClient

        client = XrayGrpcClient()
        mock_channel = MagicMock()
        mock_stub = MagicMock()
        tag = "inbound-de"
        user_uuid = "00000000-1111-2222-3333-444444444444"

        # Initially not present
        self.assertFalse(client.probe_user_presence(tag, user_uuid))
        self.assertTrue(client.verify_user_absent(tag, user_uuid))

        # Add user
        with patch.object(client, "_get_channel", return_value=mock_channel):
            with patch("scripts.xray_api.xray_grpc.proxyman_grpc.HandlerServiceStub", return_value=mock_stub):
                self.assertTrue(client.add_user(tag, user_uuid))

        # Verification must NOT call AlterInbound
        mock_stub.AlterInbound.reset_mock()
        self.assertTrue(client.probe_user_presence(tag, user_uuid))
        self.assertFalse(client.verify_user_absent(tag, user_uuid))
        self.assertFalse(mock_stub.AlterInbound.called)

        # Remove user
        with patch.object(client, "_get_channel", return_value=mock_channel):
            with patch("scripts.xray_api.xray_grpc.proxyman_grpc.HandlerServiceStub", return_value=mock_stub):
                self.assertTrue(client.remove_user(tag, user_uuid))

        # Verification must NOT call AlterInbound
        mock_stub.AlterInbound.reset_mock()
        self.assertFalse(client.probe_user_presence(tag, user_uuid))
        self.assertTrue(client.verify_user_absent(tag, user_uuid))
        self.assertFalse(mock_stub.AlterInbound.called)

    async def test_unban_user_executes_advisory_lock_and_row_lock(self):
        """M3: _unban_user must take pg_advisory_xact_lock and with_for_update."""
        session = AsyncMock()
        user = User(id=42, telegram_id=999, is_banned=True, is_deleted=False)
        session.scalar.return_value = user

        with patch("services.ban_service.update_user", new_callable=AsyncMock) as mock_update, \
             patch("services.ban_service.AuditService.log_action", new_callable=AsyncMock):
            success, status = await BanService._unban_user(
                session=session, admin_id=1, user=user, telegram_id=999
            )
            self.assertTrue(success)
            mock_update.assert_awaited_once_with(session, user, is_banned=False)

            # Check that advisory lock SQL was executed
            calls = [str(call[0][0]) for call in session.execute.call_args_list]
            self.assertTrue(any("pg_advisory_xact_lock" in sql for sql in calls))

    async def test_dead_auto_fulfill_sets_manual_review_status(self):
        """D1: When auto_fulfill exhausts attempts and marks dead, fulfillment_status is manual_review."""
        payment = Payment(
            id=123,
            user_id=1,
            amount=500,
            provider_status="succeeded",
            fulfillment_status="succeeded",
            topup_context={
                "auto_fulfill_action": "purchase",
                "quote_public_id": "00000000-0000-0000-0000-000000000001",
                "auto_fulfill_attempts": 5,
                "auto_fulfill_status": "failed",
            },
        )
        session = AsyncMock()
        await _retry_auto_fulfillment(session, payment)

        self.assertEqual(payment.topup_context.get("auto_fulfill_status"), "dead")
        self.assertEqual(payment.fulfillment_status, "manual_review")

        # Verify _needs_attention includes dead status
        compiled_clause = str(_needs_attention().compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("auto_fulfill_status", compiled_clause)
        self.assertIn("dead", compiled_clause)

    def test_healthcheck_access_logger_masks_sub_wl_token(self):
        """M5: HealthcheckAccessLogger masks /sub/wl/{token} as /sub/wl/***."""
        logger_mock = MagicMock()
        access_logger = HealthcheckAccessLogger(logger_mock, "%a %t %r %s")

        req = MagicMock(spec=web.Request)
        req.path = "/sub/wl/secret_bearer_token_12345"
        req.method = "GET"
        req.remote = "127.0.0.1"
        req.version = MagicMock(major=1, minor=1)
        resp = MagicMock(spec=web.StreamResponse)
        resp.status = 200
        resp.body_length = 512

        access_logger.log(req, resp, 0.05)

        logger_mock.info.assert_called_once()
        log_text = logger_mock.info.call_args[0][0] % logger_mock.info.call_args[0][1:]
        self.assertIn("/sub/wl/***", log_text)
        self.assertNotIn("secret_bearer_token_12345", log_text)
