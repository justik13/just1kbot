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


class TestKeyboardRuntimeStyles(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
