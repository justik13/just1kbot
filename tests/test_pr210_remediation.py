"""Regression tests for PR #210 remediation wave (R0/R1 fixes)."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardMarkup

from utils.telegram import (
    EFFECT_CONFETTI,
    EFFECT_FIRE,
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
        mock_store.assert_awaited_once_with(1, 11, is_effect=False)
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

    async def test_resilience_never_retries_network_error(self):
        """P1 fix: TelegramNetworkError wraps ambiguous timeouts (may be delivered) -> no resend."""
        op = AsyncMock(
            side_effect=TelegramNetworkError(MagicMock(), "HTTP Client says - Request timeout error")
        )
        with self.assertRaises(TelegramNetworkError):
            await _send_with_resilience(op, chat_id=9, context="test")
        self.assertEqual(op.await_count, 1)

    async def test_store_failure_aborts_render_and_cleans_sent(self):
        """P1 fix: DB store failure must abort the render, not orphan the message."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MagicMock(message_id=31))

        with patch("utils.telegram._load_hub_ids_from_db", new=AsyncMock(return_value=[])), \
             patch("utils.telegram._store_hub_id_in_db", new=AsyncMock(side_effect=RuntimeError("db down"))), \
             patch("utils.telegram._delete_hub_messages", new=AsyncMock()) as mock_del:
            with self.assertRaises(RuntimeError):
                await render_hub(bot, chat_id=3, text="hello")

        mock_del.assert_awaited_once()
        self.assertEqual(mock_del.call_args[0][2], [31])

    async def test_failed_deletions_stay_visible_in_cache(self):
        """P1 fix: partially failed stale deletions must remain in cache for retry."""
        import utils.telegram as tg

        bot = MagicMock()
        bot.edit_message_text = AsyncMock()
        tg._hub_cache.pop(5, None)

        async def fake_delete(_bot, _chat, ids):
            return [i for i in ids if i == 101]  # 101 failed to delete

        with patch("utils.telegram._load_hub_ids_from_db", new=AsyncMock(return_value=[100, 101, 102])), \
             patch("utils.telegram._store_hub_id_in_db", new=AsyncMock()), \
             patch("utils.telegram._delete_hub_messages", new=AsyncMock(side_effect=fake_delete)):
            mid = await render_hub(bot, chat_id=5, text="Next", trigger_message_id=102)

        self.assertEqual(mid, 102)
        cached_ids = tg._hub_cache[5]["ids"]
        self.assertIn(102, cached_ids)
        self.assertIn(101, cached_ids)
        self.assertNotIn(100, cached_ids)


class TestMaintenanceGates(unittest.IsolatedAsyncioTestCase):
    async def test_bal_short_custom_blocked_during_maintenance(self):
        from bot.handlers.payment import purchase_routes as pr
        import uuid

        callback = MagicMock()
        callback.data = f"bal_short_custom:{uuid.uuid4()}"
        callback.from_user = MagicMock(id=555)
        callback.answer = AsyncMock()
        state = MagicMock()
        state.clear = AsyncMock()

        with patch.object(pr.MaintenanceService, "can_user_perform_action", AsyncMock(return_value=False)), \
             patch("bot.handlers.payment.purchase_routes._render_maintenance", new=AsyncMock()) as mock_maint:
            await pr.topup_custom_shortage(callback, state, AsyncMock(), db_user=SimpleNamespace(id=1))

        state.clear.assert_awaited_once()
        mock_maint.assert_awaited_once()
        self.assertEqual(mock_maint.call_args.kwargs.get("back_to"), "menu_balance")

    async def test_bal_chg_short_custom_blocked_during_maintenance(self):
        from bot.handlers.payment import tariff_change_routes as tcr
        import uuid

        callback = MagicMock()
        callback.data = f"bal_chg_short_custom:{uuid.uuid4()}"
        callback.from_user = MagicMock(id=555)
        callback.answer = AsyncMock()
        state = MagicMock()
        state.clear = AsyncMock()

        with patch.object(tcr.MaintenanceService, "can_user_perform_action", AsyncMock(return_value=False)), \
             patch("bot.handlers.payment.tariff_change_routes._render_maintenance", new=AsyncMock()) as mock_maint:
            await tcr.topup_custom_change_shortage(callback, state, AsyncMock(), db_user=SimpleNamespace(id=1))

        state.clear.assert_awaited_once()
        mock_maint.assert_awaited_once()
        self.assertEqual(mock_maint.call_args.kwargs.get("back_to"), "payment_change_tariff")

    async def test_resume_purchase_blocked_during_maintenance(self):
        from bot.handlers.payment import purchase_routes as pr

        callback = MagicMock()
        callback.data = "balance_resume_purchase:5:change"
        callback.from_user = MagicMock(id=555)
        callback.answer = AsyncMock()

        with patch.object(pr.MaintenanceService, "can_user_perform_action", AsyncMock(return_value=False)), \
             patch("bot.handlers.payment.purchase_routes._render_maintenance", new=AsyncMock()) as mock_maint, \
             patch("services.tariff_change_quote.create_tariff_change_quote", new=AsyncMock()) as mock_quote:
            await pr.resume_purchase_after_topup(callback, AsyncMock(), db_user=SimpleNamespace(id=1))

        mock_maint.assert_awaited_once()
        self.assertEqual(mock_maint.call_args.kwargs.get("back_to"), "payment_change_tariff")
        mock_quote.assert_not_awaited()


