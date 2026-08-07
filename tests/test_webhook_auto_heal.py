import unittest
from unittest.mock import AsyncMock, MagicMock

from database.models import WebhookInbox
from services.workers.webhook_inbox import auto_resolve_untracked_canceled_webhooks, recover_stale


class WebhookAutoHealTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_resolve_untracked_canceled_webhooks(self):
        dead_row = WebhookInbox(
            id=3,
            event_type="payment.canceled",
            status="dead",
            last_error_code="payment_not_visible",
            attempts=30,
            max_attempts=30,
        )

        session = AsyncMock()
        session.scalars = AsyncMock(
            return_value=MagicMock(all=MagicMock(return_value=[dead_row]))
        )

        count = await auto_resolve_untracked_canceled_webhooks(session)

        self.assertEqual(count, 1)
        self.assertEqual(dead_row.status, "succeeded")
        self.assertIsNotNone(dead_row.processed_at)

    async def test_recover_stale_invokes_auto_resolve(self):
        dead_row = WebhookInbox(
            id=4,
            event_type="payment.canceled",
            status="dead",
            last_error_code="payment_not_visible",
            attempts=30,
            max_attempts=30,
        )

        session = AsyncMock()
        session.scalars = AsyncMock(
            side_effect=[
                MagicMock(all=MagicMock(return_value=[dead_row])),
                MagicMock(all=MagicMock(return_value=[])),
            ]
        )

        await recover_stale(session)

        self.assertEqual(dead_row.status, "succeeded")
