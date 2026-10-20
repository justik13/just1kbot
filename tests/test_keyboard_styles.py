"""Tests for Telegram Bot API button styles validity and compliance."""
from __future__ import annotations

import ast
from pathlib import Path
import unittest

from bot.keyboards import (
    get_dispute_blocked_keyboard,
    get_hub_keyboard,
    get_order_invoice_keyboard,
)
from bot.keyboards.device import (
    get_alt_connection_keyboard,
    get_device_delete_confirm_keyboard,
    get_device_keyboard,
    get_device_migrate_confirm_keyboard,
)

# Telegram Bot API 8.0+ supported InlineKeyboardButton styles:
# "danger" (red), "success" (green), "primary" (blue).
ALLOWED_TELEGRAM_BUTTON_STYLES = {"primary", "success", "danger"}


class TestKeyboardStylesAST(unittest.TestCase):
    """Static analysis of all button styles across the bot codebase."""

    def test_all_button_styles_in_bot_are_valid(self):
        """Parse all Python files in bot/ and ensure every style= argument is an allowed Telegram Bot API style."""
        bot_dir = Path(__file__).resolve().parent.parent / "bot"
        self.assertTrue(bot_dir.is_dir(), f"bot directory not found at {bot_dir}")

        found_styles: list[tuple[str, int, str]] = []

        for py_file in bot_dir.rglob("*.py"):
            with open(py_file, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(py_file))

            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    for keyword in node.keywords:
                        if keyword.arg == "style":
                            # Check if value is a string constant
                            if isinstance(keyword.value, ast.Constant) and isinstance(
                                keyword.value.value, str
                            ):
                                rel_path = py_file.relative_to(bot_dir)
                                found_styles.append((str(rel_path), node.lineno, keyword.value.value))

        self.assertGreater(len(found_styles), 0, "Expected to find style= attributes in bot code")

        for file_path, lineno, style_val in found_styles:
            self.assertIn(
                style_val,
                ALLOWED_TELEGRAM_BUTTON_STYLES,
                f"Invalid button style '{style_val}' found at bot/{file_path}:{lineno}. "
                f"Telegram Bot API only allows {ALLOWED_TELEGRAM_BUTTON_STYLES}.",
            )
            self.assertNotEqual(
                style_val,
                "warning",
                f"Telegram Bot API does not support style='warning' at bot/{file_path}:{lineno}. "
                "Use 'danger', 'success', or 'primary'.",
            )


