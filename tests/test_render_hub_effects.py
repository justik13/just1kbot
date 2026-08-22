import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.keyboard import InlineKeyboardBuilder

from utils.telegram import render_hub


class TestRenderHubEffects(unittest.IsolatedAsyncioTestCase):
    async def test_render_hub_with_effect_bypasses_edit_and_sends_new_message(self):
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MagicMock(message_id=999))
        bot.edit_message_text = AsyncMock()
        bot.delete_message = AsyncMock()

        kb = InlineKeyboardBuilder().as_markup()

        with patch('utils.telegram._load_hub_ids_from_db', new=AsyncMock(return_value=[100])),              patch('utils.telegram._store_hub_id_in_db', new=AsyncMock()),              patch('utils.telegram._delete_hub_messages', new=AsyncMock()):
            
            mid = await render_hub(
                bot,
                chat_id=12345,
                text='🎉 Успешная оплата!',
                reply_markup=kb,
                message_effect_id='5046509860389126442',
            )

            # Assert edit was NOT called because message_effect_id requires a new message
            self.assertFalse(bot.edit_message_text.called)
            # Assert send_message was called with message_effect_id
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

        with patch('utils.telegram._load_hub_ids_from_db', new=AsyncMock(return_value=[100])),              patch('utils.telegram._store_hub_id_in_db', new=AsyncMock()),              patch('utils.telegram._delete_hub_messages', new=AsyncMock()):
            
            mid = await render_hub(
                bot,
                chat_id=12345,
                text='Обычный экран',
                reply_markup=kb,
            )

            # Assert edit was called for normal navigation
            self.assertTrue(bot.edit_message_text.called)
            self.assertFalse(bot.send_message.called)
            self.assertEqual(mid, 100)

    async def test_render_hub_effect_fallback_on_api_error(self):
        bot = MagicMock()
        # First send_message fails with effect bad request, second succeeds without effect
        bot.send_message = AsyncMock(
            side_effect=[
                TelegramBadRequest(method=MagicMock(), message='Bad Request: message effect invalid'),
                MagicMock(message_id=1001),
            ]
        )

        kb = InlineKeyboardBuilder().as_markup()

        with patch('utils.telegram._load_hub_ids_from_db', new=AsyncMock(return_value=[])),              patch('utils.telegram._store_hub_id_in_db', new=AsyncMock()),              patch('utils.telegram._delete_hub_messages', new=AsyncMock()):
            
            mid = await render_hub(
                bot,
                chat_id=12345,
                text='🎉 Успешная оплата!',
                reply_markup=kb,
                message_effect_id='5046509860389126442',
            )

            self.assertEqual(bot.send_message.call_count, 2)
            # Second call dropped message_effect_id
            self.assertNotIn('message_effect_id', bot.send_message.call_args_list[1].kwargs)
            self.assertEqual(mid, 1001)


if __name__ == '__main__':
    unittest.main()