from pathlib import Path
import re
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram import Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, User as TgUser

from bot.handlers.admin import admin_router
from bot.handlers.connection import router as connection_router
from bot.handlers.fallback import router as fallback_router
from bot.handlers.payment import router as payment_router
from bot.handlers.referral import router as referral_router
from bot.handlers.start import router as start_router
from bot.handlers.support import router as support_router
from bot.handlers.white_internet import router as white_internet_router
from utils.telegram import append_hub_message, render_hub


def _get_test_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    routers = [
        start_router,
        referral_router,
        connection_router,
        white_internet_router,
        support_router,
        payment_router,
        admin_router,
        fallback_router,
    ]
    for r in routers:
        r._parent_router = None
        dp.include_router(r)
    return dp


def _collect_all_routers(router) -> list:
    result = [router]
    for sub in getattr(router, "sub_routers", []):
        result.extend(_collect_all_routers(sub))
    return result


class TestRichButtonsCallbacks(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.dp = _get_test_dispatcher()
        cls.all_routers = []
        for r in _collect_all_routers(cls.dp):
            if r is not fallback_router:
                cls.all_routers.append(r)

        repo_root = Path(__file__).resolve().parent.parent
        cb_pattern = re.compile(r'<tg-button\s+[^>]*?type="callback_data"[^>]*?data="([^"]+)"', re.IGNORECASE)

        collected = set()
        for folder in ["bot/texts", "bot/handlers"]:
            for py_file in (repo_root / folder).rglob("*.py"):
                content = py_file.read_text(encoding="utf-8")
                for match in cb_pattern.finditer(content):
                    raw_data = match.group(1)
                    if "{buy_or_sub_action}" in raw_data:
                        collected.add("menu_buy")
                        collected.add("menu_subscription")
                    elif "{amt}" in raw_data:
                        collected.add(raw_data.replace("{amt}", "100"))
                    elif "{" not in raw_data:
                        collected.add(raw_data)

        cls.collected_callbacks = collected

    def test_no_known_dead_callbacks_in_rich_buttons(self):
        """Verify that known broken/hallucinated callback_data strings do not exist in any bot files."""
        dead_callbacks = [
            "create_device",
            "topup_balance",
            "quick_renew",
            "ref_leaderboard",
            "wl_buy_base",
            "wl_activate_trial",
        ]
        bot_dir = Path(__file__).resolve().parent.parent / "bot"
        for py_file in bot_dir.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            for dead in dead_callbacks:
                self.assertNotIn(
                    f'data="{dead}"',
                    content,
                    f"Dead callback '{dead}' found in {py_file.name}",
                )

    async def test_all_tg_button_callbacks_match_registered_handlers(self):
        """Verify every callback_data in every <tg-button> in bot/texts and bot/handlers is handled by a router."""
        self.assertGreater(len(self.collected_callbacks), 0, "Should have found rich button callbacks")

        for cb_data in sorted(self.collected_callbacks):
            cb = CallbackQuery(
                id="test_query",
                from_user=TgUser(id=12345678, is_bot=False, first_name="Tester"),
                chat_instance="test_chat_instance",
                data=cb_data,
            )
            matched = False
            for r in self.all_routers:
                for h in r.callback_query.handlers:
                    res, _ = await h.check(cb)
                    if res:
                        matched = True
                        break
                if matched:
                    break
            self.assertTrue(
                matched,
                f"Callback '{cb_data}' has no matching handler in any registered router!",
            )

    async def test_render_hub_edit_rich_message_fallback_strips_rich_tags(self):
        """When bot.edit_message_text fails on rich message, fallback uses strip_html_tags and parse_mode=None."""
        bot = MagicMock()
        # First call with rich_message fails (e.g. unsupported tags)
        # Second call with stripped text succeeds
        bot.edit_message_text = AsyncMock(
            side_effect=[
                TelegramBadRequest(method=MagicMock(), message="Bad Request: can't parse entities"),
                MagicMock(message_id=100),
            ]
        )

        with patch("utils.telegram._load_hub_ids_from_db", new=AsyncMock(return_value=[100])), \
             patch("utils.telegram._store_hub_id_in_db", new=AsyncMock()), \
             patch("utils.telegram._delete_hub_messages", new=AsyncMock()):

            rich_text = (
                "<h2>Title</h2>\n"
                "<p>Line 1</p>\n"
                '<tg-button-row align="center">\n'
                '  <tg-button type="callback_data" style="primary" data="menu_balance">Topup</tg-button>\n'
                "</tg-button-row>"
            )

            mid = await render_hub(bot, chat_id=12345, text=rich_text)
            self.assertEqual(mid, 100)
            self.assertEqual(bot.edit_message_text.call_count, 2)

            # Check second call (fallback)
            fallback_call = bot.edit_message_text.call_args_list[1]
            self.assertIsNone(fallback_call.kwargs.get("parse_mode"))
            fallback_text = fallback_call.kwargs.get("text")
            self.assertNotIn("<tg-button", fallback_text)
            self.assertNotIn("<h2>", fallback_text)
            self.assertIn("[Topup]", fallback_text)

    async def test_append_hub_message_calls_send_rich_message(self):
        """append_hub_message routes rich text to bot.send_rich_message."""
        bot = MagicMock()
        bot.send_rich_message = AsyncMock(return_value=MagicMock(message_id=555))
        bot.send_message = AsyncMock()

        with patch("utils.telegram._store_hub_id_in_db", new=AsyncMock()):
            rich_text = "<h2>Log</h2><p>Data</p>"
            mid = await append_hub_message(bot, chat_id=12345, text=rich_text)
            self.assertEqual(mid, 555)
            self.assertTrue(bot.send_rich_message.called)
            self.assertFalse(bot.send_message.called)


if __name__ == "__main__":
    unittest.main()