class TestSimulationContract(unittest.IsolatedAsyncioTestCase):
    def test_simulate_bot_passes_required_source(self):
        """simulate_bot must satisfy settle_succeeded_topup's keyword-only `source`."""
        import re

        src = open("scripts/simulate_bot.py", encoding="utf-8").read()
        calls = re.findall(r"settle_succeeded_topup\(([^)]*)\)", src)
        self.assertTrue(calls, "expected at least one call in simulate_bot")
        for args in calls:
            self.assertIn("source=", args)


class TestEffectsWiring(unittest.IsolatedAsyncioTestCase):
    def test_effect_constants_match_bot_api_ids(self):
        self.assertEqual(EFFECT_CONFETTI, "5046509860389126442")
        self.assertEqual(EFFECT_LIKE, "5107584321108051014")
        self.assertEqual(EFFECT_FIRE, "5104841245755180586")
        self.assertEqual(EFFECT_LIGHTNING, EFFECT_FIRE)

    async def test_render_device_screen_effect_passthrough(self):
        """T-04: creation card renders as a NEW message carrying the FIRE effect."""
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
                message_effect_id=EFFECT_FIRE,
            )

        kwargs = mock_hub.call_args.kwargs
        self.assertEqual(kwargs.get("message_effect_id"), EFFECT_FIRE)
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

        with patch.object(pr.MaintenanceService, "can_user_perform_action", AsyncMock(return_value=True)), \
             patch("services.tariff_change_quote.create_tariff_change_quote", AsyncMock(return_value=quote_result)), \
             patch("bot.handlers.payment.purchase_routes.render_hub", new=AsyncMock()) as mock_hub:
            await pr.resume_purchase_after_topup(callback, AsyncMock(), db_user=db_user)

        mock_hub.assert_awaited_once()
        args = mock_hub.call_args[0]
        self.assertEqual(args[2], texts.PAYMENT_SHOWCASE)
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
        alt_btn = next(b for b in flat if b.callback_data and b.callback_data.startswith("alt_connection"))
        self.assertIsNone(alt_btn.style)
        delete_btn = next(b for b in flat if b.callback_data and b.callback_data.startswith("request_delete_device"))
        self.assertEqual(delete_btn.style, "danger")

        alt = self._roundtrip(get_alt_connection_keyboard(7))
        alt_flat = [btn for row in alt.inline_keyboard for btn in row]
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


