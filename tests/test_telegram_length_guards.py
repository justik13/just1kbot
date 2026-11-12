import unittest
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import CopyTextButton, DisabledButton, InlineKeyboardButton, InlineKeyboardMarkup
from utils.telegram import sanitize_reply_markup


class TestTelegramBotApi103KeyboardAudit(unittest.IsolatedAsyncioTestCase):
    """Universal audit of remaining notification keyboards against Telegram Bot API 10.3 constraints."""

    def test_sanitize_reply_markup_preserves_valid(self):
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Normal", callback_data="btn_ok")],
                [InlineKeyboardButton(text="Link", copy_text=CopyTextButton(text="https://short.link"))],
            ]
        )
        res = sanitize_reply_markup(kb)
        self.assertIs(res, kb)

    def test_sanitize_reply_markup_neutralizes_long_key(self):
        long_key = "vless://" + "A" * 1200
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Long key", copy_text=CopyTextButton(text=long_key))]]
        )
        res = sanitize_reply_markup(kb)
        btn = res.inline_keyboard[0][0]
        self.assertIsNone(btn.copy_text)
        self.assertEqual(btn.callback_data, "alert:copy_too_long")

    def test_sanitize_reply_markup_neutralizes_empty_copy_text(self):
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Empty", copy_text=CopyTextButton(text=""))]]
        )
        res = sanitize_reply_markup(kb)
        btn = res.inline_keyboard[0][0]
        self.assertIsNone(btn.copy_text)
        self.assertEqual(btn.callback_data, "alert:copy_too_long")

    def test_sanitize_reply_markup_neutralizes_oversized_callback_data_to_ignore(self):
        oversized_cb = "action:" + "я" * 50  # 7 + 100 = 107 bytes
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Long", callback_data=oversized_cb)]]
        )
        res = sanitize_reply_markup(kb)
        btn = res.inline_keyboard[0][0]
        self.assertEqual(btn.callback_data, "ignore")

    def test_sanitize_reply_markup_splits_rows_exceeding_8_buttons(self):
        buttons = [InlineKeyboardButton(text=f"B{i}", callback_data=f"b{i}") for i in range(12)]
        kb = InlineKeyboardMarkup(inline_keyboard=[buttons])
        res = sanitize_reply_markup(kb)
        self.assertEqual(len(res.inline_keyboard), 2)
        self.assertEqual(len(res.inline_keyboard[0]), 8)
        self.assertEqual(len(res.inline_keyboard[1]), 4)

    def test_sanitize_reply_markup_replaces_empty_button_label(self):
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="   ", callback_data="test")]]
        )
        res = sanitize_reply_markup(kb)
        btn = res.inline_keyboard[0][0]
        self.assertEqual(btn.text, "—")

    def test_sanitize_reply_markup_preserves_disabled_button(self):
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="1/3", disabled=DisabledButton())]]
        )
        res = sanitize_reply_markup(kb)
        btn = res.inline_keyboard[0][0]
        self.assertEqual(btn.text, "1/3")
        self.assertIsInstance(btn.disabled, DisabledButton)
        self.assertIsNone(btn.callback_data)

    def test_sanitize_reply_markup_caps_buttons_at_100(self):
        # 15 rows with 8 buttons each = 120 buttons total
        rows = [
            [InlineKeyboardButton(text=f"B{r}_{c}", callback_data=f"d:{r}_{c}") for c in range(8)]
            for r in range(15)
        ]
        kb = InlineKeyboardMarkup(inline_keyboard=rows)
        res = sanitize_reply_markup(kb)
        total_buttons = sum(len(row) for row in res.inline_keyboard)
        self.assertEqual(total_buttons, 100)
        # 12 rows of 8 buttons (= 96 buttons) + 1 row of 4 buttons (= 100 buttons total)
        self.assertEqual(len(res.inline_keyboard), 13)
        self.assertEqual(len(res.inline_keyboard[-1]), 4)
        self.assertEqual(res.inline_keyboard[0][0].text, "B0_0")
        self.assertEqual(res.inline_keyboard[-1][-1].text, "B12_3")

    def test_sanitize_reply_markup_does_not_convert_disabled_button_to_alert_copy_too_long(self):
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton.model_construct(
                text="Disabled",
                disabled=DisabledButton(),
                copy_text=CopyTextButton(text="X" * 300),
            )]]
        )
        res = sanitize_reply_markup(kb)
        btn = res.inline_keyboard[0][0]
        self.assertEqual(btn.text, "Disabled")
        self.assertIsInstance(btn.disabled, DisabledButton)
        self.assertIsNone(btn.copy_text)
        self.assertIsNone(btn.callback_data)

    async def test_alert_copy_too_long_callback_shows_alert(self):
        from bot import texts
        from bot.handlers.notifications import alert_copy_too_long_callback

        callback = MagicMock()
        callback.answer = AsyncMock()

        await alert_copy_too_long_callback(callback)

        callback.answer.assert_awaited_once_with(
            texts.ALERT_COPY_TEXT_TOO_LONG,
            show_alert=True,
        )

    def test_notification_keyboards_audit_bot_api_10_3(self):
        """Audit remaining notification keyboards against Telegram Bot API 10.3 constraints."""
        from bot.keyboards.notifications import (
            get_devices_deleted_keyboard,
            get_dismiss_alert_keyboard,
            get_dismiss_notification_keyboard,
            get_node_monitor_alert_keyboard,
            get_post_expiry_keyboard,
            get_pre_expiry_keyboard,
            get_referral_bonus_keyboard,
            get_traffic_alert_keyboard,
        )

        keyboards = [
            get_dismiss_notification_keyboard(),
            get_dismiss_alert_keyboard(),
            get_devices_deleted_keyboard(),
            get_node_monitor_alert_keyboard(1).as_markup(),
            get_post_expiry_keyboard(),
            get_pre_expiry_keyboard(),
            get_referral_bonus_keyboard(),
            get_traffic_alert_keyboard(12345),
        ]

        for idx, kb in enumerate(keyboards):
            self.assertEqual(len(kb.inline_keyboard), 1, f"KB #{idx}: expected single row")
            self.assertEqual(len(kb.inline_keyboard[0]), 1, f"KB #{idx}: expected single dismiss button")
            for row in kb.inline_keyboard:
                self.assertLessEqual(len(row), 8, f"KB #{idx}: row has {len(row)} buttons (max 8)")
                for btn in row:
                    self.assertTrue(btn.text and btn.text.strip(), f"KB #{idx}: empty button text")
                    if btn.callback_data is not None:
                        cb_bytes = len(btn.callback_data.encode("utf-8"))
                        self.assertLessEqual(cb_bytes, 64, f"KB #{idx}: callback_data {cb_bytes} bytes exceeds 64")

            sanitized = sanitize_reply_markup(kb)
            for row in sanitized.inline_keyboard:
                self.assertLessEqual(len(row), 8)
                for btn in row:
                    self.assertTrue(btn.text and btn.text.strip())
                    if btn.callback_data is not None:
                        self.assertLessEqual(len(btn.callback_data.encode("utf-8")), 64)


if __name__ == "__main__":
    unittest.main()
