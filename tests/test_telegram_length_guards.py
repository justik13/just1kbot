import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.keyboards.device import get_device_keyboard
from bot.handlers.connection.device_view_routes import build_display_vpn_key, render_device_screen


class TestTelegramLengthGuards(unittest.IsolatedAsyncioTestCase):
    def test_copy_text_button_included_when_length_under_256(self):
        short_key = 'vpn://' + 'a' * 200
        kb = get_device_keyboard(profile_id=1, raw_config=short_key, config_ready=True)
        copy_buttons = [btn for row in kb.inline_keyboard for btn in row if getattr(btn, 'copy_text', None)]
        self.assertEqual(len(copy_buttons), 1)
        self.assertEqual(copy_buttons[0].copy_text.text, short_key)

    def test_copy_text_button_omitted_when_length_exceeds_256(self):
        long_key = 'vpn://' + 'a' * 260
        kb = get_device_keyboard(profile_id=1, raw_config=long_key, config_ready=True)
        copy_buttons = [btn for row in kb.inline_keyboard for btn in row if getattr(btn, 'copy_text', None)]
        # Must be omitted to prevent TelegramBadRequest from Bot API 256-character limit
        self.assertEqual(len(copy_buttons), 0)

    def test_build_display_vpn_key_standardized(self):
        profile = SimpleNamespace(id=1, device_name='iPhone #1')
        server = SimpleNamespace(id=1, name='Germany')
        key = 'vpn://testkey'
        
        with patch('bot.handlers.connection.device_view_routes.customize_vpn_uri') as mock_cust:
            mock_cust.return_value = 'vpn://customized'
            res = build_display_vpn_key(key, profile, server)
            self.assertEqual(res, 'vpn://customized')
            mock_cust.assert_called_once_with(
                key,
                description='Germany #1',
                dns1='8.8.8.8',
                dns2='8.8.4.4',
                mtu='1280',
            )

    async def test_render_device_screen_truncates_huge_key_block_to_prevent_telegram_overflow(self):
        bot = MagicMock()
        session = AsyncMock()
        profile = SimpleNamespace(
            id=1,
            server_id=1,
            device_name='MyDevice',
            provisioning_status='active',
            traffic_down=0,
            traffic_up=0,
            last_connected=None,
            raw_config='vpn://' + 'x' * 5000,
        )
        user = SimpleNamespace(id=10, telegram_id=12345)
        server = SimpleNamespace(id=1, country_flag='🇩🇪', name='Germany', protocol='amneziawg2')

        with patch('bot.handlers.connection.device_view_routes.get_server_by_id', new=AsyncMock(return_value=server)), \
             patch('bot.handlers.connection.device_view_routes.SubscriptionService.check_access', new=AsyncMock(return_value=True)), \
             patch('bot.handlers.connection.device_view_routes.can_show_config_actions', return_value=True), \
             patch('bot.handlers.connection.device_view_routes.can_show_delete_action', return_value=True), \
             patch('bot.handlers.connection.device_view_routes.can_show_amnezia_bridge', return_value=False), \
             patch('bot.handlers.connection.device_view_routes.render_hub', new=AsyncMock()) as mock_hub:

            await render_device_screen(bot, chat_id=12345, profile=profile, user=user, session=session)

            self.assertTrue(mock_hub.called)
            rendered_text = mock_hub.call_args[0][2]
            # Must not exceed Telegram 4096 character limit
            self.assertLessEqual(len(rendered_text), 4096)
            self.assertIn('Другой способ подключения', rendered_text)


if __name__ == '__main__':
    unittest.main()
