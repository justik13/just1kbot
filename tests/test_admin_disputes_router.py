"""Unit tests for admin disputes router registration, access control, and handlers."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram import Dispatcher
from aiogram.fsm.context import FSMContext

from bot.handlers.admin import disputes_router
from bot.handlers.admin.disputes import (
    cancel_dispute_entry,
    show_dispute_card,
    show_disputes,
    start_dispute_entry,
)
from database.dispute_models import PaymentDispute


class TestAdminDisputesRouter(unittest.IsolatedAsyncioTestCase):
    def test_disputes_router_exported_in_admin_init(self):
        import bot.handlers.admin as admin_pkg

        self.assertIn("disputes_router", admin_pkg.__all__)
        self.assertIs(disputes_router, admin_pkg.disputes_router)


    async def test_setup_bot_includes_disputes_router(self):
        from aiogram.fsm.storage.memory import MemoryStorage
        from bot.main import setup_bot

        with (
            patch("bot.main.get_settings") as mock_settings,
            patch("bot.main.RedisStorage.from_url", return_value=MemoryStorage()),
            patch("bot.main.setup_bot_commands", new_callable=AsyncMock),
        ):
            mock_settings.return_value.BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
            mock_settings.return_value.REDIS_URL = "redis://localhost:6379/0"
            mock_settings.return_value.REDIS_PASSWORD = None
            mock_settings.return_value.ADMIN_IDS = [12345]
            bot, dp = await setup_bot()
            try:
                self.assertIsInstance(dp, Dispatcher)
                self.assertIn(disputes_router, dp.sub_routers)
            finally:
                await bot.session.close()

    async def test_show_disputes_non_admin_denied(self):
        callback = AsyncMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 99999
        callback.message = AsyncMock()
        state = AsyncMock(spec=FSMContext)
        session = AsyncMock()

        with patch("bot.handlers.admin.disputes.is_admin", return_value=False):
            await show_disputes(callback, state, session)
            callback.answer.assert_called_once()
            self.assertTrue(callback.answer.call_args[1].get("show_alert"))
            callback.message.edit_text.assert_not_called()

    async def test_show_disputes_admin_success(self):
        callback = AsyncMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 12345
        callback.message = AsyncMock()
        state = AsyncMock(spec=FSMContext)
        session = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        session.scalars.return_value = mock_scalars

        with patch("bot.handlers.admin.disputes.is_admin", return_value=True):
            await show_disputes(callback, state, session)

            state.clear.assert_called_once()
            callback.message.edit_text.assert_called_once()
            text = callback.message.edit_text.call_args[0][0]
            self.assertIn("Управление платежными спорами", text)
            callback.answer.assert_called_once()

    async def test_start_dispute_entry_and_cancel(self):
        callback = AsyncMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 12345
        callback.message = AsyncMock()
        state = AsyncMock(spec=FSMContext)

        with patch("bot.handlers.admin.disputes.is_admin", return_value=True):
            await start_dispute_entry(callback, state)
            state.set_state.assert_called_once()
            callback.message.answer.assert_called_once()

            await cancel_dispute_entry(callback, state)
            state.clear.assert_called_once()
            callback.message.edit_text.assert_called_once()

    async def test_show_dispute_card_found(self):
        callback = AsyncMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 12345
        callback.data = "admin_dispute_card:42"
        callback.message = AsyncMock()
        session = AsyncMock()

        dispute = MagicMock(spec=PaymentDispute)
        dispute.id = 42
        dispute.status = "open"
        dispute.payment_id = 100
        dispute.reservation_id = None
        dispute.amount = 500
        dispute.provider_case_id = "case_abc"
        dispute.chargeback_entry_id = None
        dispute.note = "Test note"
        from datetime import datetime, timezone

        dispute.disputed_at = datetime(2026, 8, 15, tzinfo=timezone.utc)

        with (
            patch("bot.handlers.admin.disputes.is_admin", return_value=True),
            patch("bot.handlers.admin.disputes._render_card", new_callable=AsyncMock) as mock_render,
        ):
            mock_render.return_value = ("Card text", MagicMock())
            await show_dispute_card(callback, session)
            mock_render.assert_called_once_with(session, 42)
            callback.message.edit_text.assert_called_once()



if __name__ == "__main__":
    unittest.main()