class TestDbReadFailureSemantics(unittest.IsolatedAsyncioTestCase):
    """P1#1: DB READ FAILURE != EMPTY HUB."""

    async def test_read_failure_does_not_create_replacement_hub(self):
        bot = MagicMock()
        bot.send_message = AsyncMock()
        bot.edit_message_text = AsyncMock()
        bot.delete_message = AsyncMock()

        with patch("utils.telegram._load_hub_ids_from_db", new=AsyncMock(side_effect=RuntimeError("db down"))), \
             patch("utils.telegram._store_hub_id_in_db", new=AsyncMock()):
            with self.assertRaises(RuntimeError):
                await render_hub(bot, chat_id=1, text="Menu", trigger_message_id=42)

        bot.send_message.assert_not_awaited()
        bot.edit_message_text.assert_not_awaited()
        bot.delete_message.assert_not_awaited()  # trigger must NOT be deleted on fake empty state

    async def test_read_failure_recovers_when_db_available(self):
        bot = MagicMock()
        bot.edit_message_text = AsyncMock()

        load = AsyncMock(side_effect=[RuntimeError("db down"), [55]])
        with patch("utils.telegram._load_hub_ids_from_db", new=load), \
             patch("utils.telegram._store_hub_id_in_db", new=AsyncMock()), \
             patch("utils.telegram._delete_hub_messages", new=AsyncMock()):
            with self.assertRaises(RuntimeError):
                await render_hub(bot, chat_id=1, text="Menu")
            mid = await render_hub(bot, chat_id=1, text="Menu")

        self.assertEqual(mid, 55)
        bot.edit_message_text.assert_awaited_once()


class TestStoreFailureCleanup(unittest.IsolatedAsyncioTestCase):
    """P1#2: delivered-but-unstored messages must be cleaned before failure escapes."""

    async def test_document_store_failure_self_cleans(self):
        from utils.telegram import _append_hub_document_unlocked

        bot = MagicMock()
        msg = MagicMock(message_id=77)
        bot.send_document = AsyncMock(return_value=msg)
        bot.delete_message = AsyncMock()

        with patch("utils.telegram._store_hub_id_in_db", new=AsyncMock(side_effect=RuntimeError("db down"))):
            with self.assertRaises(RuntimeError):
                await asyncio.wait_for(
                    _append_hub_document_unlocked(bot, chat_id=4, document=MagicMock()),
                    timeout=2,
                )

        bot.delete_message.assert_awaited_with(chat_id=4, message_id=77)

    async def test_photo_store_failure_self_cleans(self):
        from utils.telegram import send_hub_photo

        bot = MagicMock()
        msg = MagicMock(message_id=88)
        bot.send_photo = AsyncMock(return_value=msg)
        bot.delete_message = AsyncMock()

        with patch("utils.telegram._load_hub_ids_from_db", new=AsyncMock(return_value=[])), \
             patch("utils.telegram._store_hub_id_in_db", new=AsyncMock(side_effect=RuntimeError("db down"))):
            with self.assertRaises(RuntimeError):
                await asyncio.wait_for(
                    send_hub_photo(bot, chat_id=4, photo=MagicMock()),
                    timeout=2,
                )

        bot.delete_message.assert_awaited_with(chat_id=4, message_id=88)

    async def test_multi_part_store_failure_cleans_current_part_too(self):
        bot = MagicMock()
        responses = [MagicMock(message_id=11), MagicMock(message_id=12)]

        async def fake_send(**kwargs):
            if len(responses) == 0:
                raise RuntimeError("telegram dropped")
            return responses.pop(0)

        bot.send_message = AsyncMock(side_effect=fake_send)

        store_calls = []

        async def fake_store(chat_id, message_id, **kwargs):
            store_calls.append(message_id)
            if message_id == 12:
                raise RuntimeError("db down")

        long_text = "\n".join(f"row {i}" for i in range(600))

        with patch("utils.telegram._load_hub_ids_from_db", new=AsyncMock(return_value=[])), \
             patch("utils.telegram._store_hub_id_in_db", new=AsyncMock(side_effect=fake_store)), \
             patch("utils.telegram._delete_hub_messages", new=AsyncMock()) as mock_del:
            with self.assertRaises(RuntimeError):
                await render_hub(bot, chat_id=6, text=long_text)

        self.assertEqual(store_calls, [11, 12])          # both stores attempted
        deleted = mock_del.call_args[0][2]
        self.assertEqual(sorted(deleted), [11, 12])      # BOTH cleaned (append-before-store ordering)

    async def test_edit_adopted_trigger_store_failure_deletes_adopted(self):
        bot = MagicMock()
        bot.edit_message_text = AsyncMock()
        bot.delete_message = AsyncMock()

        with patch("utils.telegram._load_hub_ids_from_db", new=AsyncMock(return_value=[])), \
             patch("utils.telegram._store_hub_id_in_db", new=AsyncMock(side_effect=RuntimeError("db down"))):
            with self.assertRaises(RuntimeError):
                await render_hub(bot, chat_id=7, text="X", trigger_message_id=9)

        bot.delete_message.assert_awaited_with(chat_id=7, message_id=9)

    async def test_edit_tracked_target_store_failure_keeps_message(self):
        bot = MagicMock()
        bot.edit_message_text = AsyncMock()
        bot.delete_message = AsyncMock()

        with patch("utils.telegram._load_hub_ids_from_db", new=AsyncMock(return_value=[100])), \
             patch("utils.telegram._store_hub_id_in_db", new=AsyncMock(side_effect=RuntimeError("db down"))):
            with self.assertRaises(RuntimeError):
                await render_hub(bot, chat_id=7, text="X", trigger_message_id=100)

        # Target is already durable+visible; deleting it would destroy user's screen.
        for call in bot.delete_message.await_args_list:
            self.assertNotEqual(call.kwargs.get("message_id"), 100)

    async def test_cancellation_after_send_still_cleans(self):
        bot = MagicMock()
        responses = [MagicMock(message_id=21), None]

        async def fake_send(**kwargs):
            if len(responses) == 1:
                raise asyncio.CancelledError()
            return responses.pop(0)

        bot.send_message = AsyncMock(side_effect=fake_send)
        long_text = "\n".join(f"row {i} padding padding" for i in range(600))

        with patch("utils.telegram._load_hub_ids_from_db", new=AsyncMock(return_value=[])), \
             patch("utils.telegram._store_hub_id_in_db", new=AsyncMock()), \
             patch("utils.telegram._delete_hub_messages", new=AsyncMock()) as mock_del:
            with self.assertRaises(asyncio.CancelledError):
                await render_hub(bot, chat_id=8, text=long_text)

        mock_del.assert_awaited_once()
        self.assertIn(21, mock_del.call_args[0][2])

    async def test_concurrent_renders_same_chat_serialize(self):
        import utils.telegram as tg

        tg._hub_cache.pop(12, None)
        active = {"n": 0, "max": 0}

        async def fake_load(chat_id, session=None):
            # Simulates DB latency inside the critical section so overlapping
            # renders WOULD interleave if the per-chat lock were missing.
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
            await asyncio.sleep(0.03)
            active["n"] -= 1
            return []

        bot = MagicMock()
        seq = iter(range(100, 200))

        def make_msg():
            m = MagicMock()
            m.message_id = next(seq)
            return m

        bot.send_message = AsyncMock(side_effect=lambda **kw: make_msg())

        with patch("utils.telegram._load_hub_ids_from_db", new=AsyncMock(side_effect=fake_load)), \
             patch("utils.telegram._store_hub_id_in_db", new=AsyncMock()), \
             patch("utils.telegram._delete_hub_messages", new=AsyncMock()):
            await asyncio.wait_for(
                asyncio.gather(
                    *[render_hub(bot, chat_id=12, text=f"menu {i}") for i in range(3)]
                ),
                timeout=5,
            )

        self.assertLessEqual(active["max"], 1)  # strict serialization: no overlap
        self.assertEqual(len(tg._hub_cache[12]["ids"]), 1)


