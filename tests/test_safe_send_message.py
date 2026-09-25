import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup, Message

from services.workers import _send_alert
from services.workers.node_monitor import _send_admin_alert_msg
from utils.telegram import safe_send_message


class SafeSendMessageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_bot = AsyncMock()
        self.chat_id = 123456789

    async def test_normal_delivery_success(self):
        """Standard HTML message is delivered directly and returns message_id."""
        fake_msg = MagicMock(spec=Message)
        fake_msg.message_id = 42
        self.mock_bot.send_message.return_value = fake_msg

        res = await safe_send_message(
            self.mock_bot,
            chat_id=self.chat_id,
            text="<b>Hello world</b>",
            parse_mode="HTML",
        )

        self.assertEqual(res, 42)
        self.mock_bot.send_message.assert_called_once()
        kwargs = self.mock_bot.send_message.call_args.kwargs
        self.assertEqual(kwargs["chat_id"], self.chat_id)
        self.assertEqual(kwargs["text"], "<b>Hello world</b>")
        self.assertEqual(kwargs["parse_mode"], "HTML")

    async def test_parse_error_falls_back_to_plain_text(self):
        """If HTML has parse error, safe_send_message strips tags and retries with parse_mode=None."""
        parse_err = TelegramBadRequest(
            method="send_message",
            message="Bad Request: can't parse entities: Unexpected tag <broken> at byte offset 10",
        )
        fake_fallback_msg = MagicMock(spec=Message)
        fake_fallback_msg.message_id = 99

        # First call fails with parse error, second call succeeds
        self.mock_bot.send_message.side_effect = [parse_err, fake_fallback_msg]

        raw_text = "<b>Alert:</b> <broken>Node status &amp; &lt;fail&gt;</broken>"
        res = await safe_send_message(
            self.mock_bot,
            chat_id=self.chat_id,
            text=raw_text,
            parse_mode="HTML",
        )

        self.assertEqual(res, 99)
        self.assertEqual(self.mock_bot.send_message.call_count, 2)

        first_call = self.mock_bot.send_message.call_args_list[0].kwargs
        self.assertEqual(first_call["parse_mode"], "HTML")
        self.assertEqual(first_call["text"], raw_text)

        second_call = self.mock_bot.send_message.call_args_list[1].kwargs
        self.assertIsNone(second_call["parse_mode"])
        # Formatting tags stripped, entity content preserved, HTML unescaped
        self.assertEqual(second_call["text"], "Alert: <broken>Node status & <fail></broken>")

    async def test_forbidden_error_returns_none(self):
        """If bot is blocked by chat, returns None without raising exception."""
        forbidden_err = TelegramForbiddenError(
            method="send_message",
            message="Forbidden: bot was blocked by the user",
        )
        self.mock_bot.send_message.side_effect = forbidden_err

        res = await safe_send_message(
            self.mock_bot,
            chat_id=self.chat_id,
            text="Some message",
        )

        self.assertIsNone(res)

    async def test_unrelated_bad_request_raises(self):
        """Unrelated TelegramBadRequest (e.g. Chat not found) is re-raised."""
        unrelated_err = TelegramBadRequest(
            method="send_message",
            message="Bad Request: chat not found",
        )
        self.mock_bot.send_message.side_effect = unrelated_err

        with self.assertRaises(TelegramBadRequest):
            await safe_send_message(
                self.mock_bot,
                chat_id=self.chat_id,
                text="Some message",
            )

    async def test_message_effect_failure_retries_without_effect(self):
        """If message effect is invalid, retries without effect."""
        effect_err = TelegramBadRequest(
            method="send_message",
            message="Bad Request: message effect id is invalid",
        )
        fake_msg = MagicMock(spec=Message)
        fake_msg.message_id = 77
        self.mock_bot.send_message.side_effect = [effect_err, fake_msg]

        res = await safe_send_message(
            self.mock_bot,
            chat_id=self.chat_id,
            text="<b>Celebration</b>",
            message_effect_id="invalid_effect_123",
        )

        self.assertEqual(res, 77)
        self.assertEqual(self.mock_bot.send_message.call_count, 2)
        second_call = self.mock_bot.send_message.call_args_list[1].kwargs
        self.assertIsNone(second_call["message_effect_id"])

    async def test_message_splitting_for_long_texts(self):
        """Texts longer than 4096 chars are split across multiple messages."""
        part1 = "A" * 3000 + "\n"
        part2 = "B" * 2000
        long_text = part1 + part2

        msg1 = MagicMock(spec=Message)
        msg1.message_id = 101
        msg2 = MagicMock(spec=Message)
        msg2.message_id = 102
        self.mock_bot.send_message.side_effect = [msg1, msg2]

        kb = MagicMock(spec=InlineKeyboardMarkup)
        res = await safe_send_message(
            self.mock_bot,
            chat_id=self.chat_id,
            text=long_text,
            reply_markup=kb,
            message_effect_id="eff1",
        )

        self.assertEqual(res, 102)
        self.assertEqual(self.mock_bot.send_message.call_count, 2)

        # First call has effect, no reply_markup
        first_call = self.mock_bot.send_message.call_args_list[0].kwargs
        self.assertEqual(first_call["message_effect_id"], "eff1")
        self.assertIsNone(first_call["reply_markup"])

        # Second call has reply_markup, no effect
        second_call = self.mock_bot.send_message.call_args_list[1].kwargs
        self.assertIsNone(second_call["message_effect_id"])
        self.assertEqual(second_call["reply_markup"], kb)

    async def test_node_monitor_alert_with_malformed_html(self):
        """Node monitor alert sending succeeds even if alert text has broken HTML."""
        parse_err = TelegramBadRequest(
            method="send_message",
            message="Bad Request: can't parse entities: unclosed tag <server>",
        )
        fake_msg = MagicMock(spec=Message)
        fake_msg.message_id = 555
        self.mock_bot.send_message.side_effect = [parse_err, fake_msg]

        with patch("services.workers.node_monitor.get_settings") as mock_settings:
            mock_settings.return_value.ADMIN_IDS = [100]
            success = await _send_admin_alert_msg(
                self.mock_bot,
                text="<b>Node <server> down!</b>",
            )

        self.assertTrue(success)
        self.assertEqual(self.mock_bot.send_message.call_count, 2)
        # Fallback sent plain text
        self.assertIsNone(self.mock_bot.send_message.call_args_list[1].kwargs["parse_mode"])

    async def test_worker_supervision_send_alert_plain_text_fallback(self):
        """Worker crash alert with unescaped class name in error_type delivers cleanly."""
        parse_err = TelegramBadRequest(
            method="send_message",
            message="Bad Request: can't parse entities: entity <class 'ValueError'> not allowed",
        )
        fake_msg = MagicMock(spec=Message)
        fake_msg.message_id = 666
        self.mock_bot.send_message.side_effect = [parse_err, fake_msg]

        with patch("services.workers.get_settings") as mock_settings:
            mock_settings.return_value.ADMIN_IDS = [100]
            await _send_alert(
                self.mock_bot,
                title="CRITICAL STOP",
                worker="node_monitor",
                failure_count=3,
                error_type="<class 'ValueError'>",
            )

        self.assertEqual(self.mock_bot.send_message.call_count, 2)
        # Verify fallback was delivered with plain text
        second_call = self.mock_bot.send_message.call_args_list[1].kwargs
        self.assertIsNone(second_call["parse_mode"])
        self.assertIn("class 'ValueError'", second_call["text"])
