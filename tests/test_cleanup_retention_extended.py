"""Unit tests for extended retention cleanup (_cleanup_old_records) in cleanup worker."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch


from services.workers.cleanup import (
    AUDIT_LOG_RETENTION_DAYS,
    STALE_QUOTES_RETENTION_DAYS,
    WEBHOOK_INBOX_RETENTION_DAYS,
    _cleanup_old_records,
)


class TestCleanupRetentionExtended(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_old_records_prunes_webhooks_and_quotes(self):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 5
        mock_session.execute.return_value = mock_result

        with (
            patch("services.workers.cleanup.session_scope") as mock_scope,
            patch("services.workers.cleanup.clear_audit_logs", new_callable=AsyncMock) as mock_clear_audit,
            patch("services.workers.cleanup.logger") as mock_logger,
        ):
            mock_scope.return_value.__aenter__.return_value = mock_session
            mock_clear_audit.return_value = 10

            await _cleanup_old_records()

            # Verify clear_audit_logs called with retention days
            mock_clear_audit.assert_called_once_with(
                mock_session,
                older_than_days=AUDIT_LOG_RETENTION_DAYS,
            )

            # Verify session.execute called for broadcasts, stuck broadcasts, hub messages,
            # abandoned payments, webhooks, and stale quotes
            self.assertGreaterEqual(mock_session.execute.call_count, 5)

            # Verify info log contains counts
            mock_logger.info.assert_called_once()
            log_args = mock_logger.info.call_args[0]
            self.assertIn("old webhooks deleted", log_args[0])
            self.assertIn("stale quotes deleted", log_args[0])

    def test_retention_constants(self):
        self.assertEqual(30, WEBHOOK_INBOX_RETENTION_DAYS)
        self.assertEqual(14, STALE_QUOTES_RETENTION_DAYS)
        self.assertEqual(180, AUDIT_LOG_RETENTION_DAYS)


if __name__ == "__main__":
    unittest.main()