class TestDocumentOrdering(unittest.IsolatedAsyncioTestCase):
    """P1: send -> durable store -> THEN delete old hub (never leave chat without a hub)."""

    async def test_document_preserves_old_hub_when_store_fails(self):
        from utils.telegram import send_hub_document

        bot = MagicMock()
        msg = MagicMock(message_id=200)
        bot.send_document = AsyncMock(return_value=msg)
        bot.delete_message = AsyncMock()

        with patch("utils.telegram._load_hub_ids_from_db", new=AsyncMock(return_value=[100])), \
             patch("utils.telegram._store_hub_id_in_db", new=AsyncMock(side_effect=RuntimeError("db down"))):
            with self.assertRaises(RuntimeError):
                await asyncio.wait_for(
                    send_hub_document(bot, chat_id=4, document=MagicMock()),
                    timeout=2,
                )

        deleted = [c.kwargs.get("message_id") for c in bot.delete_message.await_args_list]
        self.assertIn(200, deleted)        # NEW message cleaned up
        self.assertNotIn(100, deleted)     # OLD hub left intact

    async def test_photo_preserves_old_hub_when_store_fails(self):
        from utils.telegram import send_hub_photo

        bot = MagicMock()
        msg = MagicMock(message_id=210)
        bot.send_photo = AsyncMock(return_value=msg)
        bot.delete_message = AsyncMock()

        with patch("utils.telegram._load_hub_ids_from_db", new=AsyncMock(return_value=[100])), \
             patch("utils.telegram._store_hub_id_in_db", new=AsyncMock(side_effect=RuntimeError("db down"))):
            with self.assertRaises(RuntimeError):
                await asyncio.wait_for(
                    send_hub_photo(bot, chat_id=4, photo=MagicMock()),
                    timeout=2,
                )

        deleted = [c.kwargs.get("message_id") for c in bot.delete_message.await_args_list]
        self.assertIn(210, deleted)
        self.assertNotIn(100, deleted)


