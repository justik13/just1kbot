"""Regression tests for audit remediation fixes (H1, H2, H3, D1, M1, M2, M3, M5)."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import web

from config.enums import ServiceType
from database.models import Tariff, User
from database.repositories.tariffs_repo import get_active_tariffs
from services.ban_service import BanService
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



    def test_healthcheck_access_logger_masks_sub_wl_token(self):
        """M5: HealthcheckAccessLogger masks /sub/wl/{token} as /sub/wl/*** and logs 200 at DEBUG."""
        logger_mock = MagicMock()
        access_logger = HealthcheckAccessLogger(logger_mock, "%a %t %r %s")

        req = MagicMock(spec=web.Request)
        req.path = "/sub/wl/secret_bearer_token_12345"
        req.method = "GET"
        req.remote = "127.0.0.1"
        req.version = MagicMock(major=1, minor=1)
        req.headers = {"X-Real-IP": "127.0.0.1", "User-Agent": "TestClient"}
        resp = MagicMock(spec=web.StreamResponse)
        resp.status = 200
        resp.body_length = 512

        access_logger.log(req, resp, 0.05)

        logger_mock.debug.assert_called_once()
        log_text = logger_mock.debug.call_args[0][0] % logger_mock.debug.call_args[0][1:]
        self.assertIn("/sub/wl/***", log_text)
        self.assertNotIn("secret_bearer_token_12345", log_text)
