import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.keyboard import InlineKeyboardBuilder

from utils.telegram import render_hub, append_hub_message


class TestRenderHubEffects(unittest.IsolatedAsyncioTestCase):
    async def test_render_hub_with_effect_bypasses_edit_and_sends_new_message(self):
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MagicMock(message_id=999))
        bot.edit_message_text = AsyncMock()
        bot.delete_message = AsyncMock()

        kb = InlineKeyboardBuilder().as_markup()

        with patch('utils.telegram._load_hub_ids_from_db', new=AsyncMock(return_value=[100])), \
             patch('utils.telegram._store_hub_id_in_db', new=AsyncMock()), \
             patch('utils.telegram._delete_hub_messages', new=AsyncMock()):

            mid = await render_hub(
                bot,
                chat_id=12345,
                text='Topup success',
                reply_markup=kb,
                message_effect_id='5046509860389126442',
            )

            self.assertFalse(bot.edit_message_text.called)
            self.assertTrue(bot.send_message.called)
            self.assertEqual(
                bot.send_message.call_args.kwargs.get('message_effect_id'),
                '5046509860389126442',
            )
            self.assertEqual(mid, 999)

    async def test_render_hub_without_effect_edits_existing_message(self):
        bot = MagicMock()
        bot.send_message = AsyncMock()
        bot.edit_message_text = AsyncMock()

        kb = InlineKeyboardBuilder().as_markup()

        with patch('utils.telegram._load_hub_ids_from_db', new=AsyncMock(return_value=[100])), \
             patch('utils.telegram._store_hub_id_in_db', new=AsyncMock()), \
             patch('utils.telegram._delete_hub_messages', new=AsyncMock()):

            mid = await render_hub(
                bot,
                chat_id=12345,
                text='Normal screen',
                reply_markup=kb,
            )

            self.assertTrue(bot.edit_message_text.called)
            self.assertFalse(bot.send_message.called)
            self.assertEqual(mid, 100)

    async def test_render_hub_cleans_and_sends_new_when_previous_message_had_effect(self):
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MagicMock(message_id=200))
        bot.edit_message_text = AsyncMock()

        kb = InlineKeyboardBuilder().as_markup()

        # Step 1: Render screen with effect -> sends message 100
        bot.send_message.return_value = MagicMock(message_id=100)
        with patch('utils.telegram._load_hub_ids_from_db', new=AsyncMock(return_value=[])), \
             patch('utils.telegram._store_hub_id_in_db', new=AsyncMock()), \
             patch('utils.telegram._delete_hub_messages', new=AsyncMock()) as mock_delete:

            mid1 = await render_hub(
                bot,
                chat_id=12345,
                text='Celebration screen',
                reply_markup=kb,
                message_effect_id='5046509860389126442',
            )
            self.assertEqual(mid1, 100)
            self.assertTrue(bot.send_message.called)

        # Step 2: User clicks navigation button to normal screen (no effect)
        # Should NOT edit message 100 (which has sticky effect), but delete 100 and send new clean message 200!
        bot.send_message.reset_mock()
        bot.send_message.return_value = MagicMock(message_id=200)
        bot.edit_message_text.reset_mock()

        with patch('utils.telegram._load_hub_ids_from_db', new=AsyncMock(return_value=[100])), \
             patch('utils.telegram._store_hub_id_in_db', new=AsyncMock()), \
             patch('utils.telegram._delete_hub_messages', new=AsyncMock()) as mock_delete:

            mid2 = await render_hub(
                bot,
                chat_id=12345,
                text='Clean menu screen',
                reply_markup=kb,
            )

            self.assertFalse(bot.edit_message_text.called)
            self.assertTrue(bot.send_message.called)
            self.assertIsNone(bot.send_message.call_args.kwargs.get('message_effect_id'))
            mock_delete.assert_called_once_with(bot, 12345, [100])
            self.assertEqual(mid2, 200)

    async def test_render_hub_effect_fallback_on_api_error(self):
        bot = MagicMock()
        bot.send_message = AsyncMock(
            side_effect=[
                TelegramBadRequest(method=MagicMock(), message='Bad Request: message effect invalid'),
                MagicMock(message_id=1001),
            ]
        )

        kb = InlineKeyboardBuilder().as_markup()

        with patch('utils.telegram._load_hub_ids_from_db', new=AsyncMock(return_value=[])), \
             patch('utils.telegram._store_hub_id_in_db', new=AsyncMock()), \
             patch('utils.telegram._delete_hub_messages', new=AsyncMock()):

            mid = await render_hub(
                bot,
                chat_id=12345,
                text='Topup success',
                reply_markup=kb,
                message_effect_id='5046509860389126442',
            )

            self.assertEqual(bot.send_message.call_count, 2)
            self.assertNotIn('message_effect_id', bot.send_message.call_args_list[1].kwargs)
            self.assertEqual(mid, 1001)

    async def test_render_hub_effect_and_parse_error_cascade_fallback(self):
        bot = MagicMock()
        bot.send_message = AsyncMock(
            side_effect=[
                TelegramBadRequest(method=MagicMock(), message='Bad Request: message effect invalid'),
                TelegramBadRequest(method=MagicMock(), message='Bad Request: can not parse entities in message'),
                MagicMock(message_id=1002),
            ]
        )

        kb = InlineKeyboardBuilder().as_markup()

        with patch('utils.telegram._load_hub_ids_from_db', new=AsyncMock(return_value=[])), \
             patch('utils.telegram._store_hub_id_in_db', new=AsyncMock()), \
             patch('utils.telegram._delete_hub_messages', new=AsyncMock()):

            mid = await render_hub(
                bot,
                chat_id=12345,
                text='<b>Success</b> <unclosed_tag>',
                reply_markup=kb,
                message_effect_id='5046509860389126442',
            )

            self.assertEqual(bot.send_message.call_count, 3)
            third_call = bot.send_message.call_args_list[2]
            self.assertIsNone(third_call.kwargs.get('parse_mode'))
            self.assertEqual(third_call.kwargs.get('text'), 'Success ')
            self.assertEqual(mid, 1002)

    async def test_append_hub_message_fallback_on_parse_error(self):
        bot = MagicMock()
        bot.send_message = AsyncMock(
            side_effect=[
                TelegramBadRequest(method=MagicMock(), message='Bad Request: can not parse entities in message'),
                MagicMock(message_id=2001),
            ]
        )

        kb = InlineKeyboardBuilder().as_markup()

        with patch('utils.telegram._store_hub_id_in_db', new=AsyncMock()):
            await append_hub_message(
                bot,
                chat_id=12345,
                text='<b>Instruction</b> <invalid_tag>',
                reply_markup=kb,
            )

            self.assertEqual(bot.send_message.call_count, 2)
            second_call = bot.send_message.call_args_list[1]
            self.assertIsNone(second_call.kwargs.get('parse_mode'))
            self.assertEqual(second_call.kwargs.get('text'), 'Instruction ')
    async def test_copy_text_button_length_guard(self):
        from bot.keyboards.user import get_referral_keyboard

        # Standard referral link <= 256 chars has CopyTextButton
        short_link = "https://t.me/just1kbot?start=ref123"
        kb = get_referral_keyboard(short_link)
        has_copy = any(
            btn.copy_text is not None
            for row in kb.inline_keyboard
            for btn in row
        )
        self.assertTrue(has_copy)

        # Oversized referral link > 256 chars omits CopyTextButton to avoid TelegramBadRequest
        long_link = "https://t.me/just1kbot?start=" + "x" * 300
        kb_long = get_referral_keyboard(long_link)
        has_copy_long = any(
            btn.copy_text is not None
            for row in kb_long.inline_keyboard
            for btn in row
        )
        self.assertFalse(has_copy_long)

if __name__ == '__main__':
    unittest.main()