class TestEffectDurability(unittest.IsolatedAsyncioTestCase):
    """P2: clean-hub-after-effect invariant must survive process restarts."""

    async def test_cold_start_effect_message_gets_clean_replacement(self):
        import utils.telegram as tg
        from contextlib import asynccontextmanager

        tg._hub_cache.pop(31, None)  # simulate restart: empty RAM cache

        bot = MagicMock()
        bot.edit_message_text = AsyncMock()
        bot.send_message = AsyncMock(return_value=MagicMock(message_id=501))
        bot.delete_message = AsyncMock()

        fake_session = AsyncMock()
        @asynccontextmanager
        async def fake_scope():
            yield fake_session

        with patch.object(tg, "session_scope", fake_scope), \
             patch.object(tg.hub_repo, "get_hub_message_ids", AsyncMock(return_value=[500])), \
             patch.object(tg.hub_repo, "get_latest_effect_message_id", AsyncMock(return_value=500)), \
             patch("utils.telegram._store_hub_id_in_db", new=AsyncMock()), \
             patch("utils.telegram._remove_hub_ids_from_db", new=AsyncMock(return_value=[])), \
             patch("utils.telegram._delete_hub_messages", new=AsyncMock()) as mock_del:
            await render_hub(bot, chat_id=31, text="Balance menu")

        # Effect-carrying message 500 must be REPLACED, not edited in place.
        bot.edit_message_text.assert_not_awaited()
        bot.send_message.assert_awaited_once()
        self.assertIsNone(bot.send_message.call_args.kwargs.get("message_effect_id"))
        self.assertEqual(mock_del.call_args[0][2], [500])

    async def test_store_persists_effect_flag_for_first_part(self):
        bot = MagicMock()
        bot.send_message = AsyncMock(side_effect=lambda **kw: MagicMock(message_id=601))

        with patch("utils.telegram._load_hub_ids_from_db", new=AsyncMock(return_value=[])), \
             patch("utils.telegram._store_hub_id_in_db", new=AsyncMock()) as mock_store, \
             patch("utils.telegram._delete_hub_messages", new=AsyncMock()):
            await render_hub(
                bot, chat_id=41, text="Success!",
                message_effect_id="5046509860389126442",
            )

        mock_store.assert_awaited_once()
        self.assertTrue(mock_store.call_args.kwargs.get("is_effect"))

    async def test_store_without_effect_writes_false_flag(self):
        bot = MagicMock()
        bot.send_message = AsyncMock(side_effect=lambda **kw: MagicMock(message_id=602))

        with patch("utils.telegram._load_hub_ids_from_db", new=AsyncMock(return_value=[])), \
             patch("utils.telegram._store_hub_id_in_db", new=AsyncMock()) as mock_store, \
             patch("utils.telegram._delete_hub_messages", new=AsyncMock()):
            await render_hub(bot, chat_id=41, text="Plain menu")

        self.assertFalse(mock_store.call_args.kwargs.get("is_effect"))


class TestRemoveFailureConsistency(unittest.IsolatedAsyncioTestCase):
    """P2: DB removal failure must invalidate cache instead of hiding DB rows."""

    async def test_remove_failure_invalidates_cache_and_raises(self):
        import utils.telegram as tg

        tg._hub_cache[15] = {"ids": [300], "effect_msg_id": None}
        bot = MagicMock()
        bot.delete_message = AsyncMock()  # telegram-side deletion succeeds

        with patch("utils.telegram.session_scope") as bad_scope, \
             patch("utils.telegram.logger.warning"):
            bad_scope.side_effect = RuntimeError("remove failed")
            with self.assertRaises(RuntimeError):
                await asyncio.wait_for(
                    tg._delete_hub_messages(bot, chat_id=15, msg_ids=[300]),
                    timeout=2,
                )

        self.assertNotIn(15, tg._hub_cache)  # cache invalidated -> next load reads DB
        tg._hub_cache.pop(15, None)


