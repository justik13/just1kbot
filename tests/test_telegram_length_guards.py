import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import CopyTextButton, DisabledButton, InlineKeyboardButton, InlineKeyboardMarkup
from bot.handlers.connection.device_view_routes import build_display_vpn_key, render_device_screen
from utils.telegram import render_hub, sanitize_reply_markup


class TestTelegramLengthGuards(unittest.IsolatedAsyncioTestCase):
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
             patch('bot.handlers.connection.device_view_routes.render_hub', new=AsyncMock()) as mock_hub:

            await render_device_screen(bot, chat_id=12345, profile=profile, user=user, session=session)

            self.assertTrue(mock_hub.called)
            rendered_text = mock_hub.call_args[0][2]
            # Must not exceed Telegram 4096 character limit
            self.assertLessEqual(len(rendered_text), 4096)
            self.assertIn('Локация: <b>🇩🇪 Germany</b>', rendered_text)
            self.assertIn('.conf', rendered_text)


    async def test_alt_connection_decommissioned_answers_alert(self):
        from bot.handlers.connection.device_view_routes import alt_connection
        from bot import texts

        callback = MagicMock()
        callback.data = 'alt_connection:1'
        callback.message = MagicMock()
        callback.answer = AsyncMock()

        state = AsyncMock()
        session = AsyncMock()
        db_user = SimpleNamespace(id=10, telegram_id=12345)

        with patch('bot.handlers.connection.device_view_routes._render_connections', new=AsyncMock()) as mock_render:
            await alt_connection(callback, state, session, db_user)
            callback.answer.assert_called_once_with(texts.ALERT_AMNEZIA_DECOMMISSIONED, show_alert=True)
            mock_render.assert_called_once()

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


    async def test_manage_device_decommissioned_answers_alert(self):
        from bot.handlers.connection.device_view_routes import manage_device
        from bot import texts

        callback = MagicMock()
        callback.data = 'manage_device:1'
        callback.message = MagicMock()
        callback.answer = AsyncMock()

        state = AsyncMock()
        session = AsyncMock()
        db_user = SimpleNamespace(id=10, telegram_id=12345)

        with patch('bot.handlers.connection.device_view_routes._render_connections', new=AsyncMock()) as mock_render:
            await manage_device(callback, state, session, db_user)
            callback.answer.assert_called_once_with(texts.ALERT_AMNEZIA_DECOMMISSIONED, show_alert=True)
            mock_render.assert_called_once()

    async def test_render_connections_financial_hold_shows_dispute_notice(self):
        from bot.handlers.connection.common import _render_connections
        from bot import texts

        target = MagicMock()
        target.bot = MagicMock()
        target.chat.id = 12345
        session = AsyncMock()
        user = SimpleNamespace(id=10, telegram_id=12345, financial_hold=True, subscription_end=None)

        with patch("services.subscription.SubscriptionService.check_access", new=AsyncMock(return_value=False)), \
             patch("bot.handlers.connection.common.render_hub", new=AsyncMock()) as mock_render_hub:
            await _render_connections(target, user, session)
            mock_render_hub.assert_called_once()
            args = mock_render_hub.call_args[0]
            self.assertEqual(args[2], texts.PAYMENT_DISPUTE_BLOCKED_NOTICE)

    async def test_render_connections_expired_shows_read_only(self):
        from bot.handlers.connection.common import _render_connections
        from datetime import datetime, timezone, timedelta

        target = MagicMock()
        target.bot = MagicMock()
        target.chat.id = 12345
        session = AsyncMock()
        expired_date = datetime.now(timezone.utc) - timedelta(days=1)
        user = SimpleNamespace(id=10, telegram_id=12345, financial_hold=False, subscription_end=expired_date)

        with patch("services.subscription.SubscriptionService.check_access", new=AsyncMock(return_value=False)), \
             patch("bot.handlers.connection.common._build_connections_screen", new=AsyncMock(return_value=("EXPIRED_TEXT", MagicMock()))) as mock_build, \
             patch("bot.handlers.connection.common.render_hub", new=AsyncMock()) as mock_render_hub:
            await _render_connections(target, user, session)
            mock_build.assert_called_once_with(user, session, read_only=True)
            mock_render_hub.assert_called_once()


