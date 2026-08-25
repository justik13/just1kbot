"""Regression tests for PR #210 remediation wave (R0/R1 fixes)."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup

from utils.telegram import (
    EFFECT_CONFETTI,
    EFFECT_LIKE,
    EFFECT_LIGHTNING,
    _send_with_resilience,
    render_hub,
)


def _kb_callbacks(markup):
    return {
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
        if btn.callback_data
    }


class TestHubResilience(unittest.IsolatedAsyncioTestCase):
    async def test_render_hub_stores_each_part_immediately(self):
        """T-02: every delivered part is persisted before the next one is sent."""
        bot = MagicMock()
        long_text = "\n".join(f"line {i}" for i in range(600))
        responses = [MagicMock(message_id=11), MagicMock(message_id=12)]

        async def fake_send(**kwargs):
            if len(responses) == 1:
                raise RuntimeError("telegram dropped")
            return responses.pop(0)

        bot.send_message = AsyncMock(side_effect=fake_send)

        with patch("utils.telegram._load_hub_ids_from_db", new=AsyncMock(return_value=[])), \
             patch("utils.telegram._store_hub_id_in_db", new=AsyncMock()) as mock_store, \
             patch("utils.telegram._delete_hub_messages", new=AsyncMock()) as mock_del:
            with self.assertRaises(RuntimeError):
                await render_hub(bot, chat_id=1, text=long_text)

        self.assertEqual(mock_store.await_count, 1)
        mock_store.assert_awaited_with(1, 11)
        mock_del.assert_awaited_once()
        deleted_ids = mock_del.call_args[0][2]
        self.assertEqual(deleted_ids, [11])

    async def test_not_modified_branch_persists_target_id(self):
        """T-13: 'message is not modified' still records the target hub id."""
        bot = MagicMock()
        bot.edit_message_text = AsyncMock(
            side_effect=TelegramBadRequest(method=MagicMock(), message="Bad Request: message is not modified")
        )
        with patch("utils.telegram._load_hub_ids_from_db", new=AsyncMock(return_value=[100])), \
             patch("utils.telegram._store_hub_id_in_db", new=AsyncMock()) as mock_store, \
             patch("utils.telegram._delete_hub_messages", new=AsyncMock()):
            mid = await render_hub(bot, chat_id=5, text="Same", trigger_message_id=100)
        self.assertEqual(mid, 100)
        mock_store.assert_awaited_once_with(5, 100)

    async def test_resilience_never_resends_after_ambiguous_timeout(self):
        """T-03: ambiguous timeout must not produce duplicate messages."""
        op = AsyncMock(side_effect=asyncio.TimeoutError())
        with self.assertRaises(asyncio.TimeoutError):
            await _send_with_resilience(op, chat_id=9, context="test")
        self.assertEqual(op.await_count, 1)

    async def test_resilience_retries_flood_control(self):
        """T-03: TelegramRetryAfter waits and retries exactly once."""
        calls = {"n": 0}

        async def op():
            calls["n"] += 1
            if calls["n"] == 1:
                raise TelegramRetryAfter(MagicMock(), "flood", retry_after=0)
            return "ok"

        result = await _send_with_resilience(op, chat_id=9, context="test")
        self.assertEqual(result, "ok")
        self.assertEqual(calls["n"], 2)


class TestEffectsWiring(unittest.IsolatedAsyncioTestCase):
    def test_effect_constants_match_bot_api_ids(self):
        self.assertEqual(EFFECT_CONFETTI, "5046509860389126442")
        self.assertEqual(EFFECT_LIKE, "5107584321108051014")
        self.assertEqual(EFFECT_LIGHTNING, "5104841245755180585")

    async def test_render_device_screen_effect_passthrough(self):
        """T-04: creation card renders as a NEW message carrying the LIGHTNING effect."""
        from bot.handlers.connection.device_view_routes import render_device_screen

        profile = SimpleNamespace(
            id=42, server_id=1, device_name="Dev #1",
            provisioning_status="active", traffic_down=0,
            traffic_up=0, last_connected=None, raw_config="vpn://abc",
        )
        user = SimpleNamespace(id=10, telegram_id=100)
        server = SimpleNamespace(id=1, country_flag="🇩🇪", name="Germany", protocol="amneziawg2")

        with patch("bot.handlers.connection.device_view_routes.get_server_by_id", AsyncMock(return_value=server)), \
             patch("bot.handlers.connection.device_view_routes.SubscriptionService.check_access", AsyncMock(return_value=True)), \
             patch("bot.handlers.connection.device_view_routes.can_show_config_actions", return_value=True), \
             patch("bot.handlers.connection.device_view_routes.can_show_delete_action", return_value=True), \
             patch("bot.handlers.connection.device_view_routes.render_hub", new=AsyncMock()) as mock_hub:
            await render_device_screen(
                MagicMock(), 12345, profile, user, AsyncMock(),
                message_effect_id=EFFECT_LIGHTNING,
            )

        kwargs = mock_hub.call_args.kwargs
        self.assertEqual(kwargs.get("message_effect_id"), EFFECT_LIGHTNING)
        self.assertTrue(kwargs.get("force_new"))


class TestPaymentFixes(unittest.IsolatedAsyncioTestCase):
    async def test_resume_purchase_same_tier_shows_renew_screen(self):
        """T-09: same-tier rejection on resume shows renew guidance, not stale-op error."""
        from bot import texts
        from bot.handlers.payment import purchase_routes as pr

        callback = MagicMock()
        callback.data = "balance_resume_purchase:5:change"
        callback.bot = MagicMock()
        callback.message = MagicMock()
        callback.message.chat = MagicMock(id=777)
        callback.answer = AsyncMock()
        db_user = SimpleNamespace(id=1)

        quote_result = SimpleNamespace(failure_code="same_tariff_requires_renew")

        with patch("services.tariff_change_quote.create_tariff_change_quote", AsyncMock(return_value=quote_result)), \
             patch("bot.handlers.payment.purchase_routes.render_hub", new=AsyncMock()) as mock_hub:
            await pr.resume_purchase_after_topup(callback, AsyncMock(), db_user=db_user)

        mock_hub.assert_awaited_once()
        args = mock_hub.call_args[0]
        self.assertEqual(args[2], texts.UI_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L165_1)
        self.assertIn("payment_quick_renew", _kb_callbacks(args[3]))

    async def test_select_tariff_inactive_same_tier_reports_unavailable(self):
        """T-12: inactive tariff check fires before the same-tier guard."""
        from bot import texts
        from bot.handlers.payment import showcase_routes as sr

        callback = MagicMock()
        callback.data = "select_tariff:3:change"
        callback.from_user = MagicMock(id=555)
        callback.answer = AsyncMock()
        callback.bot = MagicMock()
        callback.message = MagicMock()
        callback.message.chat = MagicMock(id=888)

        tariff = SimpleNamespace(id=3, device_limit=2, duration_days=30, price_rub=90, is_active=False)
        db_user = SimpleNamespace(id=1, current_tariff_id=1, device_limit=2)

        with patch.object(sr.MaintenanceService, "can_user_perform_action", AsyncMock(return_value=True)), \
             patch("bot.handlers.payment.showcase_routes.get_tariff_by_id", AsyncMock(return_value=tariff)), \
             patch("bot.handlers.payment.showcase_routes._get_effective_device_limit", AsyncMock(return_value=2)), \
             patch("bot.handlers.payment.showcase_routes.render_hub", new=AsyncMock()) as mock_hub:
            await sr.select_tariff(callback, AsyncMock(), AsyncMock(), db_user=db_user)

        callback.answer.assert_awaited_once_with(texts.ERROR_TARIFF_UNAVAILABLE, show_alert=True)
        mock_hub.assert_not_awaited()


class TestKeyboardSerialization(unittest.IsolatedAsyncioTestCase):
    """Guard against Bot API field drift: keyboards must survive a full pydantic round-trip."""

    def _roundtrip(self, markup):
        payload = markup.model_dump_json(exclude_none=True)
        restored = InlineKeyboardMarkup.model_validate_json(payload)
        self.assertEqual(restored.model_dump_json(exclude_none=True), payload)
        return restored

    async def test_device_keyboards_roundtrip_with_styles(self):
        from bot.keyboards.device import get_alt_connection_keyboard, get_device_keyboard

        kb = self._roundtrip(get_device_keyboard(profile_id=7, config_ready=True, show_delete=True))
        flat = [btn for row in kb.inline_keyboard for btn in row]
        self.assertEqual(flat[0].style, "primary")
        delete_btn = next(b for b in flat if b.callback_data and b.callback_data.startswith("request_delete_device"))
        self.assertEqual(delete_btn.style, "danger")

        alt = self._roundtrip(get_alt_connection_keyboard(7, "https://bridge.example/open"))
        alt_flat = [btn for row in alt.inline_keyboard for btn in row]
        self.assertEqual(alt_flat[0].style, "primary")
        self.assertTrue(alt_flat[-1].callback_data.startswith("manage_device:"))

    async def test_payment_keyboards_roundtrip(self):
        from bot.keyboards.payment import (
            get_topup_payment_keyboard,
            get_same_tariff_keyboard,
        )

        topup = self._roundtrip(get_topup_payment_keyboard("https://pay.example/x", 3))
        styles = {btn.style for row in topup.inline_keyboard for btn in row}
        self.assertIn("success", styles)
        self.assertIn("danger", styles)

        same = self._roundtrip(get_same_tariff_keyboard())
        self.assertIn("payment_quick_renew", _kb_callbacks(same))


if __name__ == "__main__":
    unittest.main()
