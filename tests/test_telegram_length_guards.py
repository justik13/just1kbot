import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.keyboards.device import get_device_keyboard
from bot.handlers.connection.device_view_routes import build_display_vpn_key, render_device_screen


class TestTelegramLengthGuards(unittest.IsolatedAsyncioTestCase):
    def test_copy_text_button_omitted_completely_from_device_keyboard(self):
        short_key = 'vpn://' + 'a' * 200
        kb = get_device_keyboard(profile_id=1, raw_config=short_key, config_ready=True)
        copy_buttons = [btn for row in kb.inline_keyboard for btn in row if getattr(btn, 'copy_text', None)]
        self.assertEqual(len(copy_buttons), 0)

        long_key = 'vpn://' + 'a' * 260
        kb2 = get_device_keyboard(profile_id=1, raw_config=long_key, config_ready=True)
        copy_buttons2 = [btn for row in kb2.inline_keyboard for btn in row if getattr(btn, 'copy_text', None)]
        self.assertEqual(len(copy_buttons2), 0)

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
            self.assertIn('Локация: <b>🇩🇪 Germany</b>', rendered_text)
            self.assertIn('Другой способ подключения', rendered_text)


    async def test_alt_connection_preserves_old_hub_if_guide_send_fails(self):
        from bot.handlers.connection.device_view_routes import alt_connection

        callback = MagicMock()
        callback.data = 'alt_connection:1'
        callback.message = MagicMock()
        callback.message.chat = MagicMock(id=12345)
        callback.message.message_id = 99
        callback.bot = MagicMock()
        callback.answer = AsyncMock()

        state = AsyncMock()
        session = AsyncMock()
        db_user = SimpleNamespace(id=10, telegram_id=12345)
        profile = SimpleNamespace(id=1, user_id=10, server_id=1, device_name='iPhone', raw_config='vpn://dummy', provisioning_status='active')
        server = SimpleNamespace(id=1, name='Germany')

        with patch('bot.handlers.connection.device_view_routes.SubscriptionService.check_access', new=AsyncMock(return_value=True)), \
             patch('bot.handlers.connection.device_view_routes.get_profile_by_id', new=AsyncMock(return_value=profile)), \
             patch('bot.handlers.connection.device_view_routes.can_show_config_actions', return_value=True), \
             patch('bot.handlers.connection.device_view_routes.decode_vpn_uri_to_json', return_value={'containers': [{'awg': {'last_config': '{}'}}]}), \
             patch('bot.handlers.connection.device_view_routes.get_server_by_id', new=AsyncMock(return_value=server)), \
             patch('bot.handlers.connection.device_view_routes.customize_vpn_config_dict', return_value={}), \
             patch('bot.handlers.connection.device_view_routes.build_vpn_file_from_dict', return_value='vpn_data'), \
             patch('bot.handlers.connection.device_view_routes.build_conf_file_from_dict', return_value='conf_data'), \
             patch('bot.handlers.connection.device_view_routes.can_show_amnezia_bridge', return_value=False), \
             patch('bot.handlers.connection.device_view_routes.get_hub_ids', new=AsyncMock(return_value=[99, 100])), \
             patch('bot.handlers.connection.device_view_routes.append_hub_document', new=AsyncMock()), \
             patch('bot.handlers.connection.device_view_routes.append_hub_message', new=AsyncMock(side_effect=RuntimeError('Network dropped'))), \
             patch('bot.handlers.connection.device_view_routes.delete_hub_ids', new=AsyncMock()) as mock_delete:

            await alt_connection(callback, state, session, db_user)

            # Invariant: If append_hub_message fails, old_hub_ids MUST NOT be deleted!
            self.assertFalse(mock_delete.called)

    def test_build_conf_fallback_omits_empty_i_parameters(self):
        from utils.vpn_parser import _build_conf_fallback
        data = {'hostName': '1.2.3.4', 'port': 51820}
        last_config = {
            'client_priv_key': 'priv',
            'server_pub_key': 'pub',
            'client_ip': '10.0.0.2',
            'Jc': 4, 'Jmin': 10, 'Jmax': 50,
            'S1': 15, 'S2': 20, 'S3': 5, 'S4': 10,
            'H1': '100', 'H2': '200', 'H3': '300', 'H4': '400',
            'I1': '<r 2>',
            'I2': '',  # Empty
            'I3': None, # None
            'I4': '   ', # Whitespace
            'I5': '',
        }
        conf = _build_conf_fallback(data, last_config)
        self.assertIsNotNone(conf)
        self.assertIn('I1 = <r 2>', conf)
        self.assertNotIn('I2 =', conf)
        self.assertNotIn('I3 =', conf)
        self.assertNotIn('I4 =', conf)
        self.assertNotIn('I5 =', conf)


if __name__ == '__main__':
    unittest.main()
