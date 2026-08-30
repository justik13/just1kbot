import os
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("BOT_TOKEN", "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
os.environ.setdefault("ADMIN_IDS", "[100]")
os.environ.setdefault("SUPPORT_USERNAME", "test_support")
os.environ.setdefault("DB_ENCRYPTION_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("REDIS_PASSWORD", "testpass")
os.environ.setdefault("YOOKASSA_SHOP_ID", "12345")
os.environ.setdefault("YOOKASSA_SECRET_KEY", "test_key")
os.environ.setdefault("YOOKASSA_RETURN_URL", "https://t.me/test_bot?start={bot_username}")
os.environ.setdefault("YOOKASSA_WEBHOOK_PORT", "8080")
os.environ.setdefault("DOMAIN", "myrealdomain.com")
os.environ.setdefault("SSL_EMAIL", "admin@myrealdomain.com")
# NOTE: no DATABASE_URL setdefault here — this module must not flip the
# skipUnless live-database marker of tests/test_database_startup.py.

from services.workers.cleanup import _cleanup_old_records
from services.yookassa_service import YooKassaErrorKind, YooKassaResult


def _payment_row(payment_id=501, external_id="ext-501"):
    return (payment_id, external_id)


class ProviderVerifiedAutoExpireTests(unittest.IsolatedAsyncioTestCase):
    """Pending payments older than PAYMENT_EXPIRATION_HOURS may only be
    locally cancelled after the provider still reports pending (fail-closed)."""

    def _make_scopes(self, pending_rows):
        select_res = MagicMock()
        select_res.all.return_value = pending_rows
        generic_res = MagicMock()
        update_res = MagicMock()
        update_res.rowcount = 1
        calls = {"scope_count": 0, "update_used": False}

        @asynccontextmanager
        async def fake_scope():
            calls["scope_count"] += 1
            role = calls["scope_count"]
            session = MagicMock()

            async def _execute(stmt):
                if role == 2:
                    return select_res
                if role == 3:
                    calls["update_used"] = True
                    return update_res
                return generic_res

            session.execute.side_effect = _execute
            yield session

        return fake_scope, calls

    def _patches(self, fake_scope, mock_svc, get_result_return):
        return (
            patch("services.workers.cleanup.session_scope", side_effect=fake_scope),
            patch(
                "services.workers.cleanup.clear_audit_logs",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch(
                "services.workers.cleanup._batch_delete_matching",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch("services.workers.cleanup.YooKassaService", mock_svc),
        )

    async def test_provider_pending_payment_is_cancelled(self):
        fake_scope, calls = self._make_scopes([_payment_row()])
        mock_svc = MagicMock()
        mock_svc.get_payment_result = AsyncMock(
            return_value=YooKassaResult(True, value={"id": "ext-501", "status": "pending"})
        )
        patches = self._patches(fake_scope, mock_svc, None)
        with patches[0], patches[1], patches[2], patches[3]:
            await _cleanup_old_records()
        mock_svc.get_payment_result.assert_awaited_once_with("ext-501")
        self.assertTrue(calls["update_used"])

    async def test_provider_succeeded_payment_is_never_cancelled(self):
        fake_scope, calls = self._make_scopes([_payment_row()])
        mock_svc = MagicMock()
        mock_svc.get_payment_result = AsyncMock(
            return_value=YooKassaResult(True, value={"id": "ext-501", "status": "succeeded"})
        )
        patches = self._patches(fake_scope, mock_svc, None)
        with patches[0], patches[1], patches[2], patches[3]:
            await _cleanup_old_records()
        mock_svc.get_payment_result.assert_awaited_once_with("ext-501")
        self.assertFalse(calls["update_used"])

    async def test_provider_error_is_fail_closed(self):
        fake_scope, calls = self._make_scopes([_payment_row()])
        mock_svc = MagicMock()
        mock_svc.get_payment_result = AsyncMock(
            return_value=YooKassaResult(False, error_kind=YooKassaErrorKind.NETWORK_ERROR)
        )
        patches = self._patches(fake_scope, mock_svc, None)
        with patches[0], patches[1], patches[2], patches[3]:
            await _cleanup_old_records()
        self.assertFalse(calls["update_used"])

    async def test_missing_external_id_is_fail_closed(self):
        fake_scope, calls = self._make_scopes([_payment_row(external_id=None)])
        mock_svc = MagicMock()
        mock_svc.get_payment_result = AsyncMock()
        patches = self._patches(fake_scope, mock_svc, None)
        with patches[0], patches[1], patches[2], patches[3]:
            await _cleanup_old_records()
        mock_svc.get_payment_result.assert_not_awaited()
        self.assertFalse(calls["update_used"])


if __name__ == "__main__":
    unittest.main()
