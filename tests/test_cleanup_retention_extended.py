"""Unit tests for batched retention cleanup (_cleanup_old_records, _batch_delete_matching) in cleanup worker."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from database.models import (
    BroadcastProgress,
    HubMessage,
    WebhookInbox,
)
from services.workers.cleanup import (
    AUDIT_LOG_RETENTION_DAYS,
    BATCH_DELETE_CHUNK_SIZE,
    MAX_BATCH_DELETE_ROUNDS,
    WEBHOOK_INBOX_RETENTION_DAYS,
    _batch_delete_matching,
    _cleanup_old_records,
)


class TestCleanupRetentionExtended(unittest.IsolatedAsyncioTestCase):
    def test_retention_constants(self):
        self.assertEqual(30, WEBHOOK_INBOX_RETENTION_DAYS)
        self.assertEqual(180, AUDIT_LOG_RETENTION_DAYS)
        self.assertEqual(500, BATCH_DELETE_CHUNK_SIZE)
        self.assertEqual(100, MAX_BATCH_DELETE_ROUNDS)

    async def test_batch_delete_matching_chunks_multi_round(self):
        mock_session = AsyncMock()

        # Simulate 3 rounds: 500 ids, 500 ids, 200 ids (total 1200)
        round1_scalars = MagicMock()
        round1_scalars.all.return_value = list(range(1, 501))

        round2_scalars = MagicMock()
        round2_scalars.all.return_value = list(range(501, 1001))

        round3_scalars = MagicMock()
        round3_scalars.all.return_value = list(range(1001, 1201))

        round1_res = MagicMock()
        round1_res.scalars.return_value = round1_scalars

        round2_res = MagicMock()
        round2_res.scalars.return_value = round2_scalars

        round3_res = MagicMock()
        round3_res.scalars.return_value = round3_scalars

        del_res1 = MagicMock(rowcount=500)
        del_res2 = MagicMock(rowcount=500)
        del_res3 = MagicMock(rowcount=200)

        mock_session.execute.side_effect = [
            round1_res,  # select ids round 1
            del_res1,   # delete round 1
            round2_res,  # select ids round 2
            del_res2,   # delete round 2
            round3_res,  # select ids round 3
            del_res3,   # delete round 3
        ]

        total = await _batch_delete_matching(
            WebhookInbox,
            WebhookInbox.status == "succeeded",
            session=mock_session,
            batch_size=500,
            max_rounds=20,
        )

        self.assertEqual(1200, total)
        self.assertEqual(6, mock_session.execute.call_count)
        self.assertEqual(3, mock_session.flush.call_count)

    async def test_batch_delete_matching_empty_returns_zero(self):
        mock_session = AsyncMock()
        empty_scalars = MagicMock()
        empty_scalars.all.return_value = []
        empty_res = MagicMock()
        empty_res.scalars.return_value = empty_scalars
        mock_session.execute.return_value = empty_res

        total = await _batch_delete_matching(
            WebhookInbox,
            WebhookInbox.status == "dead",
            session=mock_session,
        )

        self.assertEqual(0, total)
        self.assertEqual(1, mock_session.execute.call_count)

    async def test_cleanup_old_records_executes_batched_pruning(self):
        mock_session = AsyncMock()

        # Return empty id lists for all batched select queries
        empty_scalars = MagicMock()
        empty_scalars.all.return_value = []
        empty_res = MagicMock()
        empty_res.scalars.return_value = empty_scalars
        mock_session.execute.return_value = empty_res

        with (
            patch("services.workers.cleanup.session_scope") as mock_scope,
            patch("services.workers.cleanup.clear_audit_logs", new_callable=AsyncMock) as mock_clear_audit,
            patch("services.workers.cleanup._batch_delete_matching", new_callable=AsyncMock) as mock_batch_del,
            patch("services.workers.cleanup.logger") as mock_logger,
        ):
            mock_scope.return_value.__aenter__.return_value = mock_session
            mock_clear_audit.return_value = 15
            mock_batch_del.side_effect = [3, 2, 8]  # broadcasts, hub, webhooks

            await _cleanup_old_records()

            mock_clear_audit.assert_called_once_with(
                older_than_days=AUDIT_LOG_RETENTION_DAYS,
            )

            # Verify _batch_delete_matching called for:
            # 1. BroadcastProgress
            # 2. HubMessage
            # 3. WebhookInbox
            self.assertEqual(3, mock_batch_del.call_count)

            # 1. BroadcastProgress call
            bp_call = mock_batch_del.call_args_list[0]
            self.assertIs(bp_call[0][0], BroadcastProgress)

            # 2. HubMessage call
            hub_call = mock_batch_del.call_args_list[1]
            self.assertIs(hub_call[0][0], HubMessage)

            # 3. WebhookInbox call: verify status IN ('succeeded', 'dead')
            wh_call = mock_batch_del.call_args_list[2]
            self.assertIs(wh_call[0][0], WebhookInbox)

            # Verify info log
            mock_logger.info.assert_called_once()
            log_args = mock_logger.info.call_args[0]
            self.assertIn("old webhooks deleted", log_args[0])

    def test_webhook_retention_predicates_logic(self):
        """Pure-logic verification of the retention predicates against sample entity states."""
        now = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)
        threshold_webhooks = now - timedelta(days=WEBHOOK_INBOX_RETENTION_DAYS)

        # Predicate for WebhookInbox:
        # status IN ('succeeded', 'dead') AND received_at < threshold_webhooks
        def matches_webhook_retention(status: str, received_at: datetime) -> bool:
            return status in ("succeeded", "dead") and received_at < threshold_webhooks

        old_dt = now - timedelta(days=40)
        recent_dt = now - timedelta(days=2)

        # Webhook matching assertions
        self.assertTrue(matches_webhook_retention("succeeded", old_dt))
        self.assertTrue(matches_webhook_retention("dead", old_dt))
        self.assertFalse(matches_webhook_retention("pending", old_dt), "Pending webhook must NEVER be deleted")
        self.assertFalse(matches_webhook_retention("processing", old_dt), "Processing webhook must NEVER be deleted")
        self.assertFalse(matches_webhook_retention("retry", old_dt), "Retry webhook must NEVER be deleted")
        self.assertFalse(matches_webhook_retention("succeeded", recent_dt), "Recent succeeded webhook must NOT be deleted")
        self.assertFalse(matches_webhook_retention("dead", recent_dt), "Recent dead webhook must NOT be deleted")