class TestTelegramBotApi103KeyboardAudit(unittest.IsolatedAsyncioTestCase):
    """Universal audit of all keyboards and buttons across bot against Telegram Bot API 10.3 constraints."""

    def test_sanitize_reply_markup_preserves_valid(self):
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Normal", callback_data="btn_ok")],
                [InlineKeyboardButton(text="Link", copy_text=CopyTextButton(text="https://short.link"))],
            ]
        )
        res = sanitize_reply_markup(kb)
        self.assertIs(res, kb)

    def test_sanitize_reply_markup_neutralizes_awg_key(self):
        awg_key = "vpn://" + "A" * 1200
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="AWG Key", copy_text=CopyTextButton(text=awg_key))]]
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

    async def test_render_hub_applies_sanitizer_end_to_end(self):
        bot = MagicMock()
        bot.edit_message_text = AsyncMock()
        awg_key = "vpn://" + "A" * 1200
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Copy Key", copy_text=CopyTextButton(text=awg_key))]]
        )
        with patch("utils.telegram._dispatch_load_hub_ids_from_db", new=AsyncMock(return_value=[50])), \
             patch("utils.telegram._dispatch_store_hub_id_in_db", new=AsyncMock()), \
             patch("utils.telegram._dispatch_delete_hub_messages", new=AsyncMock(return_value=[])):
            await render_hub(bot, chat_id=999, text="Test message", reply_markup=kb)

        sent_kb = bot.edit_message_text.call_args[1]["reply_markup"]
        self.assertIsNone(sent_kb.inline_keyboard[0][0].copy_text)
        self.assertEqual(sent_kb.inline_keyboard[0][0].callback_data, "alert:copy_too_long")

    async def test_alert_copy_too_long_callback_shows_alert(self):
        from bot import texts
        from bot.handlers.start import alert_copy_too_long_callback

        callback = MagicMock()
        callback.answer = AsyncMock()

        await alert_copy_too_long_callback(callback)

        callback.answer.assert_awaited_once_with(
            texts.ALERT_COPY_TEXT_TOO_LONG,
            show_alert=True,
        )

    def test_universal_keyboards_audit_bot_api_10_3(self):
        """Universal audit of all keyboards across bot against Telegram Bot API 10.3 constraints."""
        from bot.keyboards.admin.broadcast import (
            get_broadcast_audience_keyboard,
            get_broadcast_close_keyboard,
            get_broadcast_launch_keyboard,
            get_broadcast_result_keyboard,
        )
        from bot.keyboards.admin.dashboard import (
            get_admin_cat_finance_keyboard,
            get_admin_cat_infra_keyboard,
            get_admin_cat_std_sub_keyboard,
            get_admin_cat_system_keyboard,
            get_admin_cat_users_keyboard,
            get_admin_cat_wi_sub_keyboard,
            get_admin_menu,
            get_audit_keyboard,
            get_maintenance_confirm_keyboard,
        )
        from bot.keyboards.admin.servers import (
            get_admin_server_card_keyboard,
            get_admin_server_peers_keyboard,
            get_admin_server_relays_keyboard,
            get_server_delete_confirm_keyboard,
        )
        from bot.keyboards.admin.tariffs import get_admin_tariff_card_keyboard
        from bot.keyboards.admin.users import (
            get_admin_awg_subscription_keyboard,
            get_admin_ban_reasons_keyboard,
            get_admin_confirm_action_keyboard,
            get_admin_extend_days_new_keyboard,
            get_admin_grant_days_keyboard,
            get_admin_user_balance_keyboard,
            get_admin_user_card_keyboard,
            get_admin_user_devices_keyboard,
            get_admin_wi_device_limit_keyboard,
            get_admin_wi_devices_keyboard,
            get_admin_wi_extend_days_keyboard,
            get_admin_wi_quota_keyboard,
            get_admin_wi_subscription_keyboard,
            get_admin_wi_traffic_add_keyboard,
        )
        from bot.keyboards.common import get_back_button, get_hub_keyboard
        from bot.keyboards.device import (
            get_alt_connection_keyboard,
            get_device_delete_confirm_keyboard,
            get_device_keyboard,
            get_device_migrate_confirm_keyboard,
        )
        from bot.keyboards.notifications import (
            get_devices_deleted_keyboard,
            get_node_monitor_alert_keyboard,
            get_post_expiry_keyboard,
            get_pre_expiry_keyboard,
            get_referral_bonus_keyboard,
            get_referral_onboarding_keyboard,
            get_traffic_alert_keyboard,
        )
        from bot.keyboards.payment import (
            get_balance_amounts_keyboard,
            get_balance_history_keyboard,
            get_balance_keyboard,
            get_order_checkout_keyboard,
            get_order_invoice_keyboard,
            get_payment_success_keyboard,
            get_renew_keyboard,
            get_tariff_duration_keyboard,
            get_tariff_showcase_keyboard,
            get_topup_credit_keyboard,
        )
        from bot.keyboards.user import (
            get_history_keyboard,
            get_referral_keyboard,
            get_referral_leaderboard_keyboard,
            get_referrals_list_keyboard,
        )

        awg_key = "vpn://" + "A" * 1500

        keyboards = [
            # User keyboards
            get_referral_keyboard("https://t.me/just1kbot?start=ref123", count=5),
            get_referral_keyboard(awg_key, count=0),
            get_referral_keyboard("", count=0),
            get_history_keyboard(),
            get_referral_leaderboard_keyboard(),
            get_referrals_list_keyboard(1, 1),
            # Admin user keyboards (testing normal, AWG key, empty sub_urls)
            get_admin_awg_subscription_keyboard(12345, True, vless_sub_url="https://sub.url"),
            get_admin_awg_subscription_keyboard(12345, True, vless_sub_url=awg_key),
            get_admin_awg_subscription_keyboard(12345, True, vless_sub_url=""),
            get_admin_awg_subscription_keyboard(12345, False),
            get_admin_wi_subscription_keyboard(12345, True, True, False, sub_url="https://sub.url"),
            get_admin_wi_subscription_keyboard(12345, True, True, False, sub_url=awg_key),
            get_admin_wi_subscription_keyboard(12345, True, True, False, sub_url=""),
            get_admin_wi_subscription_keyboard(12345, False),
            get_admin_wi_devices_keyboard(12345, False, sub_url="https://sub.url"),
            get_admin_wi_devices_keyboard(12345, False, sub_url=awg_key),
            get_admin_user_devices_keyboard(12345, [], vless_sub_url="https://sub.url"),
            get_admin_user_devices_keyboard(12345, [], vless_sub_url=awg_key),
            get_admin_user_card_keyboard(12345, is_banned=False),
            get_admin_user_balance_keyboard(12345),
            get_admin_ban_reasons_keyboard(12345),
            get_admin_confirm_action_keyboard("ban:123", "admin_user_card:123"),
            get_admin_extend_days_new_keyboard(12345),
            get_admin_grant_days_keyboard(12345, 1),
            get_admin_wi_device_limit_keyboard(12345),
            get_admin_wi_extend_days_keyboard(12345),
            get_admin_wi_quota_keyboard(12345),
            get_admin_wi_traffic_add_keyboard(12345),
            # Admin dashboard keyboards
            get_admin_menu(),
            get_audit_keyboard(),
            get_maintenance_confirm_keyboard(),
            get_admin_cat_users_keyboard(),
            get_admin_cat_finance_keyboard(),
            get_admin_cat_infra_keyboard(),
            get_admin_cat_system_keyboard(),
            get_admin_cat_std_sub_keyboard(),
            get_admin_cat_wi_sub_keyboard(),
            # Admin broadcast keyboards
            get_broadcast_audience_keyboard(),
            get_broadcast_close_keyboard(),
            get_broadcast_launch_keyboard(100),
            get_broadcast_result_keyboard(),
            # Admin servers keyboards
            get_admin_server_card_keyboard(1, False),
            get_admin_server_peers_keyboard(1, 1, 1, []),
            get_admin_server_relays_keyboard(1),
            get_server_delete_confirm_keyboard(1),
            get_admin_tariff_card_keyboard(1, is_active=True),
            # Common & device keyboards
            get_back_button("back"),
            get_hub_keyboard(),
            get_alt_connection_keyboard(1),
            get_device_delete_confirm_keyboard(1),
            get_device_keyboard(1, config_ready=True, show_delete=True),
            get_device_migrate_confirm_keyboard(1, 2),
            # Notifications keyboards
            get_devices_deleted_keyboard(),
            get_node_monitor_alert_keyboard(1, False).as_markup(),
            get_post_expiry_keyboard(),
            get_pre_expiry_keyboard(),
            get_referral_bonus_keyboard(),
            get_referral_onboarding_keyboard(),
            get_traffic_alert_keyboard(12345),
            # Payment keyboards
            get_balance_amounts_keyboard([100, 200, 500]),
            get_balance_history_keyboard(1, 1),
            get_balance_keyboard(),
            get_order_checkout_keyboard(1, 100, True, "back"),
            get_order_invoice_keyboard("https://pay.url", "ord123", 100, "https://t.me/support"),
            get_payment_success_keyboard(),
            get_renew_keyboard([]),
            get_tariff_duration_keyboard([]),
            get_tariff_showcase_keyboard({}),
            get_topup_credit_keyboard(),
        ]

        for idx, kb in enumerate(keyboards):
            for row in kb.inline_keyboard:
                self.assertLessEqual(len(row), 8, f"KB #{idx}: row has {len(row)} buttons (max 8)")
                for btn in row:
                    self.assertTrue(btn.text and btn.text.strip(), f"KB #{idx}: empty button text")
                    if btn.copy_text is not None:
                        self.assertGreaterEqual(len(btn.copy_text.text), 1, f"KB #{idx}: copy_text empty")
                        self.assertLessEqual(
                            len(btn.copy_text.text),
                            256,
                            f"KB #{idx}: copy_text length {len(btn.copy_text.text)} exceeds 256",
                        )
                    if btn.callback_data is not None:
                        cb_bytes = len(btn.callback_data.encode("utf-8"))
                        self.assertLessEqual(cb_bytes, 64, f"KB #{idx}: callback_data {cb_bytes} bytes exceeds 64")

            sanitized = sanitize_reply_markup(kb)
            for row in sanitized.inline_keyboard:
                self.assertLessEqual(len(row), 8)
                for btn in row:
                    self.assertTrue(btn.text and btn.text.strip())
                    if btn.copy_text is not None:
                        self.assertGreaterEqual(len(btn.copy_text.text), 1)
                        self.assertLessEqual(len(btn.copy_text.text), 256)
                    if btn.callback_data is not None:
                        self.assertLessEqual(len(btn.callback_data.encode("utf-8")), 64)


if __name__ == '__main__':
    unittest.main()