class TestKeyboardRuntimeStyles(unittest.IsolatedAsyncioTestCase):
    """Runtime inspection of button styles on generated keyboards."""

    def test_order_invoice_keyboard_styles_and_support_url(self):
        """Test get_order_invoice_keyboard button styles and direct support URL."""
        test_pay_url = "https://pay.yookassa.ru/test123"
        test_support_url = "https://t.me/test_support?text=order_help"
        kb = get_order_invoice_keyboard(
            payment_url=test_pay_url,
            order_id="12345678-abcd",
            price=450,
            support_url=test_support_url,
        )

        rows = kb.inline_keyboard
        self.assertEqual(len(rows), 4, "Invoice keyboard must contain 4 rows")

        # Row 0: Pay button (success)
        pay_btn = rows[0][0]
        self.assertEqual(pay_btn.url, test_pay_url)
        self.assertEqual(pay_btn.style, "success")

        # Row 1: Check payment (primary)
        check_btn = rows[1][0]
        self.assertEqual(check_btn.callback_data, "order_check:12345678-abcd")
        self.assertEqual(check_btn.style, "primary")

        # Row 2: Support button (direct URL without wiping invoice)
        support_btn = rows[2][0]
        self.assertEqual(support_btn.url, test_support_url)
        self.assertIsNone(support_btn.callback_data)

        # Row 3: Cancel button (danger)
        cancel_btn = rows[3][0]
        self.assertEqual(cancel_btn.callback_data, "order_cancel:12345678-abcd")
        self.assertEqual(cancel_btn.style, "danger")

        # Verify all button styles in invoice keyboard
        for row in rows:
            for btn in row:
                if btn.style is not None:
                    self.assertIn(btn.style, ALLOWED_TELEGRAM_BUTTON_STYLES)

    def test_dispute_blocked_keyboard_structure(self):
        """Test get_dispute_blocked_keyboard with support URL and back navigation."""
        test_support_url = "https://t.me/test_support?text=dispute"
        kb = get_dispute_blocked_keyboard(
            back_callback="menu_balance",
            support_url=test_support_url,
        )
        rows = kb.inline_keyboard
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0].url, test_support_url)
        self.assertEqual(rows[1][0].callback_data, "menu_balance")

    def test_device_keyboards_styles(self):
        """Test device management keyboards styles."""
        del_kb = get_device_delete_confirm_keyboard(profile_id=42)
        del_rows = del_kb.inline_keyboard
        self.assertEqual(del_rows[0][0].style, "danger")

        migrate_kb = get_device_migrate_confirm_keyboard(profile_id=42, target_server_id=1)
        migrate_rows = migrate_kb.inline_keyboard
        self.assertEqual(migrate_rows[0][0].style, "success")

        alt_kb = get_alt_connection_keyboard(profile_id=42)
        for row in alt_kb.inline_keyboard:
            for btn in row:
                if btn.style is not None:
                    self.assertIn(btn.style, ALLOWED_TELEGRAM_BUTTON_STYLES)

        manage_kb = get_device_keyboard(
            profile_id=42,
            config_ready=True,
            show_delete=True,
        )
        for row in manage_kb.inline_keyboard:
            for btn in row:
                if btn.style is not None:
                    self.assertIn(btn.style, ALLOWED_TELEGRAM_BUTTON_STYLES)

    def test_hub_keyboard_styles(self):
        """Test hub keyboard buttons styles."""
        active_hub = get_hub_keyboard(is_admin=False, is_active=True)
        for row in active_hub.inline_keyboard:
            for btn in row:
                if btn.style is not None:
                    self.assertIn(btn.style, ALLOWED_TELEGRAM_BUTTON_STYLES)

        inactive_hub = get_hub_keyboard(is_admin=False, is_active=False)
        for row in inactive_hub.inline_keyboard:
            for btn in row:
                if btn.style is not None:
                    self.assertIn(btn.style, ALLOWED_TELEGRAM_BUTTON_STYLES)

    def test_order_invoice_keyboard_without_explicit_support_url(self):
        """Test get_order_invoice_keyboard gracefully falls back to default support URL if omitted."""
        kb = get_order_invoice_keyboard(
            payment_url="https://pay.example.com/123",
            order_id="order-fallback-test",
            price=250,
        )
        rows = kb.inline_keyboard
        self.assertEqual(len(rows), 4)
        support_btn = rows[2][0]
        self.assertIsNotNone(support_btn.url)
        self.assertTrue(support_btn.url.startswith("https://t.me/"))

    async def test_connection_hub_vless_reset_hwid_danger_style(self):
        """Test _build_connections_screen assigns danger style to VLESS HWID reset button."""
        import datetime
        from unittest.mock import AsyncMock, patch
        from bot.handlers.connection.common import _build_connections_screen
        from database.models import User, VlessSubscription
        from bot import texts

        user = User(
            id=42,
            telegram_id=12345,
            subscription_end=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=10),
        )
        session = AsyncMock()
        mock_sub = VlessSubscription(id=1, user_id=42, token="token-vless-123")

        with patch("database.repositories.vless_subscription_repo.get_subscription_by_user_id", new_callable=AsyncMock, return_value=mock_sub), \
             patch("database.repositories.vless_subscription_repo.get_active_hwid_count", new_callable=AsyncMock, return_value=2), \
             patch("bot.handlers.connection.common._get_effective_device_limit", new_callable=AsyncMock, return_value=3):
            rendered, _ = await _build_connections_screen(user, session, [], read_only=False)

        self.assertIn('callback_data="vless_sub_reset"', rendered)
        self.assertIn('style="danger"', rendered)
        self.assertIn(texts.BTN_VLESS_RESET_HWID, rendered)

    def test_white_internet_overview_reset_devices_danger_style(self):
        """Test get_white_internet_overview_keyboard assigns danger style to reset devices button."""
        import datetime
        from bot.handlers.white_internet import get_white_internet_overview_keyboard
        from config.enums import WhiteInternetStatus
        from database.models import WhiteInternetSubscription
        from bot import texts

        sub = WhiteInternetSubscription(
            id=10,
            user_id=100,
            token="test-tok-123",
            status=WhiteInternetStatus.ACTIVE,
            device_limit=2,
            is_trial=False,
            expires_at=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=5),
        )
        kb = get_white_internet_overview_keyboard(sub, bot_domain="bot.example.com")
        reset_buttons = [
            btn for row in kb.inline_keyboard for btn in row if btn.callback_data == "wl_reset_devices"
        ]
        self.assertEqual(len(reset_buttons), 1)
        self.assertEqual(reset_buttons[0].text, texts.BTN_WL_RESET_DEVICES)
        self.assertEqual(reset_buttons[0].style, "danger")

    def test_support_handler_contact_button_url_generation(self):
        """Test support contact button generates expected URL with telegram_id."""
        from utils.support import get_support_url
        from bot import texts

        url = get_support_url(text=texts.SUPPORT_GREETING_TEMPLATE.format(telegram_id=999888), username="official_support")
        self.assertTrue(url.startswith("https://t.me/official_support?text="))
        self.assertIn("999888", url)


class TestSupportUrlHelpers(unittest.TestCase):
    """Unit tests for utils.support helpers."""

    def test_resolve_support_username_explicit(self):
        from utils.support import _resolve_support_username

        self.assertEqual(_resolve_support_username("test_user"), "test_user")
        self.assertEqual(_resolve_support_username("@test_user"), "test_user")
        self.assertEqual(_resolve_support_username("   @test_user   "), "test_user")

    def test_resolve_support_username_edge_cases(self):
        from utils.support import _resolve_support_username

        # If user passes empty or only '@' or spaces, it must not return empty string
        res_at = _resolve_support_username("@")
        self.assertTrue(bool(res_at))
        self.assertNotEqual(res_at, "")

        res_spaces = _resolve_support_username("   ")
        self.assertTrue(bool(res_spaces))
        self.assertNotEqual(res_spaces, "")

    def test_get_support_url_without_text(self):
        from utils.support import get_support_url

        url = get_support_url(username="my_support")
        self.assertEqual(url, "https://t.me/my_support")

    def test_get_support_url_with_prefilled_text(self):
        from utils.support import get_support_url
        from urllib.parse import parse_qs, urlparse

        text = "Здравствуйте! Мой ID: 12345. Вопрос по оплате заказа #ab/cd (450 ₽)."
        url = get_support_url(text=text, username="@my_support")
        self.assertTrue(url.startswith("https://t.me/my_support?text="))

        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        self.assertEqual(params["text"][0], text)


if __name__ == "__main__":
    unittest.main()
