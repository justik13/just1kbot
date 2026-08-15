import asyncio
import unittest
from unittest.mock import MagicMock

from aiogram import Bot

from database.repositories.users_repo import ALLOWED_USER_UPDATE_FIELDS
from services.device_service import RESERVING_STATUSES
from services.workers.cleanup import cleanup_dangling_peers_loop
from services.workers.traffic import traffic_sync_loop


class AuditSyncFixesTests(unittest.IsolatedAsyncioTestCase):
    def test_users_repo_allowed_fields_no_referral_days(self):
        self.assertNotIn("referral_days", ALLOWED_USER_UPDATE_FIELDS)
        self.assertIn("device_limit", ALLOWED_USER_UPDATE_FIELDS)
        self.assertIn("referred_by", ALLOWED_USER_UPDATE_FIELDS)

    def test_device_service_reserving_statuses_includes_update_failed(self):
        self.assertIn("update_failed", RESERVING_STATUSES)
        self.assertIn("active", RESERVING_STATUSES)
        self.assertIn("pending_create", RESERVING_STATUSES)
        self.assertIn("pending_update", RESERVING_STATUSES)

    async def test_cleanup_worker_loop_accepts_bot_and_shutdown(self):
        mock_bot = MagicMock(spec=Bot)
        shutdown_event = asyncio.Event()
        shutdown_event.set()

        # Should exit immediately without error
        await cleanup_dangling_peers_loop(mock_bot, shutdown_event)
        # Should also accept single argument (event)
        await cleanup_dangling_peers_loop(shutdown_event)

    async def test_traffic_worker_loop_accepts_bot_and_shutdown(self):
        mock_bot = MagicMock(spec=Bot)
        shutdown_event = asyncio.Event()
        shutdown_event.set()

        # Should exit immediately without error
        await traffic_sync_loop(mock_bot, shutdown_event)
        # Should also accept single argument (event)
        await traffic_sync_loop(shutdown_event)

    def test_setup_script_contains_literal_template_url(self):
        with open("scripts/setup.sh", "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("YOOKASSA_RETURN_URL='https://t.me/{bot_username}'", content)

    def test_cli_help_contains_config(self):
        with open("scripts/cli.sh", "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("config", content)


if __name__ == "__main__":
    unittest.main()
