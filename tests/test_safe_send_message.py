import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup, Message

from services.workers import _send_alert
from services.workers.node_monitor import _send_admin_alert_msg
from utils.telegram import safe_send_message, strip_html_tags


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

    def test_strip_html_tags_with_tg_time_and_expandable_blockquote(self):
        """strip_html_tags strips Telegram formatting tags including tg-time and expandable-blockquote."""
        sample_html = (
            'Server <b>DE-1</b> restarted at <tg-time unix="1710000000" format="f">14:30</tg-time>.\n'
            '<expandable-blockquote>Detailed incident report &amp; diagnostic logs</expandable-blockquote>\n'
            'Visit <a href="https://example.com">dashboard</a> for info.'
        )
        plain = strip_html_tags(sample_html)
        self.assertNotIn("<tg-time", plain)
        self.assertNotIn("</tg-time>", plain)
        self.assertNotIn("<expandable-blockquote>", plain)
        self.assertNotIn("</expandable-blockquote>", plain)
        self.assertNotIn("<b>", plain)
        self.assertIn("Server DE-1 restarted at 14:30.", plain)
        self.assertIn("Detailed incident report & diagnostic logs", plain)
        self.assertIn("Visit dashboard (https://example.com) for info.", plain)

    async def test_extract_msg_id_rejects_bool(self):
        """safe_send_message does not treat boolean True as a message_id."""
        self.mock_bot.send_message.return_value = True

        res = await safe_send_message(
            self.mock_bot,
            chat_id=self.chat_id,
            text="Test message",
        )

        self.assertIsNone(res)

    async def test_traffic_quota_alert_escapes_dangerous_server_name(self):
        """_send_quota_alert escapes dangerous HTML characters in server_name."""
        fake_msg = MagicMock(spec=Message)
        fake_msg.message_id = 777
        self.mock_bot.send_message.return_value = fake_msg

        with patch("services.workers.traffic.get_settings") as mock_settings:
            mock_settings.return_value.ADMIN_IDS = [100]
            from services.workers.traffic import _send_quota_alert

            await _send_quota_alert(
                self.mock_bot,
                telegram_id=99999,
                server_name="Node <DE-1> & 'Frankfurt' <b>test</b>",
                total_bytes=1024**4,
                profile_id=1,
            )

        self.mock_bot.send_message.assert_called_once()
        sent_text = self.mock_bot.send_message.call_args.kwargs["text"]
        # HTML special chars must be escaped with safe()
        self.assertIn("&lt;DE-1&gt;", sent_text)
        self.assertIn("&amp;", sent_text)
        self.assertIn("&lt;b&gt;test&lt;/b&gt;", sent_text)
        self.assertNotIn("<DE-1>", sent_text)

    async def test_admin_server_name_length_limit(self):
        """Server add name longer than 50 chars is rejected."""
        from bot.handlers.admin.servers.add_routes import process_add_server

        msg = MagicMock()
        msg.from_user = MagicMock(id=100)
        msg.text = "A" * 51
        msg.bot = self.mock_bot
        msg.chat.id = 100

        state = AsyncMock()
        state.get_data = AsyncMock(return_value={"step": "name"})
        session = AsyncMock()

        with patch("bot.handlers.admin.servers.add_routes.is_admin", return_value=True), \
             patch("bot.handlers.admin.servers.add_routes.render_hub") as mock_render:
            await process_add_server(msg, state, session)

            mock_render.assert_called_once()
            rendered_text = mock_render.call_args[0][2]
            self.assertIn("50", rendered_text)
            state.update_data.assert_not_called()

    async def test_admin_balance_reason_length_limit(self):
        """Balance adjustment reason longer than 100 chars is rejected."""
        from bot.handlers.admin.users.balance_routes import process_balance_reason

        msg = MagicMock()
        msg.from_user = MagicMock(id=100)
        msg.text = "R" * 101
        msg.bot = self.mock_bot
        msg.chat.id = 100
        msg.message_id = 55

        state = AsyncMock()
        state.get_data = AsyncMock(return_value={
            "target_telegram_id": 12345,
            "amount": 100,
            "action_type": "topup",
        })
        session = AsyncMock()

        with patch("bot.handlers.admin.users.balance_routes.is_admin", return_value=True), \
             patch("bot.handlers.admin.users.balance_routes.render_hub") as mock_render:
            await process_balance_reason(msg, state, session)

            mock_render.assert_called_once()
            rendered_text = mock_render.call_args[0][2]
            self.assertIn("100", rendered_text)
            state.update_data.assert_not_called()

    async def test_admin_user_search_length_limit(self):
        """User search query longer than 64 chars is rejected immediately."""
        from bot.handlers.admin.users.list_routes import process_search_user

        msg = MagicMock()
        msg.from_user = MagicMock(id=100)
        msg.text = "S" * 65
        msg.bot = self.mock_bot
        msg.chat.id = 100
        msg.message_id = 66

        state = AsyncMock()
        session = AsyncMock()

        with patch("bot.handlers.admin.users.list_routes.is_admin", return_value=True), \
             patch("bot.handlers.admin.users.list_routes.render_hub") as mock_render, \
             patch("database.repositories.users_repo.search_user_flexible") as mock_search:
            await process_search_user(msg, state, session)

            mock_render.assert_called_once()
            rendered_text = mock_render.call_args[0][2]
            self.assertIn("не найден", rendered_text)
            # search_user_flexible must not be called for overlong queries
            mock_search.assert_not_called()
            state.clear.assert_called_once()