class TestAltConnectionFailClosed(unittest.IsolatedAsyncioTestCase):
    async def test_ambiguous_vpn_delivery_aborts_and_preserves_old_hub(self):
        """P1: unknown-delivery .vpn must abort alt_connection, keep old hub intact."""
        import asyncio as aio

        from aiogram.exceptions import TelegramNetworkError

        from bot.handlers.connection.device_view_routes import alt_connection

        bot = MagicMock()
        bot.send_document = AsyncMock(
            side_effect=TelegramNetworkError(MagicMock(), "HTTP Client says - Request timeout error")
        )
        bot.send_message = AsyncMock()
        bot.delete_message = AsyncMock()

        callback = MagicMock()
        callback.data = "alt_connection:42"
        callback.bot = bot
        callback.message = MagicMock()
        callback.message.chat = MagicMock(id=100)
        callback.message.message_id = 9
        callback.from_user = MagicMock(id=10)
        callback.answer = AsyncMock()

        profile = SimpleNamespace(
            id=42, user_id=10, server_id=1, device_name="Dev #1",
            provisioning_status="active", peer_id="p1",
            raw_config="vpn://valid", traffic_down=0, traffic_up=0,
            last_connected=None, is_active=True,
        )
        db_user = SimpleNamespace(id=10, telegram_id=100)
        server = SimpleNamespace(id=1, country_flag="🇩🇪", name="Germany", protocol="amneziawg2")

        session = AsyncMock()

        async def _ok(*a, **k):
            return None

        with patch("bot.handlers.connection.device_view_routes.get_profile_by_id", AsyncMock(return_value=profile)), \
             patch("bot.handlers.connection.device_view_routes.SubscriptionService.check_access", AsyncMock(return_value=True)), \
             patch("bot.handlers.connection.device_view_routes.can_show_config_actions", return_value=True), \
             patch("bot.handlers.connection.device_view_routes.get_server_by_id", AsyncMock(return_value=server)), \
             patch("bot.handlers.connection.device_view_routes.decode_vpn_uri_to_json", return_value={"h": "1"}), \
             patch("bot.handlers.connection.device_view_routes.customize_vpn_config_dict", return_value={}), \
             patch("bot.handlers.connection.device_view_routes.build_vpn_file_from_dict", return_value="V"), \
             patch("bot.handlers.connection.device_view_routes.build_conf_file_from_dict", return_value="C"), \
             patch("utils.telegram._load_hub_ids_from_db", AsyncMock(return_value=[700])), \
             patch("utils.telegram._store_hub_id_in_db", AsyncMock()), \
             patch("utils.telegram._remove_hub_ids_from_db", AsyncMock()), \
             patch("utils.telegram._delete_hub_messages", new=AsyncMock()) as mock_del:
            with self.assertRaises(TelegramNetworkError):
                await aio.wait_for(
                    alt_connection(callback, AsyncMock(), session, db_user),
                    timeout=2,
                )

        self.assertEqual(bot.send_document.await_count, 1)   # aborted after FIRST doc
        bot.send_message.assert_not_awaited()                 # no guide attempt
        mock_del.assert_not_awaited()                         # old hub [700] preserved


