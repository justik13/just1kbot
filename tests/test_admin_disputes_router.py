"""Unit tests for admin disputes router registration, access control, and full business flow handlers."""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram import Dispatcher
from aiogram.fsm.context import FSMContext

from bot.handlers.admin import disputes_router
from bot.handlers.admin.disputes import (
    apply_dispute_resolution,
    cancel_dispute_entry,
    confirm_dispute_resolution,
    mark_dispute_review,
    receive_dispute_entry,
    show_dispute_card,
    show_disputes,
    start_dispute_entry,
)
from database.dispute_models import PaymentDispute
from services.payment_disputes import PaymentDisputeError


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
                from bot.handlers.admin import admin_router, dashboard_router
                self.assertIn(admin_router, dp.sub_routers)
                self.assertIn(dashboard_router, admin_router.sub_routers)
                self.assertIn(disputes_router, dashboard_router.sub_routers)
            finally:
                await bot.session.close()

    async def test_show_disputes_non_admin_denied(self):
        callback = AsyncMock()
        callback.from_user = MagicMock(id=99999)
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
        callback.from_user = MagicMock(id=12345)
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
        callback.from_user = MagicMock(id=12345)
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
        callback.from_user = MagicMock(id=12345)
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
        dispute.disputed_at = datetime(2026, 8, 15, tzinfo=timezone.utc)

        with (
            patch("bot.handlers.admin.disputes.is_admin", return_value=True),
            patch("bot.handlers.admin.disputes._render_card", new_callable=AsyncMock) as mock_render,
        ):
            mock_render.return_value = ("Card text", MagicMock())
            await show_dispute_card(callback, session)
            mock_render.assert_called_once_with(session, 42)
            callback.message.edit_text.assert_called_once()

    async def test_mark_dispute_review_calls_service_layer(self):
        callback = AsyncMock()
        callback.from_user = MagicMock(id=12345)
        callback.data = "admin_dispute_review:42"
        callback.message = AsyncMock()
        session = AsyncMock()

        mock_dispute = MagicMock(id=42, status="manual_review")

        with (
            patch("bot.handlers.admin.disputes.is_admin", return_value=True),
            patch("bot.handlers.admin.disputes.mark_payment_dispute_manual_review", new_callable=AsyncMock) as mock_service,
            patch("bot.handlers.admin.disputes._render_card", new_callable=AsyncMock) as mock_render,
        ):
            mock_service.return_value = mock_dispute
            mock_render.return_value = ("Updated card text", MagicMock())

            await mark_dispute_review(callback, session)

            mock_service.assert_called_once_with(
                session,
                dispute_id=42,
                admin_id=12345,
                note="marked for manual review in Telegram admin",
            )
            mock_render.assert_called_once_with(session, 42)
            callback.message.edit_text.assert_called_once()
            callback.answer.assert_called_once()

    async def test_confirm_dispute_resolution_won_shows_confirmation(self):
        callback = AsyncMock()
        callback.from_user = MagicMock(id=12345)
        callback.data = "admin_dispute_resolve:won_by_merchant:42"
        callback.message = AsyncMock()
        callback.message.edit_text = AsyncMock()
        session = AsyncMock()

        dispute = MagicMock(id=42, status="open")
        session.get.return_value = dispute

        with patch("bot.handlers.admin.disputes.is_admin", return_value=True):
            await confirm_dispute_resolution(callback, session)

            session.get.assert_called_once_with(PaymentDispute, 42)
            callback.message.edit_text.assert_called_once()
            text = callback.message.edit_text.call_args[0][0]
            self.assertIn("Подтвердите", text)
            markup = callback.message.edit_text.call_args[1]["reply_markup"]
            button_callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
            self.assertIn("admin_dispute_apply:won_by_merchant:42", button_callbacks)

    async def test_confirm_dispute_resolution_lost_shows_confirmation(self):
        callback = AsyncMock()
        callback.from_user = MagicMock(id=12345)
        callback.data = "admin_dispute_resolve:lost_by_merchant:42"
        callback.message = AsyncMock()
        callback.message.edit_text = AsyncMock()
        session = AsyncMock()

        dispute = MagicMock(id=42, status="manual_review")
        session.get.return_value = dispute

        with patch("bot.handlers.admin.disputes.is_admin", return_value=True):
            await confirm_dispute_resolution(callback, session)

            session.get.assert_called_once_with(PaymentDispute, 42)
            callback.message.edit_text.assert_called_once()
            markup = callback.message.edit_text.call_args[1]["reply_markup"]
            button_callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
            self.assertIn("admin_dispute_apply:lost_by_merchant:42", button_callbacks)

    async def test_apply_dispute_resolution_won_calls_service_layer(self):
        callback = AsyncMock()
        callback.from_user = MagicMock(id=12345)
        callback.data = "admin_dispute_apply:won_by_merchant:42"
        callback.message = AsyncMock()
        callback.message.edit_text = AsyncMock()
        session = AsyncMock()

        mock_dispute = MagicMock(id=42, status="won_by_merchant")

        with (
            patch("bot.handlers.admin.disputes.is_admin", return_value=True),
            patch("bot.handlers.admin.disputes.resolve_payment_dispute", new_callable=AsyncMock) as mock_resolve,
            patch("bot.handlers.admin.disputes._render_card", new_callable=AsyncMock) as mock_render,
        ):
            mock_resolve.return_value = mock_dispute
            mock_render.return_value = ("Resolved card text", MagicMock())

            await apply_dispute_resolution(callback, session)

            mock_resolve.assert_called_once_with(
                session,
                dispute_id=42,
                outcome="won_by_merchant",
                admin_id=12345,
            )
            mock_render.assert_called_once_with(session, 42)
            callback.message.edit_text.assert_called_once()
            callback.answer.assert_called_once()

    async def test_apply_dispute_resolution_lost_calls_service_layer(self):
        callback = AsyncMock()
        callback.from_user = MagicMock(id=12345)
        callback.data = "admin_dispute_apply:lost_by_merchant:42"
        callback.message = AsyncMock()
        callback.message.edit_text = AsyncMock()
        session = AsyncMock()

        mock_dispute = MagicMock(id=42, status="lost_by_merchant")

        with (
            patch("bot.handlers.admin.disputes.is_admin", return_value=True),
            patch("bot.handlers.admin.disputes.resolve_payment_dispute", new_callable=AsyncMock) as mock_resolve,
            patch("bot.handlers.admin.disputes._render_card", new_callable=AsyncMock) as mock_render,
        ):
            mock_resolve.return_value = mock_dispute
            mock_render.return_value = ("Resolved card text", MagicMock())

            await apply_dispute_resolution(callback, session)

            mock_resolve.assert_called_once_with(
                session,
                dispute_id=42,
                outcome="lost_by_merchant",
                admin_id=12345,
            )
            mock_render.assert_called_once_with(session, 42)

    async def test_receive_dispute_entry_validation_failures(self):
        message = AsyncMock()
        message.from_user = MagicMock(id=12345)
        message.answer = AsyncMock()
        state = AsyncMock(spec=FSMContext)
        session = AsyncMock()

        with patch("bot.handlers.admin.disputes.is_admin", return_value=True):
            # 1. Invalid parts length
            message.text = "pay_1|case_1|100|2026-08-15|open"  # only 5 parts
            await receive_dispute_entry(message, state, session)
            message.answer.assert_called()
            state.clear.assert_not_called()

            # 2. Invalid status
            message.text = "pay_1|case_1|100|2026-08-15|invalid_status|note"
            await receive_dispute_entry(message, state, session)
            state.clear.assert_not_called()

            # 3. Invalid date
            message.text = "pay_1|case_1|100|bad-date|open|note"
            await receive_dispute_entry(message, state, session)
            state.clear.assert_not_called()

    async def test_receive_dispute_entry_full_flow_open(self):
        message = AsyncMock()
        message.from_user = MagicMock(id=12345)
        message.answer = AsyncMock()
        message.text = "pay_100|case_abc|500|2026-08-15|open|Chargeback reason"
        state = AsyncMock(spec=FSMContext)
        session = AsyncMock()

        mock_dispute = MagicMock(id=99, status="open")
        mock_result = SimpleNamespace(dispute=mock_dispute, created=True)

        with (
            patch("bot.handlers.admin.disputes.is_admin", return_value=True),
            patch("bot.handlers.admin.disputes.open_payment_dispute", new_callable=AsyncMock) as mock_open,
            patch("bot.handlers.admin.disputes._render_card", new_callable=AsyncMock) as mock_render,
        ):
            mock_open.return_value = mock_result
            mock_render.return_value = ("Rendered card", MagicMock())

            await receive_dispute_entry(message, state, session)

            mock_open.assert_called_once_with(
                session,
                provider_payment_id="pay_100",
                provider_case_id="case_abc",
                amount="500",
                disputed_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
                note="Chargeback reason",
                admin_id=12345,
            )
            state.clear.assert_called_once()
            mock_render.assert_called_once_with(session, 99)
            message.answer.assert_called_once()

    async def test_receive_dispute_entry_full_flow_resolve_won(self):
        message = AsyncMock()
        message.from_user = MagicMock(id=12345)
        message.answer = AsyncMock()
        message.text = "pay_100|case_abc|500|2026-08-15|won_by_merchant|Won evidence"
        state = AsyncMock(spec=FSMContext)
        session = AsyncMock()

        mock_dispute_init = MagicMock(id=99, status="open")
        mock_dispute_won = MagicMock(id=99, status="won_by_merchant")
        mock_result = SimpleNamespace(dispute=mock_dispute_init, created=True)

        with (
            patch("bot.handlers.admin.disputes.is_admin", return_value=True),
            patch("bot.handlers.admin.disputes.open_payment_dispute", new_callable=AsyncMock) as mock_open,
            patch("bot.handlers.admin.disputes.resolve_payment_dispute", new_callable=AsyncMock) as mock_resolve,
            patch("bot.handlers.admin.disputes._render_card", new_callable=AsyncMock) as mock_render,
        ):
            mock_open.return_value = mock_result
            mock_resolve.return_value = mock_dispute_won
            mock_render.return_value = ("Rendered won card", MagicMock())

            await receive_dispute_entry(message, state, session)

            mock_open.assert_called_once()
            mock_resolve.assert_called_once_with(
                session,
                dispute_id=99,
                outcome="won_by_merchant",
                admin_id=12345,
                note="Won evidence",
            )
            state.clear.assert_called_once()
            mock_render.assert_called_once_with(session, 99)

    async def test_receive_dispute_entry_handles_service_error(self):
        message = AsyncMock()
        message.from_user = MagicMock(id=12345)
        message.answer = AsyncMock()
        message.text = "pay_100|case_abc|500|2026-08-15|open|reason"
        state = AsyncMock(spec=FSMContext)
        session = AsyncMock()

        with (
            patch("bot.handlers.admin.disputes.is_admin", return_value=True),
            patch("bot.handlers.admin.disputes.open_payment_dispute", new_callable=AsyncMock) as mock_open,
        ):
            mock_open.side_effect = PaymentDisputeError(code="payment_not_found")

            await receive_dispute_entry(message, state, session)

            state.clear.assert_not_called()
            message.answer.assert_called_once()
            self.assertTrue(len(message.answer.call_args[0][0]) > 0)




if __name__ == "__main__":
    unittest.main()

