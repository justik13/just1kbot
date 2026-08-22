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
            mid = await append_hub_message(
                bot,
                chat_id=12345,
                text='<b>Instruction</b> <invalid_tag>',
                reply_markup=kb,
            )

            self.assertEqual(bot.send_message.call_count, 2)
            second_call = bot.send_message.call_args_list[1]
            self.assertIsNone(second_call.kwargs.get('parse_mode'))
            self.assertEqual(second_call.kwargs.get('text'), 'Instruction ')
            self.assertEqual(mid, 2001)


if __name__ == '__main__':
    unittest.main()