class TestIntegrationsAndCleanupVerification(unittest.TestCase):
    def test_integrations_all_matches_defined_attributes(self):
        """Verify integrations.__all__ contains only actual attributes and imports cleanly."""
        import integrations
        for name in integrations.__all__:
            self.assertTrue(
                hasattr(integrations, name),
                f"integrations.__all__ contains missing attribute '{name}'",
            )

    def test_caddyfile_has_no_legacy_endpoints(self):
        """Verify Caddyfile and Caddyfile.ci do not contain deleted legacy endpoints."""
        from pathlib import Path
        for fname in ("Caddyfile", "Caddyfile.ci"):
            caddyfile_path = Path(__file__).resolve().parent.parent / fname
            content = caddyfile_path.read_text(encoding="utf-8")
            self.assertNotIn("/amnezia/open", content, f"{fname} contains /amnezia/open")
            self.assertNotIn("/sub/", content, f"{fname} contains /sub/")
            self.assertNotIn("/subscription/", content, f"{fname} contains /subscription/")

    def test_device_rename_routes_does_not_use_payment_cancel(self):
        """Verify device_rename_routes does not use BTN_PAYMENT_CANCEL."""
        from pathlib import Path
        routes_path = Path(__file__).resolve().parent.parent / "bot" / "handlers" / "connection" / "device_rename_routes.py"
        content = routes_path.read_text(encoding="utf-8")
        self.assertNotIn("BTN_PAYMENT_CANCEL", content)

    def test_format_subscription_date_friendly(self):
        """Verify format_subscription_date returns human-friendly Russian date."""
        from datetime import datetime, timezone
        from bot.formatters import format_subscription_date
        from utils.datetime_helpers import now_msk

        now = now_msk()
        cur_year_dt = datetime(now.year, 9, 25, 12, 0, tzinfo=timezone.utc)
        res = format_subscription_date(cur_year_dt)
        self.assertIn("25", res)
        self.assertIn("сентября", res)
        self.assertNotIn(str(now.year), res)

        next_year_dt = datetime(now.year + 1, 9, 25, 12, 0, tzinfo=timezone.utc)
        res_next = format_subscription_date(next_year_dt)
        self.assertIn(str(now.year + 1), res_next)

        self.assertEqual(format_subscription_date(None), "—")

    def test_tariff_showcase_keyboard_has_starting_price(self):
        """Verify tariff showcase buttons display starting price."""
        from types import SimpleNamespace
        from bot.keyboards.payment import get_tariff_showcase_keyboard

        tariffs = {
            2: [SimpleNamespace(id=1, price_rub=150, duration_days=30), SimpleNamespace(id=2, price_rub=400, duration_days=90)],
            5: [SimpleNamespace(id=3, price_rub=300, duration_days=30)],
        }
        kb = get_tariff_showcase_keyboard(tariffs)
        btn_texts = [btn.text for row in kb.inline_keyboard for btn in row]
        self.assertTrue(any("150 ₽" in t for t in btn_texts))
        self.assertTrue(any("300 ₽" in t for t in btn_texts))

    def test_change_tariff_keyboard_back_target(self):
        """Verify change tariff back button returns to subscription when active."""
        from types import SimpleNamespace
        from bot.keyboards.payment import get_change_tariff_keyboard

        tariffs = [SimpleNamespace(id=1, device_limit=2), SimpleNamespace(id=2, device_limit=5)]
        kb_active = get_change_tariff_keyboard(tariffs, current_limit=2, is_subscription_active=True)
        back_active = [btn for row in kb_active.inline_keyboard for btn in row if btn.callback_data in ("menu_subscription", "back_to_main_menu")]
        self.assertEqual(back_active[0].callback_data, "menu_subscription")

        kb_inactive = get_change_tariff_keyboard(tariffs, current_limit=2, is_subscription_active=False)
        back_inactive = [btn for row in kb_inactive.inline_keyboard for btn in row if btn.callback_data in ("menu_subscription", "back_to_main_menu")]
        self.assertEqual(back_inactive[0].callback_data, "back_to_main_menu")

    def test_device_keyboard_layout_and_button_order(self):
        """Verify device card keyboard places rename/instructions in row 1, alt connection in row 2."""
        from bot.keyboards.device import get_device_keyboard

        kb = get_device_keyboard(profile_id=42, config_ready=True, show_delete=True)
        rows = kb.inline_keyboard
        self.assertEqual(len(rows[0]), 2)  # Rename, Instructions
        self.assertTrue(rows[0][0].callback_data.startswith("rename_device"))
        self.assertTrue(rows[0][1].callback_data.startswith("support_help"))

        self.assertEqual(len(rows[1]), 1)  # Alt connection
        self.assertTrue(rows[1][0].callback_data.startswith("alt_connection"))
        self.assertIsNone(rows[1][0].style)  # Not primary!


if __name__ == "__main__":
    unittest.main()
