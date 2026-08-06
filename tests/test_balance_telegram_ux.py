import unittest
from pathlib import Path
from types import SimpleNamespace

from alembic.config import Config
from alembic.script import ScriptDirectory

from bot.handlers.payment.balance_routes import topup_presets
from bot.keyboards.payment import (
    get_balance_amounts_keyboard,
    get_balance_keyboard,
    get_topup_payment_keyboard,
    get_topup_waiting_keyboard,
)
from bot.texts import get_text
from database.models import Payment


def callbacks(markup) -> list[str]:
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


class BalanceTelegramUXTests(unittest.TestCase):
    def test_presets_come_from_unique_active_tariff_prices(self):
        tariffs = [
            SimpleNamespace(price_rub=499, is_active=True),
            SimpleNamespace(price_rub=149, is_active=True),
            SimpleNamespace(price_rub=499, is_active=True),
            SimpleNamespace(price_rub=1001, is_active=True),
            SimpleNamespace(price_rub=299, is_active=True),
            SimpleNamespace(price_rub=799, is_active=True),
            SimpleNamespace(price_rub=999, is_active=True),
            SimpleNamespace(price_rub=399, is_active=True),
            SimpleNamespace(price_rub=599, is_active=True),
            SimpleNamespace(price_rub=699, is_active=True),
        ]
        tariffs[4].is_active = False
        settings = SimpleNamespace(
            BALANCE_MAX_PRESET_RUB=1000,
            BALANCE_MAX_PRESET_OPTIONS=6,
        )
        self.assertEqual(
            topup_presets(tariffs, settings),
            [149, 399, 499, 599, 699, 799],
        )

    def test_balance_screen_always_has_topup_history_and_back(self):
        self.assertEqual(
            callbacks(get_balance_keyboard()),
            ["balance_topup", "balance_history", "back_to_main_menu"],
        )
        self.assertEqual(
            callbacks(get_balance_keyboard(has_visible_topup=True)),
            [
                "balance_resume_topup",
                "balance_history",
                "back_to_main_menu",
            ],
        )

    def test_balance_history_keyboard_pagination(self):
        from bot.keyboards import get_balance_history_keyboard, get_referrals_list_keyboard
        # Single page -> only back button
        self.assertEqual(callbacks(get_balance_history_keyboard(1, 1)), ["menu_balance"])
        # Multiple pages -> back, indicator, forward, back to menu
        self.assertEqual(
            callbacks(get_balance_history_keyboard(1, 3)),
            ["ignore", "ignore", "balance_history:2", "menu_balance"],
        )
        self.assertEqual(
            callbacks(get_balance_history_keyboard(2, 3)),
            ["balance_history:1", "ignore", "balance_history:3", "menu_balance"],
        )

    def test_referrals_list_keyboard_pagination(self):
        from bot.keyboards import get_referrals_list_keyboard
        self.assertEqual(callbacks(get_referrals_list_keyboard(1, 1)), ["referral"])
        self.assertEqual(
            callbacks(get_referrals_list_keyboard(1, 2)),
            ["ignore", "ignore", "referrals_list:2", "referral"],
        )

    def test_amount_keyboard_always_has_custom_amount(self):

        values = callbacks(get_balance_amounts_keyboard([149, 499]))
        self.assertEqual(
            values,
            [
                "balance_create:149",
                "balance_create:499",
                "balance_custom_amount",
                "menu_balance",
            ],
        )

    def test_topup_controls_match_hidden_payment_semantics(self):
        waiting = callbacks(get_topup_waiting_keyboard(42))
        ready = callbacks(get_topup_payment_keyboard("https://example.com", 42))
        expected = [
            "balance_check:42",
            "balance_cancel:42",
            "balance_later:42",
        ]
        self.assertEqual(waiting, expected)
        self.assertEqual(ready, expected)

    def test_main_and_profile_templates_require_visible_balance(self):
        for key in ("HUB_HEADER", "PROFILE_TEXT_ACTIVE", "PROFILE_TEXT_INACTIVE"):
            text = get_text(key)
            self.assertTrue("{balance}" in text or "{real_balance}" in text)


    def test_payment_url_delivery_has_durable_marker(self):
        self.assertIn("payment_url_notified_at", Payment.__table__.c)
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        self.assertEqual(scripts.get_heads(), ["0001_clean_baseline"])

    def test_direct_tariff_yookassa_route_is_not_registered(self):
        router_source = (
            Path(__file__).parents[1]
            / "bot"
            / "handlers"
            / "payment"
            / "__init__.py"
        ).read_text(encoding="utf-8")
        keyboard_source = (
            Path(__file__).parents[1] / "bot" / "keyboards" / "payment.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("yookassa_routes", router_source)
        self.assertNotIn("pay_yookassa:", keyboard_source)
        self.assertFalse(
            (Path(__file__).parents[1] / "services" / "payment_service").exists()
        )

class BalanceTelegramUXAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_and_render_topup_handles_callback_and_none_targets(self):
        from unittest.mock import AsyncMock, MagicMock
        from aiogram.types import CallbackQuery
        from bot.handlers.payment.balance_routes import _create_and_render_topup

        # target is None
        await _create_and_render_topup(None, MagicMock(), MagicMock(), 100)

        # target is CallbackQuery with None message
        cb = MagicMock(spec=CallbackQuery)
        cb.bot = MagicMock()
        cb.bot.get_me = AsyncMock(return_value=MagicMock(username="test"))
        cb.message = None
        await _create_and_render_topup(cb, MagicMock(), MagicMock(), 100)


if __name__ == "__main__":
    unittest.main()
